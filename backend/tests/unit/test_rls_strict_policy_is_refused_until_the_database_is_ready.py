# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""A strict tenant policy is refused until the database is ready for it.

``OE_RLS_STRICT`` drops the ``tenant_id IS NULL`` arm from the policy, so a row
with no tenant becomes invisible to everybody. On a database that still holds
such rows, or whose tenant values are only user ids, that hides live business
data from its own owners. That is why :func:`rls_readiness_report` exists, and
why :func:`apply_rls` consults it before honouring the flag.

These tests pin the behaviours that make the flag safe to ship:

* a refused strict boot still provisions the permissive policy, so asking for
  strict can never leave a database with no policy at all;
* a ready database gets a predicate with no NULL escape, which fails closed for
  an anonymous caller because an empty GUC matches no row;
* turning the flag off again really rolls back. ``CREATE POLICY`` is guarded on
  ``pg_policies`` and never replaces an installed policy, so without an explicit
  drop a strict predicate would survive the rollback and keep hiding rows;
* a policy whose mode cannot be read from the catalog is left alone, because
  guessing in order to drop it is how data gets hidden or exposed;
* the permissive path emits what it emitted before strict existed.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.rls import GUC_NAME

_CENSUS = "SELECT tablename, qual FROM pg_policies"
_PERMISSIVE = "(tenant_id IS NULL OR tenant_id::text = current_setting('app.current_tenant', true))"
_STRICT = "(tenant_id::text = current_setting('app.current_tenant', true))"
_TENANT_TABLE = "oe_demo_tenant_row"


class _Result:
    def __init__(self, rows: list[Any] | None = None, value: Any = None) -> None:
        self._rows = rows if rows is not None else []
        self._value = value

    def all(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return self._value


class _Connection:
    """Records statements and answers the policy-mode census from ``policy_rows``."""

    def __init__(self, policy_rows: list[tuple[str, str | None]] | None = None) -> None:
        self.executed: list[str] = []
        self.policy_rows = policy_rows or []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        self.executed.append(sql)
        if _CENSUS in sql:
            return _Result(rows=self.policy_rows)
        return _Result()

    @contextlib.asynccontextmanager
    async def begin_nested(self):
        yield self


def _base() -> SimpleNamespace:
    """A declarative Base with one tenant table and one table without a tenant."""
    from sqlalchemy import Column, Integer, MetaData, String, Table

    metadata = MetaData()
    Table(_TENANT_TABLE, metadata, Column("id", Integer, primary_key=True), Column("tenant_id", String))
    Table("oe_demo_global_row", metadata, Column("id", Integer, primary_key=True), Column("label", String))
    return SimpleNamespace(metadata=metadata)


def _report(*, ready: bool, codes: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "ready_for_strict": ready,
        "blockers": [{"code": code, "table": _TENANT_TABLE} for code in codes],
        "tables": [],
    }


@pytest.fixture
def rls_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import rls_setup

    monkeypatch.setattr(rls_setup, "rls_enabled", lambda: True)


def _stub_readiness(monkeypatch: pytest.MonkeyPatch, report: dict[str, object]) -> None:
    from app.core import rls_setup

    async def _fake_report(conn: Any, tables: list[str], **kwargs: Any) -> dict[str, object]:
        return report

    monkeypatch.setattr(rls_setup, "rls_readiness_report", _fake_report)


def _created(conn: _Connection) -> list[str]:
    return [s for s in conn.executed if "CREATE POLICY" in s]


def _dropped(conn: _Connection) -> list[str]:
    return [s for s in conn.executed if "DROP POLICY" in s]


# ── the flag is refused until the database is ready ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_a_strict_boot_on_a_database_that_is_not_ready_provisions_the_permissive_policy_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing strict must not mean provisioning nothing.

    An operator who sets ``OE_RLS_STRICT`` is asking for enforcement. Answering
    with no policy at all would be worse than the permissive one already in
    place, so the refusal downgrades to permissive, says so in the stats, and
    logs the blockers that have to be cleared first.
    """
    from app.core import rls_setup

    _stub_readiness(monkeypatch, _report(ready=False, codes=("null_tenant_rows", "tenant_is_user")))
    conn = _Connection()

    stats = await rls_setup.apply_rls(conn, _base(), strict=True)

    assert stats["strict_refused"] == 2, "both blockers must be reported, not just the first"
    assert stats["tables"] == 1, "the permissive policy is still provisioned"
    created = _created(conn)
    assert len(created) == 1
    assert _PERMISSIVE in created[0], "the fallback must be the rollout predicate"
    assert _STRICT not in created[0]
    assert not _dropped(conn), "a downgrade is not a mode change, so nothing is dropped"


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_a_strict_boot_on_a_ready_database_installs_a_predicate_with_no_null_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import rls_setup

    _stub_readiness(monkeypatch, _report(ready=True))
    conn = _Connection()

    stats = await rls_setup.apply_rls(conn, _base(), strict=True)

    assert "strict_refused" not in stats
    assert stats["tables"] == 1
    created = _created(conn)
    assert len(created) == 1
    assert _STRICT in created[0]
    assert "IS NULL" not in created[0], "the whole point of strict is that the NULL arm is gone"
    # USING and WITH CHECK both carry it, so reads and writes are scoped alike.
    assert created[0].count(_STRICT) == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_strict_still_enables_and_forces_row_level_security(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strict changes the predicate only, not the ENABLE/FORCE hardening."""
    from app.core import rls_setup

    _stub_readiness(monkeypatch, _report(ready=True))
    conn = _Connection()

    await rls_setup.apply_rls(conn, _base(), strict=True)

    joined = "\n".join(conn.executed)
    assert f'ALTER TABLE "{_TENANT_TABLE}" ENABLE ROW LEVEL SECURITY' in joined
    assert f'ALTER TABLE "{_TENANT_TABLE}" FORCE ROW LEVEL SECURITY' in joined


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_strict_is_inert_while_enforcement_itself_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """``OE_RLS_STRICT`` alone changes nothing on a default install."""
    from app.core import rls_setup

    monkeypatch.setattr(rls_setup, "rls_enabled", lambda: False)
    monkeypatch.setattr(rls_setup, "strict_requested", lambda: True)
    conn = _Connection()

    stats = await rls_setup.apply_rls(conn, _base(), strict=True)

    assert stats == {"roles": 0, "tables": 0}, "the disabled-mode contract is unchanged"
    assert conn.executed == [], "no statement at all reaches the database"


# ── rollback is real, not just a flag ───────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_turning_strict_back_off_drops_the_strict_policy_so_the_rollback_is_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guarded ``CREATE POLICY`` would leave the strict predicate installed.

    ``_policy_statements`` only creates when ``pg_policies`` has no row for the
    policy name, so switching the flag off would otherwise be a no-op and
    NULL-tenant rows would stay hidden after the operator rolled back. The mode
    census makes the transition explicit: drop, then let the guarded create
    install the requested predicate.
    """
    from app.core import rls_setup

    conn = _Connection(policy_rows=[(_TENANT_TABLE, _STRICT)])

    stats = await rls_setup.apply_rls(conn, _base(), strict=False)

    dropped = _dropped(conn)
    assert len(dropped) == 1, "the strict policy has to go before the create runs"
    assert f'ON "{_TENANT_TABLE}"' in dropped[0]
    assert "oe_tenant_isolation" in dropped[0]
    assert stats["policies_replaced"] == 1
    created = _created(conn)
    assert len(created) == 1 and _PERMISSIVE in created[0]
    # The drop has to precede the create, or the guard finds the old policy and
    # the create does nothing.
    assert conn.executed.index(dropped[0]) < conn.executed.index(created[0])


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_switching_to_strict_replaces_an_installed_permissive_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import rls_setup

    _stub_readiness(monkeypatch, _report(ready=True))
    conn = _Connection(policy_rows=[(_TENANT_TABLE, _PERMISSIVE)])

    stats = await rls_setup.apply_rls(conn, _base(), strict=True)

    assert stats["policies_replaced"] == 1
    assert len(_dropped(conn)) == 1
    assert _STRICT in _created(conn)[0]


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_a_policy_already_in_the_requested_mode_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No churn on the common boot: the mode matches, so nothing is dropped."""
    from app.core import rls_setup

    conn = _Connection(policy_rows=[(_TENANT_TABLE, _PERMISSIVE)])

    stats = await rls_setup.apply_rls(conn, _base(), strict=False)

    assert not _dropped(conn)
    assert stats["policies_replaced"] == 0
    assert stats["tables"] == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_a_policy_whose_mode_cannot_be_read_is_never_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognised ``qual`` means "leave it alone", not "replace it".

    A policy written by an administrator or by another release is not this
    code's to drop. Guessing its mode in order to recreate it is how live rows
    get hidden or exposed, so an unreadable catalog answer provokes no DDL.
    """
    from app.core import rls_setup

    for qual in (None, "", "(something_else = 1)"):
        conn = _Connection(policy_rows=[(_TENANT_TABLE, qual)])

        stats = await rls_setup.apply_rls(conn, _base(), strict=False)

        assert not _dropped(conn), f"qual={qual!r} must not be classified"
        assert stats["policies_replaced"] == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("rls_on")
async def test_an_unreadable_policy_catalog_does_not_stop_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The census is an optimisation, so its failure must not be fatal.

    A login without catalog rights, or a connection that cannot answer the
    query, yields "nothing known" rather than an exception: provisioning still
    applies the guarded create, exactly as it did before the census existed.
    """
    from app.core import rls_setup

    class _Refusing(_Connection):
        async def execute(self, statement: Any, params: Any = None) -> _Result:
            sql = str(statement)
            self.executed.append(sql)
            if _CENSUS in sql:
                raise RuntimeError("permission denied for catalog")
            return _Result()

    conn = _Refusing()

    stats = await rls_setup.apply_rls(conn, _base(), strict=False)

    assert stats["tables"] == 1, "provisioning continues"
    assert stats["policies_replaced"] == 0
    assert len(_created(conn)) == 1


# ── the predicates themselves ───────────────────────────────────────────────


def test_the_permissive_statements_are_unchanged_by_the_arrival_of_strict() -> None:
    """Pin the rollout SQL so strict cannot quietly rewrite the default path.

    An install that never sets ``OE_RLS_STRICT`` must provision byte-identically
    what it did before, which is what makes this an additive change rather than
    a behaviour change for every existing deployment.
    """
    from app.core.rls_setup import _policy_statements

    statements = _policy_statements(_TENANT_TABLE)

    assert len(statements) == 3
    assert statements[0] == f'ALTER TABLE "{_TENANT_TABLE}" ENABLE ROW LEVEL SECURITY'
    assert statements[1] == f'ALTER TABLE "{_TENANT_TABLE}" FORCE ROW LEVEL SECURITY'
    assert "IF NOT EXISTS" in statements[2], "the create stays guarded, so it never replaces"
    assert f'CREATE POLICY "oe_tenant_isolation" ON "{_TENANT_TABLE}"' in statements[2]
    assert _PERMISSIVE in statements[2]
    assert statements[2].count(_PERMISSIVE) == 2, "USING and WITH CHECK are scoped alike"


def test_the_strict_predicate_fails_closed_for_an_anonymous_caller() -> None:
    """An anonymous request binds an empty GUC, and no row may match it.

    The permissive predicate's ``IS NULL`` arm would still hand an anonymous
    caller every NULL-tenant row. Strict has exactly one arm, comparing the
    column to ``current_setting``, so an empty string matches nothing and the
    policy denies rather than widening to "all rows".
    """
    from app.core.rls_setup import permissive_predicate, strict_predicate

    strict = strict_predicate()

    assert strict == _STRICT
    assert "IS NULL" not in strict
    assert " OR " not in strict, "one comparison arm only"
    assert GUC_NAME in strict
    assert strict != permissive_predicate()


def test_policy_is_strict_reads_the_mode_off_a_deparsed_qual() -> None:
    """PostgreSQL deparses ``qual``, so the discriminator is the NULL arm."""
    from app.core.rls_setup import policy_is_strict

    assert (
        policy_is_strict(
            "(tenant_id IS NULL OR ((tenant_id)::text = current_setting('app.current_tenant'::text, true)))"
        )
        is False
    )
    assert policy_is_strict("((tenant_id)::text = current_setting('app.current_tenant'::text, true))") is True
    assert policy_is_strict(None) is None
    assert policy_is_strict("") is None
    assert policy_is_strict("(other_column = 1)") is None


def test_strict_readiness_verdict_refuses_a_report_it_does_not_understand() -> None:
    """Only an explicit ``ready_for_strict is True`` is accepted."""
    from app.core.rls_setup import strict_readiness_verdict

    assert strict_readiness_verdict({"ready_for_strict": True}) == (True, [])
    assert strict_readiness_verdict({}) == (False, ["readiness_unknown"])
    assert strict_readiness_verdict({"ready_for_strict": None}) == (False, ["readiness_unknown"])
    assert strict_readiness_verdict({"ready_for_strict": "yes"}) == (False, ["readiness_unknown"])

    ok, codes = strict_readiness_verdict(
        {"ready_for_strict": False, "blockers": [{"code": "null_tenant_rows"}, {"code": "tenant_is_user"}]}
    )
    assert ok is False
    assert codes == ["null_tenant_rows", "tenant_is_user"], "every blocker is named, sorted"


def test_a_blocker_without_a_code_is_still_reported() -> None:
    from app.core.rls_setup import strict_readiness_verdict

    ok, codes = strict_readiness_verdict({"ready_for_strict": False, "blockers": [{"table": "t"}, "not-a-dict"]})
    assert ok is False
    assert codes == ["unknown"], "a malformed blocker must not read as ready"
