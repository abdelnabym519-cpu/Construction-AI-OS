# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The tenant binding has one call site, so background work is unpolicied by design.

:mod:`app.core.rls` decides whether a transaction is policied by a sentinel: a
session whose tenant context variable was never bound keeps the role the
connection arrived with, which on a default install is the cluster superuser, and
a superuser bypasses every policy even with ``FORCE ROW LEVEL SECURITY``. The
only thing that binds a tenant is the global request dependency, so every path
that opens a session outside a request - the three agent run paths, the scheduler,
the event bus, the job runner, the startup seeds - is unpolicied.

That is a documented decision rather than an oversight, and the listener names its
own remedy: run trusted system work under the ``BYPASSRLS`` role. What these tests
add is the pin. Without them the shape of the gap is prose in a docstring, and
somebody scoping background work later, or adding a fourth agent run path, would
have nothing to tell them the platform's behaviour had changed. The observed
consequence - which role each shape of session actually runs as, and what it can
then read - is measured against a real database in the PostgreSQL lane by
``tests/pg/test_a_background_session_keeps_the_connecting_role_and_is_not_policied.py``.

No database is needed here, which is why this file is in the unit lane: the
invariant should fail everywhere and not only when ``OE_TEST_DB=pg`` is set.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: Paths that start agent or job work outside a request. Each binds the caller for
#: cost visibility and none of them binds a tenant.
_BACKGROUND_RUN_PATHS = (
    "modules/ai_agents/router.py",
    "modules/ai_agents/scheduler.py",
    "modules/ai_agents/events.py",
    "core/job_runner.py",
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


def test_the_tenant_binding_has_exactly_one_call_site_in_the_application() -> None:
    """One door binds a tenant, and it is the request dependency.

    Asserted by file rather than by line, so moving the call inside
    ``app/dependencies.py`` is not a failure and adding a second binder is.
    """
    call_sites = _call_sites_of("set_request_tenant")

    assert [site.split(":")[0] for site in call_sites] == ["app/dependencies.py"], call_sites


def test_the_background_run_paths_scope_costs_but_bind_no_tenant() -> None:
    """Named rather than implied: these are the paths the gap is about."""
    for relative in _BACKGROUND_RUN_PATHS:
        source = (_APP_ROOT / relative).read_text(encoding="utf-8")
        assert "set_request_tenant" not in source, relative

    # The three agent paths do bind the caller for cost visibility, which is what
    # makes the missing tenant binding a decision rather than an omission: they
    # thought about scope, and this is the scope the platform gives them.
    for relative in _BACKGROUND_RUN_PATHS[:3]:
        source = (_APP_ROOT / relative).read_text(encoding="utf-8")
        assert "caller_scope" in source, relative


def test_the_listener_leaves_an_unbound_session_on_the_connecting_role() -> None:
    """The code path that makes the two tests above mean something.

    Read rather than executed, because executing it needs a cluster: an unbound
    context variable returns before any role change, and a bound one - including
    the ``None`` an anonymous request binds - downgrades the transaction and then
    sets the GUC. A failure to downgrade must propagate, which the module states
    and this asserts is not swallowed by a handler around it.
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
