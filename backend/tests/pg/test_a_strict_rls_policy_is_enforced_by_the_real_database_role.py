# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""A strict tenant policy is enforced by the real database role, on PostgreSQL.

The unit suite proves what :mod:`app.core.rls_setup` emits. This file proves what
PostgreSQL does with it, which is the part that decides whether tenant isolation
is a property of the platform or of every query someone remembers to write.

Everything here runs against the lane's embedded PostgreSQL 16 cluster, on probe
tables, through the repository's own ``apply_rls``. Reads and writes that are
meant to be policied are issued on a connection that has done
``SET LOCAL ROLE "oe_app"`` and bound ``app.current_tenant``, exactly as
:mod:`app.core.rls` does for a request, and each assertion also records which
role produced it: the cluster's session user is the ``postgres`` superuser, and a
superuser bypasses every policy without a word, so a test that did not prove it
was running as the restricted role would pass just as happily with no policy
installed at all.

The strict predicate is installed while the database is still clean, and the
unowned row is inserted afterwards, because that ordering is the interesting one:
it is what a legacy row looks like to an install that has already tightened, and
the readiness report would have refused strict mode outright had the row been
there first.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core import rls_setup
from app.core.rls import APP_ROLE, SYSTEM_ROLE
from app.core.rls_setup import _NEVER_POLICY, RLSConfigurationError, tenant_tables
from tests.pg import rls_fixtures as rls

pytestmark = pytest.mark.asyncio

_SELECT = f'SELECT label FROM "{rls.PROBE_TENANT}" ORDER BY label'
_COUNT = f'SELECT count(*) FROM "{rls.PROBE_TENANT}"'


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """``apply_rls`` and ``verify_rls_role`` are no-ops unless enforcement is on."""
    monkeypatch.setattr(rls_setup, "rls_enabled", lambda: True)


@pytest_asyncio.fixture
async def strict_probe(pg_engine):  # noqa: ANN001, ANN201 - the lane's async engine fixture
    """Two probe tables under the strict policy, with an unowned row added after.

    ``apply_rls(strict=True)`` is the real provisioning call, so the readiness
    gate, the role statements and the policy DDL under test are the shipped ones.
    The tables are dropped afterwards: this cluster is shared with the rest of the
    lane and row-level security must not be left enabled on anything another
    suite reads.
    """
    base = rls.probe_base(rls.PROBE_TENANT, rls.PROBE_SECOND)
    async with pg_engine.begin() as conn:
        await rls.drop_probe_tables(conn, rls.PROBE_TENANT, rls.PROBE_SECOND)
        await rls.ensure_roles(conn)
        await rls.create_probe_tables(conn, rls.PROBE_TENANT, rls.PROBE_SECOND)
        # Clean first: every row owned, no tenant value that is a user id. This is
        # what lets the readiness gate accept strict mode at all.
        await rls.seed(conn, rls.PROBE_TENANT, ("a-1", rls.TENANT_A, "alpha one"))
        await rls.seed(conn, rls.PROBE_TENANT, ("a-2", rls.TENANT_A, "alpha two"))
        await rls.seed(conn, rls.PROBE_TENANT, ("b-1", rls.TENANT_B, "beta one"))
        await rls.seed(conn, rls.PROBE_SECOND, ("s-a", rls.TENANT_A, "second alpha"))
        stats = await rls_setup.apply_rls(conn, base, strict=True)
        # Only now the unowned row, written by the superuser who is not subject to
        # the policy: a legacy record that appears after the install tightened.
        await rls.seed(conn, rls.PROBE_TENANT, ("legacy", None, rls.UNOWNED_LABEL))
        await rls.seed(conn, rls.PROBE_SECOND, ("s-legacy", None, rls.UNOWNED_LABEL))

    try:
        yield SimpleNamespace(engine=pg_engine, base=base, stats=stats)
    finally:
        async with pg_engine.begin() as conn:
            await rls.drop_probe_tables(conn, rls.PROBE_TENANT, rls.PROBE_SECOND)


# ── What provisioning installed ─────────────────────────────────────────────


async def test_apply_rls_installs_the_strict_predicate_on_a_real_database(strict_probe) -> None:  # noqa: ANN001
    """The readiness gate accepted strict mode, and PostgreSQL stored it."""
    assert strict_probe.stats["tables"] == 2
    assert "strict_refused" not in strict_probe.stats

    async with strict_probe.engine.connect() as conn:
        policy = await rls.installed_policy(conn, rls.PROBE_TENANT)

    assert policy["installed"] is True
    assert policy["is_strict"] is True
    assert policy["rls_enabled"] is True
    # FORCE binds the table owner as well, so owning a table is not a way past it.
    assert policy["forced"] is True
    assert "IS NULL" not in (policy["qual"] or "").upper()
    assert "current_setting" in (policy["qual"] or "")


async def test_the_policy_is_installed_on_every_table_it_was_asked_for(strict_probe) -> None:  # noqa: ANN001
    """Per table, not once for the database."""
    async with strict_probe.engine.connect() as conn:
        first = await rls.installed_policy(conn, rls.PROBE_TENANT)
        second = await rls.installed_policy(conn, rls.PROBE_SECOND)

    assert first["is_strict"] is True
    assert second["is_strict"] is True

    outcome = await rls.as_app_role(
        strict_probe.engine, rls.TENANT_A, f'SELECT label FROM "{rls.PROBE_SECOND}"', fetch=True
    )
    assert outcome.rows == [("second alpha",)]


# ── What the restricted role can see ────────────────────────────────────────


async def test_the_restricted_role_sees_its_own_tenant_and_no_other(strict_probe) -> None:  # noqa: ANN001
    """The core isolation claim, observed rather than inferred."""
    alpha = await rls.as_app_role(strict_probe.engine, rls.TENANT_A, _SELECT, fetch=True)
    beta = await rls.as_app_role(strict_probe.engine, rls.TENANT_B, _SELECT, fetch=True)

    # Which role answered. Without this the assertion below would also hold for a
    # superuser, which sees everything, and for a table with no rows at all.
    assert alpha.current_user == APP_ROLE
    assert alpha.rolsuper is False
    assert alpha.session_user != APP_ROLE

    assert alpha.rows == [("alpha one",), ("alpha two",)]
    assert beta.rows == [("beta one",)]


async def test_an_unowned_row_is_hidden_from_every_tenant_under_the_strict_policy(strict_probe) -> None:  # noqa: ANN001
    """No ``IS NULL`` arm, so a row with no tenant belongs to nobody."""
    alpha = await rls.as_app_role(strict_probe.engine, rls.TENANT_A, _SELECT, fetch=True)
    beta = await rls.as_app_role(strict_probe.engine, rls.TENANT_B, _SELECT, fetch=True)
    everyone = await rls.admin_rows(strict_probe.engine, _SELECT)

    assert (rls.UNOWNED_LABEL,) not in (alpha.rows or [])
    assert (rls.UNOWNED_LABEL,) not in (beta.rows or [])
    # It is not gone, it is hidden: the superuser still reads it, so the two rows
    # a tenant sees are the result of a policy and not of an empty table.
    assert (rls.UNOWNED_LABEL,) in everyone


async def test_a_caller_with_no_tenant_bound_matches_nothing_at_all(strict_probe) -> None:  # noqa: ANN001
    """An empty GUC fails closed instead of widening to every row."""
    anonymous = await rls.as_app_role(strict_probe.engine, None, _COUNT, fetch=True)
    empty = await rls.as_app_role(strict_probe.engine, "", _COUNT, fetch=True)

    assert anonymous.rows == [(0,)]
    assert empty.rows == [(0,)]
    assert anonymous.current_user == APP_ROLE


async def test_the_same_query_as_the_connecting_superuser_returns_every_row(strict_probe) -> None:  # noqa: ANN001
    """The contrast that makes the four assertions above mean something."""
    as_super = await rls.admin_rows(strict_probe.engine, _COUNT)
    as_tenant = await rls.as_app_role(strict_probe.engine, rls.TENANT_A, _COUNT, fetch=True)

    assert as_super == [(4,)]
    assert as_tenant.rows == [(2,)]


# ── What the restricted role can change ─────────────────────────────────────


async def test_a_cross_tenant_update_changes_nothing(strict_probe) -> None:  # noqa: ANN001
    """Committed, so what is asserted afterwards is what the database kept."""
    outcome = await rls.as_app_role(
        strict_probe.engine,
        rls.TENANT_A,
        f"UPDATE \"{rls.PROBE_TENANT}\" SET label = 'hijacked' WHERE tenant_id = '{rls.TENANT_B}'",
        commit=True,
    )

    assert outcome.error is None  # not an error: the row is simply not in scope
    assert outcome.rowcount == 0
    kept = await rls.admin_rows(strict_probe.engine, f"SELECT label FROM \"{rls.PROBE_TENANT}\" WHERE id = 'b-1'")
    assert kept == [("beta one",)]


async def test_a_cross_tenant_delete_removes_nothing(strict_probe) -> None:  # noqa: ANN001
    outcome = await rls.as_app_role(
        strict_probe.engine,
        rls.TENANT_A,
        f"DELETE FROM \"{rls.PROBE_TENANT}\" WHERE tenant_id = '{rls.TENANT_B}'",
        commit=True,
    )

    assert outcome.error is None
    assert outcome.rowcount == 0
    assert await rls.admin_rows(strict_probe.engine, _COUNT) == [(4,)]


async def test_an_unqualified_delete_reaches_only_the_callers_own_rows(strict_probe) -> None:  # noqa: ANN001
    """``DELETE FROM t`` with no predicate at all is still bounded by the policy."""
    outcome = await rls.as_app_role(strict_probe.engine, rls.TENANT_A, f'DELETE FROM "{rls.PROBE_TENANT}"', commit=True)

    assert outcome.error is None
    assert outcome.rowcount == 2  # alpha's two rows, and only those
    survivors = await rls.admin_rows(strict_probe.engine, f'SELECT id FROM "{rls.PROBE_TENANT}" ORDER BY id')
    assert survivors == [("b-1",), ("legacy",)]


async def test_writing_a_row_for_another_tenant_is_refused_by_the_policy(strict_probe) -> None:  # noqa: ANN001
    """``WITH CHECK`` carries the same predicate, so a forged tenant cannot be stored."""
    outcome = await rls.as_app_role(
        strict_probe.engine,
        rls.TENANT_A,
        f'INSERT INTO "{rls.PROBE_TENANT}" (id, tenant_id, label) '
        f"VALUES ('forged', '{rls.TENANT_B}', 'forged for beta')",
        commit=True,
    )

    assert outcome.error is not None
    assert "row-level security" in outcome.error
    assert await rls.admin_rows(
        strict_probe.engine, f"SELECT count(*) FROM \"{rls.PROBE_TENANT}\" WHERE id = 'forged'"
    ) == [(0,)]


async def test_strict_mode_refuses_to_create_a_new_unowned_row(strict_probe) -> None:  # noqa: ANN001
    """A strict install cannot grow the backlog of rows it hides."""
    outcome = await rls.as_app_role(
        strict_probe.engine,
        rls.TENANT_A,
        f"INSERT INTO \"{rls.PROBE_TENANT}\" (id, tenant_id, label) VALUES ('new-legacy', NULL, 'born unowned')",
        commit=True,
    )

    assert outcome.error is not None
    assert "row-level security" in outcome.error

    writing_its_own_tenant = await rls.as_app_role(
        strict_probe.engine,
        rls.TENANT_A,
        f'INSERT INTO "{rls.PROBE_TENANT}" (id, tenant_id, label) '
        f"VALUES ('new-alpha', '{rls.TENANT_A}', 'born owned')",
        commit=True,
    )
    # So the refusal above is the predicate and not a missing INSERT grant.
    assert writing_its_own_tenant.error is None
    assert writing_its_own_tenant.rowcount == 1


async def test_row_level_security_with_no_policy_at_all_hides_every_row(strict_probe) -> None:  # noqa: ANN001
    """Enabled and unpolicied denies, which is why a refused strict request still installs one."""
    async with strict_probe.engine.begin() as conn:
        await conn.execute(text(f'DROP POLICY IF EXISTS "{rls_setup._POLICY_NAME}" ON "{rls.PROBE_TENANT}"'))

    outcome = await rls.as_app_role(strict_probe.engine, rls.TENANT_A, _COUNT, fetch=True)
    assert outcome.rows == [(0,)]
    assert await rls.admin_rows(strict_probe.engine, _COUNT) == [(4,)]


# ── The role itself ─────────────────────────────────────────────────────────


async def test_the_runtime_role_holds_dml_and_still_cannot_bypass_the_policy(strict_probe) -> None:  # noqa: ANN001
    """Least privilege, measured in the catalog and then in behaviour."""
    async with strict_probe.engine.connect() as conn:
        attributes = await rls.role_attributes(conn, APP_ROLE)
        privileges = (
            await conn.execute(
                text(
                    "SELECT has_table_privilege(:r, :t, 'SELECT'), has_table_privilege(:r, :t, 'INSERT'), "
                    "has_table_privilege(:r, :t, 'UPDATE'), has_table_privilege(:r, :t, 'DELETE')"
                ),
                {"r": APP_ROLE, "t": rls.PROBE_TENANT},
            )
        ).one()

    assert attributes == {
        "exists": True,
        "rolsuper": False,
        "rolbypassrls": False,
        "rolcanlogin": False,
        "rolinherit": False,
    }
    # It may do everything the application needs to a row it is allowed to see.
    assert tuple(privileges) == (True, True, True, True)
    # And it still sees two of four rows, so the denial is the policy and not a
    # missing grant: privileges and isolation are separate facts.
    outcome = await rls.as_app_role(strict_probe.engine, rls.TENANT_A, _COUNT, fetch=True)
    assert outcome.rows == [(2,)]


async def test_the_bypass_role_is_separate_and_is_not_the_request_role(strict_probe) -> None:  # noqa: ANN001
    """``oe_system`` carries BYPASSRLS; ``oe_app`` must not."""
    async with strict_probe.engine.connect() as conn:
        system = await rls.role_attributes(conn, SYSTEM_ROLE)
        app = await rls.role_attributes(conn, APP_ROLE)

    assert system["exists"] is True
    assert system["rolbypassrls"] is True
    assert system["rolsuper"] is False
    assert app["rolbypassrls"] is False
    assert SYSTEM_ROLE != APP_ROLE


async def test_verify_rls_role_accepts_the_role_provisioning_created(strict_probe) -> None:  # noqa: ANN001
    """The boot preflight passes on a database provisioning just prepared."""
    assert await rls_setup.verify_rls_role(strict_probe.engine) is True


@pytest.mark.parametrize(
    ("grant", "revoke"),
    [("SUPERUSER", "NOSUPERUSER"), ("BYPASSRLS", "NOBYPASSRLS")],
)
async def test_verify_rls_role_refuses_a_role_that_could_bypass_the_policy(  # noqa: ANN001
    strict_probe, grant: str, revoke: str
) -> None:
    """Either attribute makes every policy decorative, so startup must refuse."""
    async with strict_probe.engine.begin() as conn:
        await conn.execute(text(f'ALTER ROLE "{APP_ROLE}" {grant}'))
    try:
        with pytest.raises(RLSConfigurationError) as raised:
            await rls_setup.verify_rls_role(strict_probe.engine)
        assert grant in str(raised.value)

        # And the refusal is about the attribute: with it taken away the same
        # database passes again.
        async with strict_probe.engine.begin() as conn:
            await conn.execute(text(f'ALTER ROLE "{APP_ROLE}" {revoke}'))
        assert await rls_setup.verify_rls_role(strict_probe.engine) is True
    finally:
        async with strict_probe.engine.begin() as conn:
            await conn.execute(text(f'ALTER ROLE "{APP_ROLE}" NOSUPERUSER NOBYPASSRLS'))


# ── Records that are global by design ───────────────────────────────────────


async def test_the_reference_tables_that_are_global_by_design_are_never_policied(strict_probe) -> None:  # noqa: ANN001
    """A global record is excluded by name, not admitted by a NULL tenant escape."""
    from app.database import Base

    policied = tenant_tables(Base)
    assert policied, "the real schema does carry tenant tables"
    assert sorted(set(_NEVER_POLICY) & set(policied)) == []

    async with strict_probe.engine.connect() as conn:
        present = set(
            (
                await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:n)"),
                    {"n": sorted(_NEVER_POLICY)},
                )
            ).scalars()
        )
        assert present, "the lane's schema should hold the reference tables"
        for name in sorted(present):
            state = await rls.installed_policy(conn, name)
            # Not even enabled, so nothing can hide a row in them by accident.
            assert state["rls_enabled"] is False, name
            assert state["installed"] is False, name
            # And the restricted role can actually read one, which is what
            # "global" has to mean in behaviour and not only in a denylist.
            outcome = await rls.as_app_role(
                strict_probe.engine, rls.TENANT_A, f'SELECT count(*) FROM "{name}"', fetch=True
            )
            assert outcome.error is None, name
            assert outcome.current_user == APP_ROLE
