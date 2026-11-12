# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Which paths bind a tenant, and which still run unpolicied.

:mod:`app.core.rls` decides whether a transaction is policied by a sentinel: a
session whose tenant context variable was never bound keeps the role the connection
arrived with, which on a default install is the cluster superuser, and a superuser
bypasses every policy even with ``FORCE ROW LEVEL SECURITY``.

This file used to be named
``test_the_rls_tenant_binding_has_one_call_site_so_background_work_is_unpolicied``,
and at the time that was the truth: the only thing that bound a tenant was the global
request dependency, so every path that opened a session outside a request - the three
agent run paths, the scheduler, the event bus, the job runner, the startup seeds - was
unpolicied. Phase 02b changed that for one group and left the rest, so the name would
have been a lie and the single-call-site assertion would have failed for the reason it
was written to detect. Both are corrected here rather than deleted, because the pin is
what tells the next person that the platform's behaviour moved:

* :func:`app.core.rls.bound_tenant` is a second binder, and the three agent run paths
  use it, resolving the tenant from the user who started the run and passing it in
  explicitly rather than inheriting ambient state.
* Everything else that starts work outside a request is still unpolicied, and is named
  below as the Phase 02c list. The listener's own remedy still applies to them: run
  trusted system work under the ``BYPASSRLS`` role.

The observed consequence - which role each shape of session actually runs as, and what
it can then read - is measured against a real database in the PostgreSQL lane by
``tests/pg/test_a_background_session_keeps_the_connecting_role_and_is_not_policied.py``.
No database is needed here, which is why this file is in the unit lane: the invariant
should fail everywhere and not only when ``OE_TEST_DB=pg`` is set.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core import rls

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: Paths that start agent or job work outside a request and now bind the tenant of
#: whoever started the run. Phase 02b; the binding is asserted below, not assumed.
_TENANT_BOUND_RUN_PATHS = (
    "modules/ai_agents/router.py",
    "modules/ai_agents/service.py",
    "modules/ai_estimator/service.py",
)

#: Paths that start work outside a request and still bind nothing. This is the
#: Phase 02c list: each one keeps the connecting superuser role and therefore
#: bypasses every policy, and each needs either a tenant binding (when a person
#: started it) or an explicit decision to run under the BYPASSRLS system role.
_UNPOLICIED_RUN_PATHS = (
    "modules/ai_agents/scheduler.py",
    "modules/ai_agents/events.py",
    "core/job_runner.py",
    "modules/pointcloud/seed.py",
    "modules/value/seed.py",
    "modules/property_dev/service.py",
)


def _call_sites_of(name: str) -> list[str]:
    """Every ``name(...)`` call site under ``app/``, as ``path:line``."""
    found: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if name not in source:
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:  # pragma: no cover - every app file parses on CI's 3.12
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if called == name:
                found.append(f"{path.relative_to(_APP_ROOT.parent).as_posix()}:{node.lineno}")
    return found


def _called_name(node: ast.expr) -> str:
    """The function name a call node addresses, for attribute or plain calls."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return getattr(node.func, "id", "")
    return ""


def test_the_tenant_binding_has_exactly_two_call_sites_and_both_are_named() -> None:
    """Two binders exist: the request door, and the context manager it shares.

    Asserted by file rather than by line, so moving a call inside either file is not a
    failure and adding a third binder is. A third binder is how a path quietly starts
    deciding its own tenant instead of being handed one.
    """
    call_sites = _call_sites_of("set_request_tenant")
    assert [site.split(":")[0] for site in call_sites] == ["app/core/rls.py", "app/dependencies.py"], call_sites


def test_the_only_binder_inside_rls_is_the_context_manager() -> None:
    """``bound_tenant`` is the one thing in :mod:`app.core.rls` that binds.

    If the listener or the installer ever bound a tenant itself, the sentinel that
    distinguishes "no request" from "anonymous request" would stop meaning anything.
    """
    tree = ast.parse((_APP_ROOT / "core" / "rls.py").read_text(encoding="utf-8"))
    enclosing = [
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(fn)
        if _called_name(node) == "set_request_tenant"
    ]
    assert enclosing == ["bound_tenant"], enclosing


@pytest.mark.parametrize("relative", _TENANT_BOUND_RUN_PATHS)
def test_an_agent_run_path_binds_the_tenant_of_the_user_who_started_it(relative: str) -> None:
    """The binding is explicit, resolved from the initiator, and wraps the run.

    Three things are pinned, because each can be lost independently:

    * the path resolves the organization through ``active_organization_id``, which is
      the initiator's membership and not ambient state;
    * it uses the ``bound_tenant`` context manager rather than the raw primitive, so
      the binding is undone even when the run raises;
    * the ``runner.run(...)`` call is inside that ``async with`` block, so the binding
      is still held when the tools open their sessions.
    """
    source = (_APP_ROOT / relative).read_text(encoding="utf-8")
    assert "active_organization_id" in source, relative
    assert "set_request_tenant" not in source, f"{relative} must bind through bound_tenant, not the primitive"

    tree = ast.parse(source, filename=relative)
    runs_inside_a_binding = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        if not any(_called_name(item.context_expr) == "bound_tenant" for item in node.items):
            continue
        runs_inside_a_binding += sum(1 for call in ast.walk(node) if _called_name(call) == "run")
    assert runs_inside_a_binding >= 1, f"{relative} has a bound_tenant block that wraps no run"


@pytest.mark.parametrize("relative", _UNPOLICIED_RUN_PATHS)
def test_the_remaining_background_paths_still_bind_nothing(relative: str) -> None:
    """Named rather than implied: these are the paths Phase 02c is about.

    A pass here is a statement that the gap is still open, on purpose, and that nobody
    has closed it for one path without updating the list. Binding one of them is a
    change worth making - it just has to be a deliberate one, so this test then fails
    and the path moves to :data:`_TENANT_BOUND_RUN_PATHS`.
    """
    source = (_APP_ROOT / relative).read_text(encoding="utf-8")
    assert "set_request_tenant" not in source, relative
    assert "bound_tenant" not in source, relative


def test_the_paths_that_charge_costs_still_bind_the_caller() -> None:
    """What made the missing tenant binding a decision rather than an omission.

    These paths already bound the caller for cost visibility, so they had thought about
    scope; the tenant is the other half of the same scope. The two service-level run
    paths are not in this list: they charge costs through the session they are handed
    rather than binding a caller scope of their own, so asserting ``caller_scope`` in
    them would be asserting something the code does not do.
    """
    for relative in ("modules/ai_agents/router.py", "modules/ai_agents/scheduler.py", "modules/ai_agents/events.py"):
        source = (_APP_ROOT / relative).read_text(encoding="utf-8")
        assert "caller_scope" in source, relative


async def test_binding_a_tenant_holds_it_for_the_block_and_gives_it_back() -> None:
    """The sentinel, not the value, is what decides whether a session is policied."""
    assert rls._request_tenant.get() is rls._UNSET  # noqa: SLF001 - the sentinel is the point

    async with rls.bound_tenant("company-1") as bound:
        assert bound == "company-1"
        assert rls._request_tenant.get() == "company-1"  # noqa: SLF001
        assert rls.current_request_tenant() == "company-1"

    assert rls._request_tenant.get() is rls._UNSET  # noqa: SLF001
    assert rls.current_request_tenant() is None


async def test_a_run_with_no_organization_binds_none_rather_than_nothing() -> None:
    """``None`` is a binding, and it is not the same as not binding.

    ``None`` stamps an empty ``app.current_tenant``, which a fail-closed policy matches
    nothing against: a run whose initiator belongs to no organization sees no tenant
    rows. Leaving the context unbound would instead keep the connecting superuser and
    see every company's rows, which is the one of the two answers that is not safe to
    be wrong about.
    """
    async with rls.bound_tenant(None):
        assert rls._request_tenant.get() is None  # noqa: SLF001
        assert rls._request_tenant.get() is not rls._UNSET  # noqa: SLF001
        assert rls.current_request_tenant() is None

    assert rls._request_tenant.get() is rls._UNSET  # noqa: SLF001


async def test_a_nested_binding_restores_the_outer_tenant() -> None:
    """A run started inside a request must not leak its tenant into the request."""
    async with rls.bound_tenant("outer-company"):
        async with rls.bound_tenant("inner-company"):
            assert rls.current_request_tenant() == "inner-company"
        assert rls.current_request_tenant() == "outer-company"


def test_the_listener_leaves_an_unbound_session_on_the_connecting_role() -> None:
    """The code path that makes the tests above mean something.

    Read rather than executed, because executing it needs a cluster: an unbound context
    variable returns before any role change, and a bound one - including the ``None`` an
    anonymous request binds - downgrades the transaction and then sets the GUC. A
    failure to downgrade must propagate, which the module states and this asserts is not
    swallowed by a handler around it.
    """
    source = (_APP_ROOT / "core" / "rls.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    listener = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_scope_transaction"
    )
    body = ast.dump(listener)
    assert "_UNSET" in body, "the listener must test the sentinel"
    assert "SET LOCAL ROLE" in body, "the listener must downgrade the transaction"
    assert "set_config" in body, "the listener must bind the GUC"
    # No try/except around the downgrade: a failure has to abort the transaction
    # rather than let the rest of it run as the superuser.
    assert not [n for n in ast.walk(listener) if isinstance(n, ast.Try)], "the downgrade must not be guarded"
