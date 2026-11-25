# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""An event-triggered agent run reads costs as its creator, like every other run.

Three paths start agent work without a request around them: the HTTP background
task, the scheduler tick, and the event bus. Each starts a run that belongs to a
particular user, and each therefore has to bind ``cost_scope.caller_scope`` so the
run's cost reads are scoped to that user's catalogue rather than to everything the
process can see.

Two of the three did. ``events._fire_subscribed_agents`` fired subscribed agents
after correctly checking that each creator may see the event's project - the
authorization was there - but started the run without the scope binding, so the
same agent read a wider cost catalogue when an event fired it than when its own
schedule did. Nothing raised and no test failed; the two runs simply disagreed.

The first test below pins the structural rule, because it is the part that regresses
silently: a new dispatch path added without the binding behaves identically in every
other respect. The second pins what the binding actually means, so the rule is not
just a shape.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

from app.core import cost_scope


def _calls_inside_a_caller_scope(tree: ast.AST) -> set[int]:
    """Ids of every call node lexically inside a ``with cost_scope.caller_scope(...)``."""
    scoped: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        binds_caller = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Attribute)
            and item.context_expr.func.attr == "caller_scope"
            for item in node.items
        )
        if not binds_caller:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                scoped.add(id(child))
    return scoped


def test_every_agent_run_dispatch_binds_the_caller_scope() -> None:
    """No path may start an agent run outside the scope of the user it runs as."""
    app_root = Path(__file__).resolve().parents[2] / "app"
    dispatches: list[str] = []
    unscoped: list[str] = []

    for path in sorted(app_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "start_run(" not in source:
            continue
        tree = ast.parse(source, filename=str(path))
        scoped = _calls_inside_a_caller_scope(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "start_run":
                continue
            where = f"{path.relative_to(app_root.parent)}:{node.lineno}"
            dispatches.append(where)
            if id(node) not in scoped:
                unscoped.append(where)

    # The two dispatch paths that exist today: the scheduler tick and the event
    # bus. If this count ever drops the scan has stopped finding them, which
    # would make the assertion above vacuously true.
    assert len(dispatches) >= 2, f"expected the known dispatch sites, found {dispatches}"
    assert unscoped == [], f"agent run started outside a caller scope: {unscoped}"


def test_the_bound_scope_is_the_identity_a_run_reads_as() -> None:
    """What the binding means: inside it the caller is that user, outside it nobody."""
    creator = uuid.uuid4()

    assert cost_scope.current_caller() is None
    with cost_scope.caller_scope(creator):
        assert cost_scope.current_caller() == creator
        # A string id binds to the same identity, which is what a dispatch path
        # holding ``str(user_id)`` gets.
        with cost_scope.caller_scope(str(creator)):
            assert cost_scope.current_caller() == creator
    assert cost_scope.current_caller() is None
