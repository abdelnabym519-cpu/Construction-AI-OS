# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""What a boot does while the tenancy repair runs, and what it does not do.

The repair rewrites rows a user can see, so the two questions a reviewer has to answer
without reading a thousand lines of ``app/main.py`` are: does the boot wait for it, and
can it stop the boot. The answers are yes and no, and both are load-bearing in opposite
directions - waiting is what makes the data correct before the first request is served,
and being unable to stop the boot is what makes a database this code does not understand
unable to take the application down.

The shape this asserts is the one ``app/main.py`` actually has: an
``@app.on_event("startup")`` handler awaits ``_startup_impl``, which awaits the repair
before it calls ``module_loader.load_all``. Starlette runs every startup handler to
completion before the server accepts a request, which the file itself relies on in
several comments, so the repair is finished before routers mount and long before a socket
opens.

The measured half of this lives in
``tests/pg/test_the_tenancy_boot_repair_is_atomic_idempotent_and_locked.py`` and
``tests/pg/test_a_million_rows_are_rewritten_fast_enough_to_boot_on.py``: how long the
wait is, and how fast a worker that finds another one holding the advisory lock gives up
on waiting. What is pinned here is the ordering, because ordering is what makes those
measurements mean anything - a repair that ran after the lifespan yielded would be
equally fast and would serve requests against a tenant column it had not finished
rewriting.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[2] / "app" / "main.py"
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _tree() -> ast.Module:
    return ast.parse(_MAIN.read_text(encoding="utf-8"))


def _await_of(tree: ast.Module, name: str) -> ast.Await:
    """The ``await name(...)`` in the file, or a loud failure if there is not one."""
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", None) == name
    ]
    assert len(found) == 1, f"expected exactly one `await {name}(...)`, found {len(found)}"
    return found[0]


def _enclosing(tree: ast.Module, lineno: int) -> ast.AsyncFunctionDef:
    """The innermost function containing a line."""
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, _FUNCTIONS) and node.lineno <= lineno <= (node.end_lineno or node.lineno)
    ]
    assert candidates, f"line {lineno} is not inside a function"
    return max(candidates, key=lambda node: node.lineno)


def test_the_repair_runs_before_the_module_loader_mounts_any_router() -> None:
    """Routers mount after the tenant column has been rewritten, not during.

    A module's ``on_startup`` hook can read the data this repair fixes - formwork's
    does - and every route the loader mounts is a route that will answer from the
    tenant column. Both are reasons the repair has to be earlier in the same
    coroutine, and neither is visible in a diff of the repair itself.
    """
    tree = _tree()
    repair = _await_of(tree, "run_data_repairs")
    startup = _enclosing(tree, repair.lineno)

    loader = [
        node
        for node in ast.walk(startup)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "load_all"
    ]
    assert loader, "the startup coroutine no longer calls module_loader.load_all"
    assert repair.lineno < min(node.lineno for node in loader), (
        f"the repair is awaited at line {repair.lineno}, after the module loader at "
        f"{min(node.lineno for node in loader)}"
    )


def test_the_repair_runs_inside_a_startup_handler_and_not_in_a_task() -> None:
    """Awaited inside a handler Starlette runs before it accepts a connection.

    The chain is: ``@app.on_event("startup")`` registers ``startup``, which awaits
    ``_startup_impl``, which awaits the repair. Starlette runs every startup handler
    to completion before the server accepts a request - ``app/main.py`` relies on that
    in several places, including the comment that uvicorn binds only after startup
    returns - so a repair awaited inside one is finished before the first route answers.

    What this rules out is the shape that would look identical in a log and behave
    completely differently: ``create_task(_startup_impl())``, which would let startup
    return, the socket open, and requests arrive while the tenant column still held
    people's ids. Asserted for every function in the chain, not just the last hop.
    """
    tree = _tree()
    startup_impl = _enclosing(tree, _await_of(tree, "run_data_repairs").lineno)

    callers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and getattr(node.value.func, "id", None) == startup_impl.name
    ]
    assert len(callers) == 1, f"expected exactly one await of {startup_impl.name}, found {len(callers)}"
    handler = _enclosing(tree, callers[0].lineno)

    registered = [
        decorator
        for decorator in handler.decorator_list
        if isinstance(decorator, ast.Call)
        and getattr(decorator.func, "attr", "") == "on_event"
        and any(isinstance(arg, ast.Constant) and arg.value == "startup" for arg in decorator.args)
    ]
    assert registered, (
        f"{handler.name} awaits {startup_impl.name} but is not registered with @app.on_event('startup'), "
        "so nothing here proves it runs before the application serves"
    )

    for name in (handler.name, startup_impl.name):
        scheduled = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", "") in ("create_task", "ensure_future")
            and name in ast.unparse(node)
        ]
        assert not scheduled, f"{name} is also scheduled as a task at line {scheduled[0].lineno}"


def test_a_repair_that_fails_cannot_stop_the_boot() -> None:
    """Guarded twice over, because one guard is a contract and the other is a fact.

    ``run_data_repairs`` catches each repair individually and reports it, which the
    runner's own tests measure. The startup path wraps the call in a ``try`` anyway, so
    a failure in discovery, in the session factory, or in publishing the report - none
    of which the runner catches - still leaves the application serving. A boot that a
    repair could stop would turn a database this code does not understand into an
    outage, and the guard has to swallow rather than re-raise or it is decoration.
    """
    tree = _tree()
    repair = _await_of(tree, "run_data_repairs")
    startup = _enclosing(tree, repair.lineno)

    guarding = [
        node
        for node in ast.walk(startup)
        if isinstance(node, ast.Try)
        and node.lineno <= repair.lineno <= (node.end_lineno or node.lineno)
        and any(isinstance(child, ast.Await) for child in ast.walk(node))
    ]
    assert guarding, "the startup call to run_data_repairs is not inside a try"

    innermost = max(guarding, key=lambda node: node.lineno)
    re_raised = [
        child
        for handler in innermost.handlers
        for child in ast.walk(handler)
        if isinstance(child, ast.Raise) and child.exc is None
    ]
    assert not re_raised, "the guard re-raises, so it cannot protect the boot"
