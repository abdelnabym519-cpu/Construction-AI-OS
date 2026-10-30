# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Unit tests for the tenant-scope resolution policy.

These pin the *resolution policy* of :mod:`app.core.tenant_scope` so the
multi-tenant hardening that builds on it has a stable, documented contract:

* the tenant is the explicit ``tenant_id`` claim, else ``None``. There is no
  fall-through to ``sub``; the note below says what that replaced and why;
* the resolver is total and never raises (anonymous / malformed payloads collapse
  to ``None`` rather than blowing up a request);
* an authenticated access token that carries no tenant is refused at the door
  (:func:`app.dependencies.reject_token_without_a_tenant`) rather than being scoped
  to its holder's own id, while a refresh token is left alone because it is the
  recovery path;
* :func:`tenant_scope_owner` reproduces the established ``None if admin else
  tenant`` filter and fails *closed* (a non-admin or unknown caller stays scoped,
  never widened to all rows).

What changed, and why the expectations below changed with it
-----------------------------------------------------------
Four tests in this file used to assert that a payload carrying only ``sub``
resolved to that user id. That was the documented single-tenant contract: with no
tenancy entity in the schema the caller's own id was the only value a ``tenant_id``
column ever held, and migration ``v231_contact_tenant_id`` said so in its
docstring. ADR 005 replaces it. The fall-through is what made the
``tenant_is_user`` readiness blocker permanent, because an install could be moved
onto organizations perfectly and the next claimless token would put a person
straight back into a tenant column. The tests were not weakened to accommodate
that: each one now asserts the opposite behaviour, and the person-as-tenant reading
is pinned as *absent* by :func:`test_a_person_is_not_a_tenant` so that a future
"helpful" fallback fails here first.

The pure resolver tests run everywhere (the module imports no DB). The admin-bypass
tests stub :func:`app.core.tenant_scope._is_admin` so they stay pure too: they
assert the *branching*, not the DB lookup, which is exercised by the integration
suite (``tests/integration/test_tenant_isolation.py``).
"""

from __future__ import annotations

import pytest

from app.core.tenant_scope import (
    SUBJECT_CLAIM,
    TENANT_CLAIM,
    resolve_tenant_id,
    tenant_scope_owner,
)

# ── resolve_tenant_id: pure resolution policy ───────────────────────────────


def test_none_payload_resolves_to_none() -> None:
    """An anonymous caller (no payload) must yield None, not raise."""
    assert resolve_tenant_id(None) is None


def test_empty_payload_resolves_to_none() -> None:
    """A payload with neither claim resolves to None."""
    assert resolve_tenant_id({}) is None


def test_a_person_is_not_a_tenant() -> None:
    """A payload carrying only ``sub`` has no tenant.

    This is the assertion that replaced ``sub``-as-tenant. A caller's user id is
    who they are, not which company's data they may see, and the two coincide only
    in an install with one person in it. Returning ``None`` here is what makes the
    door's refusal reachable at all: a resolver that answered ``sub`` would never
    let :func:`app.dependencies.reject_token_without_a_tenant` fire, and the
    ``tenant_is_user`` readiness blocker would stay raised forever however
    carefully the rows were migrated.
    """
    assert resolve_tenant_id({SUBJECT_CLAIM: "user-123"}) is None


def test_tenant_claim_is_the_only_source() -> None:
    """The claim answers, and a ``sub`` sitting beside it changes nothing."""
    payload = {SUBJECT_CLAIM: "user-123", TENANT_CLAIM: "org-9"}
    assert resolve_tenant_id(payload) == "org-9"


def test_blank_tenant_claim_resolves_to_none() -> None:
    """A present-but-blank tenant claim is not a tenant.

    It used to fall through to ``sub``, which made a whitespace claim
    indistinguishable from a real one further down the stack. Blank means unknown,
    and unknown is refused at the door rather than guessed at here.
    """
    payload = {SUBJECT_CLAIM: "user-123", TENANT_CLAIM: "   "}
    assert resolve_tenant_id(payload) is None


def test_blank_sub_resolves_to_none() -> None:
    """A whitespace-only sub is not a usable tenant id."""
    assert resolve_tenant_id({SUBJECT_CLAIM: "   "}) is None


def test_non_string_claims_are_coerced() -> None:
    """Numeric claims (some IdPs emit ints) are coerced to str."""
    assert resolve_tenant_id({TENANT_CLAIM: 7}) == "7"
    assert resolve_tenant_id({TENANT_CLAIM: 7, SUBJECT_CLAIM: 42}) == "7"
    # A numeric ``sub`` on its own is still no tenant, coerced or not.
    assert resolve_tenant_id({SUBJECT_CLAIM: 42}) is None


def test_tenant_claim_surrounding_whitespace_is_stripped() -> None:
    """A real tenant id with stray whitespace is trimmed, not rejected."""
    assert resolve_tenant_id({TENANT_CLAIM: "  org-9  "}) == "org-9"


def test_zero_like_string_tenant_is_preserved() -> None:
    """A falsy-looking but non-empty id ('0') is a valid tenant, not None."""
    assert resolve_tenant_id({TENANT_CLAIM: "0"}) == "0"


# ── reject_token_without_a_tenant: the door, not the resolver ────────────────


def _door():
    """Imported lazily: ``app.dependencies`` builds an engine at module load."""
    from app.dependencies import reject_token_without_a_tenant

    return reject_token_without_a_tenant


def test_an_access_token_without_a_tenant_is_refused() -> None:
    """The credential is valid and still not acceptable, which is the point."""
    from fastapi import HTTPException

    try:
        reject = _door()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"app.dependencies not importable here (no DB engine): {exc}")

    with pytest.raises(HTTPException) as refused:
        reject({"sub": "user-123", "type": "access"})
    assert refused.value.status_code == 401
    assert refused.value.headers == {"WWW-Authenticate": "Bearer"}
    # The refusal has to be distinguishable from a bad signature, or a client
    # cannot tell "sign in again" from "your clock is wrong".
    assert "no tenant" in str(refused.value.detail)


def test_a_legacy_token_with_no_type_claim_is_refused_too() -> None:
    """``decode_access_token`` treats a missing ``type`` as ``access``, so one arrives here."""
    from fastapi import HTTPException

    try:
        reject = _door()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"app.dependencies not importable here (no DB engine): {exc}")

    with pytest.raises(HTTPException):
        reject({"sub": "user-123"})


def test_a_token_that_names_an_organization_passes() -> None:
    try:
        reject = _door()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"app.dependencies not importable here (no DB engine): {exc}")

    assert reject({"sub": "user-123", "type": "access", "tenant_id": "org-9"}) is None


def test_a_refresh_token_is_never_refused_for_having_no_tenant() -> None:
    """Refusing the refresh path would remove the recovery the refusal points at.

    A refresh token carries no tenant claim by design: it is not a scoping
    credential, and the flow that redeems it mints a new access token whose claim is
    resolved against the database as it stands then. Refusing one here would turn
    "refresh and carry on" into "sign in again" for every session issued before
    organizations existed.
    """
    try:
        reject = _door()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"app.dependencies not importable here (no DB engine): {exc}")

    assert reject({"sub": "user-123", "type": "refresh"}) is None


# ── tenant_scope_owner: admin-bypass branching (DB lookup stubbed) ──────────


class _SentinelSession:
    """Stand-in for an AsyncSession - never touched because _is_admin is stubbed."""


@pytest.mark.asyncio
async def test_owner_filter_none_tenant_short_circuits(monkeypatch) -> None:
    """A None tenant returns None without ever consulting the DB."""

    async def _boom(_session, _user_id):  # pragma: no cover - must not run
        raise AssertionError("_is_admin must not be called for a None tenant")

    monkeypatch.setattr("app.core.tenant_scope._is_admin", _boom)
    assert await tenant_scope_owner(_SentinelSession(), None, actor_id=None) is None


@pytest.mark.asyncio
async def test_owner_filter_admin_is_unrestricted(monkeypatch) -> None:
    """An admin caller maps to None (= 'do not filter'), mirroring projects."""

    async def _is_admin_true(_session, _user_id):
        return True

    monkeypatch.setattr("app.core.tenant_scope._is_admin", _is_admin_true)
    assert await tenant_scope_owner(_SentinelSession(), "org-9", actor_id="admin-user") is None


@pytest.mark.asyncio
async def test_owner_filter_non_admin_keeps_tenant(monkeypatch) -> None:
    """A normal user's query stays scoped to their own tenant id."""

    async def _is_admin_false(_session, _user_id):
        return False

    monkeypatch.setattr("app.core.tenant_scope._is_admin", _is_admin_false)
    assert await tenant_scope_owner(_SentinelSession(), "org-9", actor_id="user-123") == "org-9"


@pytest.mark.asyncio
async def test_the_admin_lookup_is_by_actor_and_not_by_tenant(monkeypatch) -> None:
    """The two arguments used to be one value, and the admin check read the wrong one.

    ``_is_admin`` looks its id up in the users table. While a tenant *was* a user
    that worked; once it is an organization the lookup finds nothing, the check
    fails closed, and every administrator silently loses the unfiltered view of the
    company-wide resources this helper targets. Nothing raises and no row leaks,
    which is why this is pinned by what the lookup is *called with* rather than by
    what it returns.
    """
    seen: list[str | None] = []

    async def _record(_session, user_id):
        seen.append(user_id)
        return False

    monkeypatch.setattr("app.core.tenant_scope._is_admin", _record)
    result = await tenant_scope_owner(_SentinelSession(), "org-9", actor_id="user-123")

    assert seen == ["user-123"], f"the admin lookup was given {seen}, not the actor"
    assert result == "org-9"


@pytest.mark.asyncio
async def test_an_anonymous_actor_never_reads_as_an_admin(monkeypatch) -> None:
    """No actor means no bypass, and the tenant still scopes the query."""

    async def _is_admin(_session, user_id):
        assert user_id is None
        return False

    monkeypatch.setattr("app.core.tenant_scope._is_admin", _is_admin)
    assert await tenant_scope_owner(_SentinelSession(), "org-9", actor_id=None) == "org-9"


# ── get_current_tenant_id: dependency delegates to the pure resolver ────────


@pytest.mark.asyncio
async def test_dependency_delegates_to_resolver() -> None:
    """The FastAPI dependency returns exactly resolve_tenant_id(payload).

    Lives in ``app.dependencies``, which imports the DB engine at module load, so
    this is skipped where the embedded PostgreSQL is not up and exercised in CI.
    """
    try:
        from app.dependencies import get_current_tenant_id
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"app.dependencies not importable here (no DB engine): {exc}")

    assert await get_current_tenant_id(None) is None
    # A caller with an identity but no organization has no tenant. This asserted
    # ``"user-123"`` while the resolver fell through to ``sub``.
    assert await get_current_tenant_id({"sub": "user-123"}) is None
    assert await get_current_tenant_id({"sub": "user-123", "tenant_id": "org-9"}) == "org-9"
