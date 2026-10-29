# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""A session that never bound a tenant keeps the connecting role and is not policied.

:mod:`app.core.rls` decides whether a transaction is policied by a sentinel, not
by a value. Its ``after_begin`` listener reads a context variable whose default
is a private ``_UNSET`` object: a *bound* tenant, including the ``None`` an
anonymous request binds, downgrades the transaction to ``oe_app`` and sets the
GUC, while an unbound session returns early and keeps whatever role the
connection arrived with. On a default install that role is the cluster superuser,
which bypasses every policy even with ``FORCE ROW LEVEL SECURITY``.

That is a documented decision and not an oversight - the listener's own comment
calls background work trusted system code and names the remedy, which is to run
it under the ``BYPASSRLS`` role - but it is a decision whose consequence had
never been observed against a database. These tests observe it, on the lane's
real PostgreSQL cluster and through the same listener
:mod:`app.database` registers for the application's own sessions.

Two things are worth reading before the assertions. First, ``None`` and "never
bound" answer identically through the public reader :func:`current_request_tenant`
and behave completely differently in the database, which is why the tests assert
on ``current_user`` and row counts rather than on the reader.

The *shape* of the gap is pinned beside the always-on unit lane rather than here,
in ``test_which_paths_bind_a_tenant_and_which_still_do_not``:
the binding has exactly one call site in the whole application, the global request
dependency, so every path that opens a session outside a request is unpolicied by
construction. That needs no database, and it should fail in every lane and not
only when the PostgreSQL one is selected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import rls as rls_module
from app.core import rls_setup
from app.core.rls import APP_ROLE, GUC_NAME, SYSTEM_ROLE
from tests.pg import rls_fixtures as rls

pytestmark = pytest.mark.asyncio

_COUNT = f'SELECT count(*) FROM "{rls.PROBE_TENANT}"'
_IDENTITY = "SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"


@pytest_asyncio.fixture
async def bound_probe(pg_engine, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    """Probe table under the strict policy, plus a session factory on this cluster.

    ``app.database`` registers the row-level-security listener on the plain
    ``Session`` class, which is what ``AsyncSession`` drives, so a factory built
    here is driven by the same listener the application uses rather than by a
    test-local copy of it. Enforcement is switched on for the duration of the
    test only; the listener reads the flag when a transaction begins.
    """
    monkeypatch.setattr(rls_module, "rls_enabled", lambda: True)
    monkeypatch.setattr(rls_setup, "rls_enabled", lambda: True)

    base = rls.probe_base(rls.PROBE_TENANT)
    async with pg_engine.begin() as conn:
        await rls.drop_probe_tables(conn, rls.PROBE_TENANT)
        await rls.ensure_roles(conn)
        await rls.create_probe_tables(conn, rls.PROBE_TENANT)
        await rls.seed(conn, rls.PROBE_TENANT, ("a-1", rls.TENANT_A, "alpha one"))
        await rls.seed(conn, rls.PROBE_TENANT, ("a-2", rls.TENANT_A, "alpha two"))
        await rls.seed(conn, rls.PROBE_TENANT, ("b-1", rls.TENANT_B, "beta one"))
        stats = await rls_setup.apply_rls(conn, base, strict=True)
        await rls.seed(conn, rls.PROBE_TENANT, ("legacy", None, rls.UNOWNED_LABEL))

    # A fixture that silently fell back to the permissive policy would make every
    # count below wrong in the same direction, so the precondition is asserted.
    assert "strict_refused" not in stats
    assert stats["tables"] == 1

    factory = async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield SimpleNamespace(engine=pg_engine, factory=factory, base=base)
    finally:
        async with pg_engine.begin() as conn:
            await rls.drop_probe_tables(conn, rls.PROBE_TENANT)


async def _observe(factory) -> tuple[str, bool, str | None, int]:  # noqa: ANN001
    """One session's identity, the GUC it carries, and how many rows it sees.

    The GUC is returned raw, because its three states are three different facts:
    a tenant string for a bound request, the empty string for an anonymous one
    (the listener sets it deliberately, and a strict policy matches nothing), and
    SQL NULL for a session that was never bound at all.
    """
    async with factory() as session:
        identity = (await session.execute(text(_IDENTITY))).one()
        bound_to = (await session.execute(text(f"SELECT current_setting('{GUC_NAME}', true)"))).scalar_one()
        visible = (await session.execute(text(_COUNT))).scalar_one()
    return str(identity[0]), bool(identity[1]), None if bound_to is None else str(bound_to), int(visible)


async def test_a_request_shaped_session_runs_as_the_restricted_role(bound_probe) -> None:  # noqa: ANN001
    """Binding a tenant is what downgrades the transaction, and the policy then bites."""
    token = rls_module.set_request_tenant(rls.TENANT_A)
    try:
        role, is_super, bound_to, visible = await _observe(bound_probe.factory)
    finally:
        rls_module.reset_request_tenant(token)

    assert role == APP_ROLE
    assert is_super is False
    assert bound_to == rls.TENANT_A
    assert visible == 2  # alpha's two rows, of four in the table
    assert await rls.admin_rows(bound_probe.engine, _COUNT) == [(4,)]


async def test_an_anonymous_request_binds_no_tenant_and_the_policy_denies_everything(bound_probe) -> None:  # noqa: ANN001
    """``None`` is a bound value, so the role still downgrades and nothing matches."""
    token = rls_module.set_request_tenant(None)
    try:
        role, is_super, bound_to, visible = await _observe(bound_probe.factory)
    finally:
        rls_module.reset_request_tenant(token)

    assert role == APP_ROLE
    assert is_super is False
    assert bound_to == ""
    assert visible == 0
    # The public reader cannot tell this apart from "never bound" ...
    assert rls_module.current_request_tenant() is None


async def test_a_session_that_never_bound_a_tenant_keeps_the_connecting_role(bound_probe) -> None:  # noqa: ANN001
    """... and yet the database treats the two completely differently.

    This is the gap, observed: the same reader answer, one transaction policied
    and denied everything, the next one running as a superuser and reading every
    row in the table including two tenants' and the unowned one.
    """
    assert rls_module.current_request_tenant() is None
    role, is_super, bound_to, visible = await _observe(bound_probe.factory)

    assert role != APP_ROLE
    assert is_super is True  # a superuser bypasses the policy, FORCE or not
    assert visible == 4
    # The GUC was never set at all, which is a different state from the empty
    # string an anonymous request binds: nothing downstream could read a tenant.
    assert bound_to is None


async def test_resetting_the_binding_returns_a_session_to_the_unpolicied_shape(bound_probe) -> None:  # noqa: ANN001
    """The context variable does not leak from one unit of work into the next."""
    token = rls_module.set_request_tenant(rls.TENANT_A)
    try:
        bound_role, _, _, bound_visible = await _observe(bound_probe.factory)
    finally:
        rls_module.reset_request_tenant(token)

    after_role, after_super, _, after_visible = await _observe(bound_probe.factory)

    assert (bound_role, bound_visible) == (APP_ROLE, 2)
    assert after_role != APP_ROLE
    assert after_super is True
    assert after_visible == 4


async def test_the_bypass_role_is_the_documented_way_to_run_trusted_system_work(bound_probe) -> None:  # noqa: ANN001
    """``oe_system`` sees every row even with a tenant bound, and is not a superuser."""
    outcome = await rls.as_app_role(bound_probe.engine, rls.TENANT_A, _COUNT, fetch=True, role=SYSTEM_ROLE)

    assert outcome.current_user == SYSTEM_ROLE
    assert outcome.rolsuper is False
    assert outcome.rows == [(4,)]

    async with bound_probe.engine.connect() as conn:
        attributes = await rls.role_attributes(conn, SYSTEM_ROLE)
    assert attributes["rolbypassrls"] is True
    assert attributes["rolcanlogin"] is False

    # The request role, bound to the same tenant, still sees only its own rows:
    # bypassing is a property of the role and not of the session.
    app_outcome = await rls.as_app_role(bound_probe.engine, rls.TENANT_A, _COUNT, fetch=True)
    assert app_outcome.current_user == APP_ROLE
    assert app_outcome.rows == [(2,)]
