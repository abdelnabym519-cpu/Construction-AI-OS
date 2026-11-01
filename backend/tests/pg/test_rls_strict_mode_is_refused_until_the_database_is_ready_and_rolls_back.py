# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Strict mode is refused until the database is ready, and rolls back cleanly.

The companion file observes what the strict policy does once PostgreSQL has
accepted it. This one observes the two decisions around it, on the same real
cluster:

* a database that is not ready is refused, and the refusal leaves the documented
  permissive policy installed rather than no policy at all, because row-level
  security that is enabled with nothing to allow denies every row;
* once the rows are owned, the same call tightens, which is the backfill-then-
  enforce sequence the roadmap describes, observed rather than asserted;
* turning strict off again really replaces the policy, and repeated cycles are
  stable, so an operator can retreat without leaving a strict predicate hiding
  rows behind a guard that only ever creates.

``provision_rls`` is driven end to end for the same cases, because that is the
function a boot calls: it takes the advisory lock, opens its own transaction and
reads the operator's flag, and none of that is exercised by calling ``apply_rls``
directly.

The ordering inside each test matters and is the reason they do not share one
fixture state: a row nobody owns makes the readiness gate refuse strict mode, so
a test that wants to observe the strict policy has to own that row first, and a
test that wants to observe the refusal has to leave it alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core import rls_setup
from app.core.rls import APP_ROLE
from tests.pg import rls_fixtures as rls

pytestmark = pytest.mark.asyncio

_SELECT = f'SELECT label FROM "{rls.PROBE_TENANT}" ORDER BY label'
_SELECT_IDS = f'SELECT id FROM "{rls.PROBE_TENANT}" ORDER BY id'
_COUNT = f'SELECT count(*) FROM "{rls.PROBE_TENANT}"'


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rls_setup, "rls_enabled", lambda: True)


@pytest_asyncio.fixture
async def probe_tables(pg_engine):  # noqa: ANN001, ANN201 - the lane's async engine fixture
    """Probe tables with two tenants' rows and one row nobody owns.

    No policy is installed here: each test decides which mode to ask for, because
    the ordering of the data and the request is what is under test.
    """
    base = rls.probe_base(rls.PROBE_TENANT, rls.PROBE_SECOND)
    async with pg_engine.begin() as conn:
        await rls.drop_probe_tables(conn, rls.PROBE_TENANT, rls.PROBE_SECOND)
        await rls.ensure_roles(conn)
        await rls.create_probe_tables(conn, rls.PROBE_TENANT, rls.PROBE_SECOND)
        await rls.seed(conn, rls.PROBE_TENANT, ("a-1", rls.TENANT_A, "alpha one"))
        await rls.seed(conn, rls.PROBE_TENANT, ("b-1", rls.TENANT_B, "beta one"))
        await rls.seed(conn, rls.PROBE_TENANT, ("legacy", None, rls.UNOWNED_LABEL))
        await rls.seed(conn, rls.PROBE_SECOND, ("s-a", rls.TENANT_A, "second alpha"))

    try:
        yield SimpleNamespace(engine=pg_engine, base=base)
    finally:
        async with pg_engine.begin() as conn:
            await rls.drop_probe_tables(conn, rls.PROBE_TENANT, rls.PROBE_SECOND)


async def _policy(engine, table: str = rls.PROBE_TENANT) -> dict[str, object]:  # noqa: ANN001
    async with engine.connect() as conn:
        return await rls.installed_policy(conn, table)


async def _own_the_unowned_rows(engine) -> None:  # noqa: ANN001
    """The backfill: give every legacy row an owner, as an administrator would.

    Bounded by ``tenant_id IS NULL`` rather than an unqualified update, so it
    cannot touch a row that already has an owner.
    """
    for table in (rls.PROBE_TENANT, rls.PROBE_SECOND):
        async with engine.begin() as conn:
            await conn.execute(
                text(f'UPDATE "{table}" SET tenant_id = :t WHERE tenant_id IS NULL'),
                {"t": rls.TENANT_A},
            )


async def _add_an_unowned_row(engine, row_id: str) -> None:  # noqa: ANN001
    """Insert a row nobody owns, after the policy is already installed.

    Written by the connecting superuser, which is not subject to the policy, so
    this models a legacy row appearing on an install that has already tightened.
    """
    async with engine.begin() as conn:
        await rls.seed(conn, rls.PROBE_TENANT, (row_id, None, rls.UNOWNED_LABEL))


# ── A. Refused while the database is not ready ──────────────────────────────


async def test_a_row_nobody_owns_refuses_strict_mode(probe_tables) -> None:  # noqa: ANN001
    """The readiness gate is consulted on the real database, and it says no."""
    async with probe_tables.engine.connect() as conn:
        report = await rls_setup.rls_readiness_report(conn, [rls.PROBE_TENANT, rls.PROBE_SECOND])

    assert report["ready_for_strict"] is False
    assert [b["code"] for b in report["blockers"]] == ["null_tenant_rows"]
    assert report["blockers"][0]["table"] == rls.PROBE_TENANT
    assert report["blockers"][0]["rows"] == 1
    assert report["dialect"] == "postgresql"
    assert report["enforced"] is True


async def test_a_refused_strict_request_installs_the_permissive_policy_instead(probe_tables) -> None:  # noqa: ANN001
    """Asking for strict on an unready database must never leave a table unpolicied."""
    async with probe_tables.engine.begin() as conn:
        stats = await rls_setup.apply_rls(conn, probe_tables.base, strict=True)

    assert stats["strict_refused"] == 1  # one blocker code
    assert stats["tables"] == 2

    policy = await _policy(probe_tables.engine)
    assert policy["installed"] is True
    assert policy["is_strict"] is False
    assert policy["rls_enabled"] is True
    assert policy["forced"] is True
    assert "IS NULL" in (policy["qual"] or "").upper()

    # And the fallback behaves like the documented rollout mode: the caller sees
    # its own rows plus the one nobody owns, which is exactly why the rollout mode
    # is not the end state.
    outcome = await rls.as_app_role(probe_tables.engine, rls.TENANT_A, _SELECT, fetch=True)
    assert outcome.current_user == APP_ROLE
    assert outcome.rows == [("alpha one",), (rls.UNOWNED_LABEL,)]

    # Both tables were policied, so the refusal changed the mode and not the reach.
    second = await _policy(probe_tables.engine, rls.PROBE_SECOND)
    assert second["installed"] is True
    assert second["is_strict"] is False


async def test_a_tenant_value_that_is_really_a_user_id_refuses_strict_mode(probe_tables) -> None:  # noqa: ANN001
    """The ``tenant_is_user`` blocker, measured against a real users table.

    The probe stand-in is passed explicitly because ``apply_rls`` measures against
    the application's own ``oe_users_user``, and writing a user row into the
    cluster the whole lane shares is not this test's business.
    """
    async with probe_tables.engine.begin() as conn:
        await rls.create_probe_users(conn, "user-1")
        await rls.seed(conn, rls.PROBE_TENANT, ("a-1", "user-1", "owned by a user id"))
        report = await rls_setup.rls_readiness_report(conn, [rls.PROBE_TENANT], users_table=rls.PROBE_USERS)

    assert report["ready_for_strict"] is False
    blockers = {b["code"]: b for b in report["blockers"]}
    assert "tenant_is_user" in blockers
    assert blockers["tenant_is_user"]["rows"] == 1
    assert blockers["tenant_is_user"]["users"] == 1
    assert blockers["tenant_is_user"]["tables"] == [rls.PROBE_TENANT]


async def test_a_table_that_is_not_in_the_database_is_skipped_not_reported_as_a_blocker(probe_tables) -> None:  # noqa: ANN001
    """A module that was never installed must not stop the report from answering."""
    async with probe_tables.engine.connect() as conn:
        report = await rls_setup.rls_readiness_report(conn, [rls.PROBE_TENANT, "oe_rls_probe_absent"])

    assert [t["table"] for t in report["tables"]] == [rls.PROBE_TENANT]
    codes = [b["code"] for b in report["blockers"]]
    # The unowned probe row is still a blocker, and rightly so; what must not
    # happen is the absent table adding one, or the report giving up.
    assert "unmeasured_table" not in codes
    assert all(b.get("table") != "oe_rls_probe_absent" for b in report["blockers"])
    assert report["ready_for_strict"] is False


async def test_the_report_measures_what_a_strict_policy_would_hide(probe_tables) -> None:  # noqa: ANN001
    """The per-table numbers an operator reads before deciding to tighten."""
    async with probe_tables.engine.connect() as conn:
        report = await rls_setup.rls_readiness_report(conn, [rls.PROBE_TENANT])

    (measured,) = report["tables"]
    assert measured["table"] == rls.PROBE_TENANT
    assert measured["rows"] == 3
    assert measured["null_tenant_rows"] == 1
    assert measured["tenants"] == 2
    assert measured["rows_with_user_tenant"] == 0
    assert measured["rls_enabled"] is False
    assert measured["policy_installed"] is False


# ── B. Accepted once the database is ready ──────────────────────────────────


async def test_backfilling_the_unowned_row_lets_the_same_call_tighten(probe_tables) -> None:  # noqa: ANN001
    """The documented sequence, end to end: measure, refuse, backfill, enforce."""
    async with probe_tables.engine.begin() as conn:
        refused = await rls_setup.apply_rls(conn, probe_tables.base, strict=True)
    assert refused["strict_refused"] == 1
    assert (await _policy(probe_tables.engine))["is_strict"] is False

    await _own_the_unowned_rows(probe_tables.engine)

    async with probe_tables.engine.begin() as conn:
        report = await rls_setup.rls_readiness_report(conn, [rls.PROBE_TENANT, rls.PROBE_SECOND])
        assert report["ready_for_strict"] is True
        assert report["blockers"] == []
        accepted = await rls_setup.apply_rls(conn, probe_tables.base, strict=True)

    assert "strict_refused" not in accepted
    assert accepted["policies_replaced"] == 2  # the permissive policies the refusal installed
    assert (await _policy(probe_tables.engine))["is_strict"] is True

    # The backfilled row now belongs to alpha, so alpha reads it and beta does not.
    alpha = await rls.as_app_role(probe_tables.engine, rls.TENANT_A, _SELECT, fetch=True)
    beta = await rls.as_app_role(probe_tables.engine, rls.TENANT_B, _SELECT, fetch=True)
    assert alpha.rows == [("alpha one",), (rls.UNOWNED_LABEL,)]
    assert beta.rows == [("beta one",)]


async def test_a_tenant_scope_that_matches_nothing_returns_no_rows(probe_tables) -> None:  # noqa: ANN001
    """An invalid scope is not a reason to widen: it simply matches nothing."""
    await _own_the_unowned_rows(probe_tables.engine)
    async with probe_tables.engine.begin() as conn:
        stats = await rls_setup.apply_rls(conn, probe_tables.base, strict=True)
    assert "strict_refused" not in stats

    for invalid in ("not-a-tenant", "tenant-alpha-probe ", "' OR '1'='1"):
        outcome = await rls.as_app_role(probe_tables.engine, invalid, _COUNT, fetch=True)
        assert outcome.rows == [(0,)], invalid
        assert outcome.current_user == APP_ROLE
        assert outcome.rolsuper is False

    # The bound tenant still sees its rows, so the empties above are the scope
    # being wrong and not the policy being broken.
    valid = await rls.as_app_role(probe_tables.engine, rls.TENANT_A, _COUNT, fetch=True)
    assert valid.rows == [(2,)]  # a-1, plus the backfilled row that used to be unowned


# ── C. Rollback, and repeated transitions ───────────────────────────────────


async def test_turning_strict_mode_off_replaces_the_policy_with_the_permissive_one(probe_tables) -> None:  # noqa: ANN001
    """A guarded ``CREATE POLICY`` never replaces, so the rollback has to be explicit."""
    await _own_the_unowned_rows(probe_tables.engine)
    async with probe_tables.engine.begin() as conn:
        await rls_setup.apply_rls(conn, probe_tables.base, strict=True)
    assert (await _policy(probe_tables.engine))["is_strict"] is True

    # A legacy row appears after the install tightened: strict hides it. Rows are
    # identified by id here, because the backfill above gave the original legacy
    # row an owner without changing its label, so a label no longer tells the two
    # apart.
    await _add_an_unowned_row(probe_tables.engine, "late-legacy")
    hidden = await rls.as_app_role(probe_tables.engine, rls.TENANT_A, _SELECT_IDS, fetch=True)
    assert {row[0] for row in (hidden.rows or [])} == {"a-1", "legacy"}

    async with probe_tables.engine.begin() as conn:
        stats = await rls_setup.apply_rls(conn, probe_tables.base, strict=False)

    assert stats["policies_replaced"] == 2
    rolled_back = await _policy(probe_tables.engine)
    assert rolled_back["is_strict"] is False
    assert rolled_back["rls_enabled"] is True
    assert rolled_back["forced"] is True
    assert "IS NULL" in (rolled_back["qual"] or "").upper()

    # The observable consequence of the rollback: the row strict was hiding is
    # readable again, so nothing stayed hidden behind a policy nobody asked for.
    permissive = await rls.as_app_role(probe_tables.engine, rls.TENANT_A, _SELECT_IDS, fetch=True)
    assert {row[0] for row in (permissive.rows or [])} == {"a-1", "legacy", "late-legacy"}


async def test_repeated_enable_and_disable_cycles_end_in_the_mode_that_was_asked_for(probe_tables) -> None:  # noqa: ANN001
    """An operator can change their mind more than once."""
    await _own_the_unowned_rows(probe_tables.engine)
    for cycle in range(3):
        async with probe_tables.engine.begin() as conn:
            await rls_setup.apply_rls(conn, probe_tables.base, strict=True)
        assert (await _policy(probe_tables.engine))["is_strict"] is True, cycle

        async with probe_tables.engine.begin() as conn:
            await rls_setup.apply_rls(conn, probe_tables.base, strict=False)
        assert (await _policy(probe_tables.engine))["is_strict"] is False, cycle

        # Isolation still holds in whichever mode the cycle ended in: beta never
        # reads alpha's row, permissive or strict.
        beta = await rls.as_app_role(probe_tables.engine, rls.TENANT_B, _SELECT, fetch=True)
        assert beta.rows == [("beta one",)], cycle


async def test_a_second_run_in_the_same_mode_leaves_the_installed_policy_alone(probe_tables) -> None:  # noqa: ANN001
    """Idempotent: booting twice changes nothing the first boot did."""
    await _own_the_unowned_rows(probe_tables.engine)
    async with probe_tables.engine.begin() as conn:
        first = await rls_setup.apply_rls(conn, probe_tables.base, strict=True)
        second = await rls_setup.apply_rls(conn, probe_tables.base, strict=True)

    assert first["policies_replaced"] == 0
    assert second["policies_replaced"] == 0
    assert second["tables"] == first["tables"] == 2
    assert (await _policy(probe_tables.engine))["is_strict"] is True


async def test_an_unrecognised_policy_is_never_dropped_by_a_mode_change(probe_tables) -> None:  # noqa: ANN001
    """A policy this code cannot classify is left alone rather than guessed at."""
    async with probe_tables.engine.begin() as conn:
        await conn.execute(text(f'ALTER TABLE "{rls.PROBE_TENANT}" ENABLE ROW LEVEL SECURITY'))
        await conn.execute(
            text(
                f'CREATE POLICY "{rls_setup._POLICY_NAME}" ON "{rls.PROBE_TENANT}" '
                "USING (label IS NOT NULL) WITH CHECK (label IS NOT NULL)"
            )
        )

    async with probe_tables.engine.begin() as conn:
        stats = await rls_setup.apply_rls(conn, probe_tables.base, strict=False)

    # Nothing was dropped, so an administrator's own policy survives a boot.
    assert stats["policies_replaced"] == 0
    async with probe_tables.engine.connect() as conn:
        policy = await rls.installed_policy(conn, rls.PROBE_TENANT)
    assert "IS NOT NULL" in (policy["qual"] or "")
    assert policy["is_strict"] is None


# ── The boot path an operator actually runs ─────────────────────────────────


async def test_provision_rls_installs_the_strict_policy_when_the_operator_asks_for_it(  # noqa: ANN001
    probe_tables, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the advisory lock and its own transaction, as a boot calls it."""
    await _own_the_unowned_rows(probe_tables.engine)
    monkeypatch.setattr(rls_setup, "strict_requested", lambda: True)

    stats = await rls_setup.provision_rls(probe_tables.engine, probe_tables.base)

    assert stats["roles"] > 0
    assert stats["tables"] == 2
    assert "strict_refused" not in stats
    assert (await _policy(probe_tables.engine))["is_strict"] is True
    assert await rls_setup.verify_rls_role(probe_tables.engine) is True


async def test_provision_rls_refuses_strict_and_says_so_when_the_database_is_not_ready(  # noqa: ANN001
    probe_tables, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rls_setup, "strict_requested", lambda: True)

    stats = await rls_setup.provision_rls(probe_tables.engine, probe_tables.base)

    assert stats["strict_refused"] == 1
    policy = await _policy(probe_tables.engine)
    assert policy["installed"] is True
    assert policy["is_strict"] is False


async def test_provision_rls_does_nothing_at_all_while_enforcement_is_off(  # noqa: ANN001
    probe_tables, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default install: no roles, no policies, no transaction opened."""
    monkeypatch.setattr(rls_setup, "rls_enabled", lambda: False)
    monkeypatch.setattr(rls_setup, "strict_requested", lambda: True)

    stats = await rls_setup.provision_rls(probe_tables.engine, probe_tables.base)

    assert stats == {"roles": 0, "tables": 0}
    policy = await _policy(probe_tables.engine)
    assert policy["installed"] is False
    assert policy["rls_enabled"] is False
    # Strict mode is a sub-mode of enforcement, so asking for it alone changes
    # nothing on a default install.
    assert await rls_setup.verify_rls_role(probe_tables.engine) is False


async def test_the_strict_flag_is_read_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reader a boot uses, pinned to the setting it reads."""
    monkeypatch.setattr(rls_setup, "get_settings", lambda: SimpleNamespace(rls_strict=False))
    assert rls_setup.strict_requested() is False

    monkeypatch.setattr(rls_setup, "get_settings", lambda: SimpleNamespace(rls_strict=True))
    assert rls_setup.strict_requested() is True
