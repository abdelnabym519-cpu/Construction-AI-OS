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

#: Functions that read the subject *and* hand a tenant to somebody else, where the
#: tenant they hand over is not the subject. Both are correct, and both are listed
#: because the census below cannot tell a correct one from a defect by name: it
#: catches the shape, and the shape includes a route that reads ``sub`` for its actor
#: and resolves its tenant properly on the next line.
_TENANT_ARGUMENTS_THAT_ARE_NOT_FED_BY_THE_SUBJECT = {
    # ``actor_id`` comes from ``payload["sub"]`` and ``tenant_id`` from
    # ``payload["tenant_id"]``, in the same call and never confused: the subject is
    # who is asking and the claim is whose data it is. This is the audit context
    # every request carries, so it is the one place the two are read side by side
    # on purpose.
    "app/dependencies.py::audit_context_dep",
    # Reads ``sub`` into ``user_id`` for the analytics call's actor, and takes its
    # tenant from ``_payload_tenant_id``, which reads the ``tenant_id`` and
    # ``org_id`` claims and answers ``None`` when neither is there - no fall-through
    # to the subject. A non-administrator with no claim gets an empty result set
    # rather than somebody else's, which is the fail-closed answer.
    "app/modules/property_dev/router.py::dashboard_broker_performance",
}

#: Functions that hand a *person* over where a company belongs. These are not
#: allowed and not blessed: they are open defects, listed here because a defect
#: nobody can see is worse than a defect on a list, and because the list is
#: two-way ratcheted - fixing one removes it from the census and leaving it on the
#: list after fixing it fails the same test.
#:
#: Both take ``caller = payload.get("sub")`` and pass ``tenant_id=str(caller)`` into
#: a service that writes it onto a row and uses it to scope a contact lookup. That
#: is the ``tenant_is_user`` shape ADR 005 spent a repair fixing, on a live route,
#: and the service docstring still documents it as intended: "``tenant_id``:
#: caller's user id". ``ROADMAP_STATUS.md``'s Phase 02c table names one property_dev
#: site, ``service.py:3562``; the same defect has these two route sites above it and
#: a third below it, where the ``Lead`` row's own tenant is taken from the request
#: body. Fixing them is not a two-line edit and is not done here: the service uses
#: the value to scope a find-or-create in the contacts bridge, so changing the key
#: from a person to a company changes which existing contacts are found, and that
#: needs a characterization test pinning both directions first - which is exactly
#: how the procurement_concept half of this same table was closed.
_OPEN_DEFECTS_THAT_PASS_A_PERSON_WHERE_A_TENANT_BELONGS = {
    "app/modules/property_dev/router.py::create_buyer",
    "app/modules/property_dev/router.py::create_lead",
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


def _passes_a_tenant_argument(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether this function hands a tenant to somebody else as a keyword argument."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg is not None and "tenant" in keyword.arg.lower():
                    return True
    return False


def _tenant_argument_census() -> list[str]:
    """Every ``app/`` function that reads ``sub`` and passes a tenant as an argument.

    The census above asks whether a function *named* like a tenant resolver reads the
    subject. This one asks whether a function hands a tenant to somebody else while
    reading the subject at all, which is the same mistake in the place a name-shaped
    census cannot see. A route called ``create_lead`` resolves nothing and is named
    nothing, and it passed ``payload["sub"]`` as ``tenant_id`` for as long as the only
    census was the name-shaped one - which is to say the whole time, since Phase 02b
    deleted the two resolvers this file was written about.
    """
    found: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        relative = path.relative_to(_APP_ROOT.parent).as_posix()
        if "/tests/" in f"/{relative}" or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - every app file parses on CI's 3.12
            continue
        for fn in _functions(tree):
            if _reads_subject(fn) and _passes_a_tenant_argument(fn):
                found.append(f"{relative}::{fn.name}")
    return sorted(found)


def _names_fed_by_the_subject(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The local names this function assigns out of a read of the token's subject."""
    fed: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        reads_subject = any(isinstance(n, ast.Constant) and n.value == "sub" for n in ast.walk(node.value))
        if not reads_subject:
            continue
        fed.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return fed


def _tenant_arguments_fed_by_the_subject(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """The tenant keyword arguments whose value traces back to a read of ``sub``.

    One assignment of indirection, which is all this codebase uses: the routes that
    are wrong assign ``caller = payload.get("sub")`` and then pass ``str(caller)``,
    and the routes that are right read ``sub`` into an actor name and pass a tenant
    that came from a claim instead. Following the name is what separates those two
    without a human deciding which list a new site belongs on.
    """
    fed = _names_fed_by_the_subject(fn)
    if not fed:
        return []
    traced: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg is None or "tenant" not in keyword.arg.lower():
                continue
            used = {n.id for n in ast.walk(keyword.value) if isinstance(n, ast.Name)}
            if used & fed:
                traced.append(keyword.arg)
    return traced


def _subject_fed_tenant_census() -> list[str]:
    """Every ``app/`` function that passes the token's subject as a tenant."""
    found: list[str] = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        relative = path.relative_to(_APP_ROOT.parent).as_posix()
        if "/tests/" in f"/{relative}" or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - every app file parses on CI's 3.12
            continue
        for fn in _functions(tree):
            if _tenant_arguments_fed_by_the_subject(fn):
                found.append(f"{relative}::{fn.name}")
    return sorted(found)


def test_every_function_that_hands_over_a_tenant_while_reading_the_subject_is_accounted_for() -> None:
    """The wide net: four functions, each on one of the two lists below.

    Two read the subject as an actor and take their tenant from a claim. Two pass the
    subject itself as the tenant. A fifth site fails here the day it is written, and a
    site that stops reading the subject fails here the day it is fixed, so neither list
    can quietly grow stale.
    """
    census = _tenant_argument_census()
    accounted = (
        _TENANT_ARGUMENTS_THAT_ARE_NOT_FED_BY_THE_SUBJECT | _OPEN_DEFECTS_THAT_PASS_A_PERSON_WHERE_A_TENANT_BELONGS
    )
    assert census == sorted(accounted), census


def test_the_two_lists_are_a_classification_the_reader_derives_and_not_one_somebody_wrote() -> None:
    """The narrow net, derived: which of those four actually feed a tenant from ``sub``.

    This is the assertion that keeps the lists honest. The classification above is prose
    and could be written to say anything; this follows the assignment from
    ``payload.get("sub")`` into the tenant keyword and names what it reaches, so the two
    lists are checked against each other rather than each against my reading of them.
    """
    derived = _subject_fed_tenant_census()
    assert derived == sorted(_OPEN_DEFECTS_THAT_PASS_A_PERSON_WHERE_A_TENANT_BELONGS), derived
    assert not (set(derived) & _TENANT_ARGUMENTS_THAT_ARE_NOT_FED_BY_THE_SUBJECT), derived


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
