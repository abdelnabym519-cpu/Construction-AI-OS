# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""No route resolves a tenant from the token's subject.

Phase 02b removed the fall-through from :func:`app.core.tenant_scope.resolve_tenant_id`
and made the door refuse an access token with no ``tenant_id`` claim. That is only half
of it. Two modules carried their own copy of the old rule - a private
``_tenant_id_from_payload`` that preferred the claim and then fell back to ``sub`` - so
their routes answered a question the door had already refused, and answered it with a
person's id where a company's id belongs.

Twenty-six call sites in the dashboards router and four in the compliance router, so
this is not one forgotten line: it is the rule the two modules were written against.

The consequence is not theoretical. ``SnapshotRepository.get`` filters on
``tenant_id == str(tenant_id)``, and a resolver that returns the caller's own id makes
every snapshot that predates the backfill - stamped with a user id, as
``v231_contact_tenant_id`` stamped contacts - readable by its owner and by nobody else,
which is exactly the ``tenant_is_user`` shape the readiness report refuses to enforce
over. Worse, it disagrees with the door: the same credential is refused on one route and
silently reinterpreted on another.

These tests pin the source contract rather than the behaviour, because the behaviour
cannot be exercised here: importing ``app.modules.dashboards.router`` pulls in
``cad2data_bridge``, which imports pandas, which imports ``ctypes``, and a sandbox
interpreter built without ``_ctypes`` cannot get past it. The route-level evidence lives
in ``tests/integration/test_tenant_isolation.py``, which mounts the module on a runner
that has a normal CPython. What is pinned here is that the fallback is gone, that every
tenant in these two routers comes from the shared resolver, and that the only functions
left under ``app/`` whose name says tenant and whose body reads ``sub`` are the two that
read it as an actor and not as a tenant.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_DASHBOARDS = "modules/dashboards/router.py"
_COMPLIANCE = "modules/compliance/router.py"

#: Functions whose name contains ``tenant`` and whose body reads the constant
#: ``sub``, and which are nevertheless correct. Both read the subject as the
#: *actor* - who is asking - and never as the tenant - whose data it is. The list
#: is the reviewable claim: growing it means somebody found a new place where a
#: person's id is used as a company's id, and that has to be argued, not appended.
_ALLOWED_SUBJECT_READS = {
    # Passes ``payload["sub"]`` as ``actor_id`` to ``tenant_scope_owner``. The
    # tenant comes from ``CurrentTenantId``, which is the claim or nothing.
    "app/dependencies.py::tenant_scoped_owner_filter",
    # Resolves the tenant from the *project's owner*, and reads ``sub`` to decide
    # whether the caller may see that project at all. Listed for Phase 02c, not
    # allowed forever: "the project owner is the tenant boundary" is a second
    # ownership model, and pointcloud scans are stamped with a person's id
    # because of it.
    "app/modules/pointcloud/service.py::_resolve_tenant_and_verify",
}


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _body_without_docstring(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """The function's body, minus a leading docstring.

    Docstrings are excluded on purpose: half the modules in this repository explain
    the removed ``sub`` fallback in prose, and prose that says "there is no
    fall-through to sub" must not be what fails the check.
    """
    first = fn.body[0] if fn.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return fn.body[1:]
    return fn.body


def _reads_subject(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    scope = ast.Module(body=_body_without_docstring(fn), type_ignores=[])
    return any(isinstance(n, ast.Constant) and n.value == "sub" for n in ast.walk(scope))


def _census() -> list[str]:
    """Every ``app/`` function named like a tenant resolver that reads ``sub``."""
    found: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        relative = path.relative_to(_APP_ROOT.parent).as_posix()
        if "/tests/" in f"/{relative}" or path.name.startswith("test_"):
            continue  # in-module test files assert on payloads; they resolve nothing
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - every app file parses on CI's 3.12
            continue
        for fn in _functions(tree):
            if "tenant" not in fn.name.lower():
                continue
            if _reads_subject(fn):
                found.append(f"{relative}::{fn.name}")
    return sorted(found)


def test_the_only_tenant_named_functions_reading_the_subject_are_actor_reads() -> None:
    """The whole product, in one census.

    Before the fix this named four functions: the two private
    ``_tenant_id_from_payload`` copies in the dashboards and compliance routers, and
    the two actor reads that are allowed. Now it names only the allowed two, so a new
    resolver that reaches for ``sub`` fails here the day it is written rather than the
    day somebody notices two routes disagreeing with the door.
    """
    assert _census() == sorted(_ALLOWED_SUBJECT_READS), _census()


@pytest.mark.parametrize("relative", [_DASHBOARDS, _COMPLIANCE])
def test_the_router_has_no_private_tenant_resolver(relative: str) -> None:
    """The duplicate is deleted, not corrected.

    A second resolver that happens to agree with the first today is still a second
    place to forget when the rule changes, which is how these two came to exist.
    """
    tree = ast.parse((_APP_ROOT / relative).read_text(encoding="utf-8"))
    names = {fn.name for fn in _functions(tree)}
    assert "_tenant_id_from_payload" not in names, names & {"_tenant_id_from_payload"}


@pytest.mark.parametrize(
    ("relative", "expected_sites"),
    [(_DASHBOARDS, 26), (_COMPLIANCE, 4)],
)
def test_every_tenant_the_router_resolves_comes_from_the_shared_resolver(relative: str, expected_sites: int) -> None:
    """Each route asks :func:`resolve_tenant_id`, and the count is pinned.

    The count matters, and it is pinned per file: the dashboards router resolves a
    tenant in twenty-six places and the compliance router in four. A resolver replaced in
    most of them would leave the rest answering a question the door had already refused,
    and a test that only checked "some call exists" would not notice. If the number
    changes, this fails and the change is a decision somebody writes down.
    """
    source = (_APP_ROOT / relative).read_text(encoding="utf-8")
    assert "from app.core.tenant_scope import" in source, relative
    assert "resolve_tenant_id" in source, relative

    tree = ast.parse(source)
    resolved = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not any("tenant" in name for name in targets):
            continue
        call = node.value if isinstance(node.value, ast.Call) else None
        called = call.func.attr if call and isinstance(call.func, ast.Attribute) else (call.func.id if call else "")
        assert called == "resolve_tenant_id", (relative, node.lineno, called)
        resolved += 1
    assert resolved == expected_sites, (relative, resolved)


def test_the_shared_resolver_answers_none_for_a_credential_with_no_claim() -> None:
    """The behaviour both routers now inherit, stated where the census points.

    A payload with a subject and no claim is what a pre-Phase-02b token looks like.
    The answer is ``None`` - no tenant - and not the subject. ``None`` is fail-closed
    downstream: ``SnapshotRepository.get`` filters on equality, so a snapshot is not
    found rather than found by accident.
    """
    from app.core.tenant_scope import SUBJECT_CLAIM, TENANT_CLAIM, resolve_tenant_id

    legacy = {SUBJECT_CLAIM: "6f1c1c1e-0d5b-4a5f-9d5f-1c1e0d5b4a5f", "role": "admin", "type": "access"}
    assert resolve_tenant_id(legacy) is None

    current = {**legacy, TENANT_CLAIM: "0d5b4a5f-9d5f-1c1e-0d5b-4a5f9d5f1c1e"}
    assert resolve_tenant_id(current) == current[TENANT_CLAIM]
