# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Tenant-scope resolution (multi-tenant isolation).

This module centralizes "which tenant is the caller?" into one place so that
data-access layers all ask the same question the same way instead of each
re-deriving an owner id from the JWT.

What the tenant is
------------------
An **organization** (ADR 005). Its id, as a canonical 36-character UUID
string, is the value every ``tenant_id`` column in this schema holds and the
value the ``tenant_id`` claim in an access token carries. The two are the same
string, which is what lets PostgreSQL row-level security and the application
layer agree without a translation table.

It used to be the caller's user id. That was the documented single-tenant
doctrine - migration ``v231_contact_tenant_id`` says *"For single-tenant
installs ... it equals the creator's user id"* - and :func:`resolve_tenant_id`
implemented it by falling back to ``sub`` when no claim was present. ADR 005
removes the fallback. Keeping it would have made the ``tenant_is_user``
readiness blocker permanent: an install could be moved onto organizations
perfectly and the next claimless token would put a person straight back into a
tenant column.

Resolution policy
-----------------
:func:`resolve_tenant_id` returns the ``tenant_id`` claim, or ``None``. That is
the whole policy, and it is deliberately a total function:

* ``None`` means "no tenant is known", which is true for an anonymous caller,
  for a system/background caller with no request, and for a credential issued
  before organizations existed.
* Deciding what to *do* about the third case is not this module's job. An
  authenticated access token that names no organization is refused at the door
  by :func:`app.dependencies.reject_token_without_a_tenant`, so a resolver that
  stayed total and a door that fails closed divide the work without either one
  guessing.

Unauthenticated and system contexts therefore keep their documented behaviour:
they resolve to ``None``, exactly as before.

NON-BREAKING GUARANTEE
----------------------
The FastAPI dependency :data:`CurrentTenantId` is wired in
:mod:`app.dependencies` on top of ``get_optional_user_payload``, which returns
``None`` for anonymous / unknown callers instead of raising. The dependency
therefore never adds a 401/403 to an endpoint that did not already have one, and
returns ``None`` whenever the tenant cannot be determined.

A ``None`` tenant must be treated by callers as "do not narrow the query"
**only** where that is already the established admin/unrestricted policy; on a
tenant-gated endpoint a ``None`` should scope to nothing (the safe default,
mirroring :func:`app.dependencies.accessible_project_ids`). Where that could
have changed behaviour, it was checked rather than assumed:
``CatalogueStore._available`` *adds* an organization's catalogues only when a
tenant is present, so treating a claimless caller as anonymous narrows the
result rather than widening it.

This is the app-layer half of the multi-tenant plan. PostgreSQL-native RLS is
the other half, in :mod:`app.core.rls_setup`, and strict enforcement stays off
by default: it is enabled only when :func:`app.core.rls_readiness.rls_readiness_report`
says the data proves ownership. PostgreSQL superusers and BYPASSRLS roles bypass
policies even with ``FORCE ROW LEVEL SECURITY``, so enforced requests run under
a separate non-bypassing role.

Import safety
-------------
This module intentionally imports **nothing** from :mod:`app.dependencies` or
:mod:`app.database` at module load, and the tenancy constants below live here
rather than in :mod:`app.modules.users.models` because that module imports
``app.database``, which builds an engine at import time. Both
:mod:`app.modules.users.repairs` (boot path) and
:mod:`app.modules.users.service` (token mint) import the constants from here, so
the repair that backfills an organization and the mint that names one cannot
drift apart on what "the default organization" is called. The pure resolver
:func:`resolve_tenant_id` is therefore importable, and unit-testable, in any
environment, even where the embedded PostgreSQL engine is not yet up. The
request-time dependency is assembled in :mod:`app.dependencies` (which already
owns the DB-session plumbing), keeping the import edge one-directional:
``dependencies -> tenant_scope`` only, never back.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: The JWT claim carrying the caller's organization id. Minted in
#: :meth:`app.modules.users.service.UserService._issue_token_pair` and read here.
TENANT_CLAIM = "tenant_id"

#: The claim naming the caller. Kept as a constant because it is the value this
#: module no longer mistakes for a tenant, and a reader should see both names.
SUBJECT_CLAIM = "sub"

#: The slug of the organization a single-tenant install gets. Unique across the
#: table, so two boots racing to create it collide on purpose and one wins.
DEFAULT_ORGANIZATION_SLUG = "default"

#: The human-readable name of that organization.
DEFAULT_ORGANIZATION_NAME = "Default organization"

#: The only membership state that entitles a caller to a token naming that
#: organization. A suspended membership is not one you may act in.
MEMBERSHIP_ACTIVE = "active"


def resolve_tenant_id(payload: dict[str, Any] | None) -> str | None:
    """Resolve the caller's tenant id from a decoded JWT payload.

    Pure function (no I/O) so it is trivially unit-testable.

    Resolution:
        1. The ``tenant_id`` claim, when present and non-empty. This is the only
           source. It is resolved from the database when the token is minted, so
           it names an organization the holder was an active member of at that
           moment.
        2. ``None`` in every other case: no payload, no claim, or a claim that is
           blank once stripped.

    There is no third step. A previous version of this function fell back to
    ``sub``, on the single-tenant reading that the tenant is the calling user.
    That fallback is what ADR 005 removes, and its removal is the content of this
    change: a person's id is who they are, not which company's rows they may see,
    and the two coincide only in an install with one person in it.

    ``None`` is not a refusal. It means "no tenant is known", and callers decide
    what that is worth: an anonymous request, a background job with no request at
    all, and a credential issued before organizations existed all resolve to
    ``None``, and only the last of those is refused - by
    :func:`app.dependencies.reject_token_without_a_tenant`, at the door, where the
    distinction between "no caller" and "a caller with an unusable credential" is
    still available.

    Args:
        payload: Decoded JWT payload, or ``None`` for an anonymous caller.

    Returns:
        The tenant id as a string, or ``None`` when it cannot be resolved.
    """
    if not payload:
        return None

    raw_tenant = payload.get(TENANT_CLAIM)
    if raw_tenant is None:
        return None

    # ``str()`` because some identity providers emit numeric claims, and a
    # numeric organization id is still an organization id. ``strip()`` because a
    # claim padded with whitespace is not a different tenant, and a blank one is
    # not a tenant at all.
    tenant = str(raw_tenant).strip()
    return tenant or None


# The request-time FastAPI dependency ``get_current_tenant_id`` and its
# ``CurrentTenantId`` alias live in :mod:`app.dependencies` (alongside
# ``CurrentUserId`` / ``SessionDep``): that module already owns the
# ``get_optional_user_payload`` building block and the DB-session plumbing, so
# wiring the dependency there keeps this module free of any ``app.dependencies``
# / ``app.database`` import and thus pure-importable. It delegates to
# :func:`resolve_tenant_id` above, and refuses a claimless access token before
# any handler runs.


async def tenant_scope_owner(
    session: AsyncSession,
    tenant_id: str | None,
    *,
    actor_id: str | None,
) -> str | None:
    """Resolve the owner-scope filter for ``tenant_id``, honouring admin bypass.

    Centralises the ``owner_filter = None if admin else tenant`` idiom that was
    copy-pasted across routers (e.g. ``contacts.list_contacts``). Pass the result
    as the ``owner_id=`` argument of a repository method that knows how to apply
    (or skip) the tenant filter::

        owner = await tenant_scope_owner(session, tenant_id, actor_id=payload["sub"])
        items, total = await repo.list(owner_id=owner, ...)

    ``actor_id`` is required and keyword-only, and it is not ``tenant_id``. This
    function used to take one argument and hand it to :func:`_is_admin`, which
    looks its argument up in the users table. While a tenant *was* a user that
    worked; once it is an organization the lookup finds nothing, the check fails
    closed, and every administrator silently loses the unfiltered view of the
    company-wide resources this helper exists for. Nothing raises and no row leaks,
    which is why the separation is pinned by a test asserting what the admin
    lookup is *called with* rather than only what it returns.

    Returns:
        ``None`` when the caller is an admin (= "do not filter", the same
        unrestricted sentinel ``accessible_project_ids`` uses) **or** when
        ``tenant_id`` is already ``None``; otherwise the tenant id unchanged.

    Note the deliberate overload of ``None``: an anonymous caller and an admin
    both map to ``None`` here. That is correct for the *company-wide* resources
    this helper targets (contacts, subcontractors, supplier catalogs), where the
    repository's ``owner_id=None`` branch is the admin/unrestricted view and an
    anonymous caller never reaches the handler (a ``RequirePermission`` guard
    rejects them first). Do **not** use this helper to gate a per-row fetch - use
    :func:`app.dependencies.verify_project_access` (404-on-deny) for that.
    """
    if tenant_id is None:
        return None

    if await _is_admin(session, actor_id):
        return None

    return tenant_id


async def _is_admin(session: AsyncSession, user_id: str | None) -> bool:
    """Return ``True`` when ``user_id`` resolves to an active admin user.

    Mirrors the admin-bypass check in
    :func:`app.dependencies.verify_project_access` /
    :func:`app.dependencies.accessible_project_ids`: a malformed id or a lookup
    failure fails *closed* (treated as non-admin -> the caller's query stays
    scoped) rather than silently granting an unfiltered view.

    Imports are function-local on purpose: importing the users repository (and
    through it ``app.database``) at module import time would open a PostgreSQL
    connection during collection and break the pure unit tests / non-PG
    environments. Every other auth helper in this codebase defers these imports
    the same way.
    """
    import uuid as _uuid

    from app.modules.users.repository import UserRepository

    if user_id is None:
        return False

    try:
        uid = _uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        return False

    try:
        user = await UserRepository(session).get_by_id(uid)
    except Exception:
        logger.exception("Admin-role lookup failed during tenant-scope resolution")
        return False

    return user is not None and getattr(user, "role", "") == "admin"
