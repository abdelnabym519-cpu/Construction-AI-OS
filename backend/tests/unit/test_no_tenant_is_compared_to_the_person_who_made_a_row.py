"""No tenant is compared to the person who made the row, unless somebody wrote down why.

``tenant_id`` names an organization (ADR 005) and ``owner_id`` / ``created_by``
name a person. Comparing one with the other is the defect the readiness report
calls ``tenant_is_user``, and it does not look like a defect where it is
written: it looks like a scope clause that happens to work, because on a
database whose rows were written before organizations existed the tenant column
*does* hold a person's id, so the comparison finds them. It stops finding them
the day a backfill points those columns at organizations, and it quietly keeps
excluding colleagues from each other's rows until somebody notices.

So the shape is worth a gate rather than a review, and the gate is a ratchet in
the same sense as ``scripts/check_security_class_ratchets.py``: every site that
exists is in ``ALLOWLIST`` with a reason written in prose, a site that is not in
the list fails the run, and an entry that no longer matches a site fails it too.
The list only shrinks, and shrinking it is a deliberate act - which is the point,
because two of the ten entries are the seeds ``ROADMAP_STATUS.md`` lists under
Phase 02c and closing that phase is what removes them.

WHAT IS DETECTED

Four shapes, found in the AST rather than by grepping for two words near each
other. A grep for proximity produces 45 hits in this tree, of which 36 are
keyword arguments, schema fields and prose - a list that long is not a gate, it
is noise everybody learns to skip. These four are the shapes where a value
actually meets a column:

  * ``compare`` - one operand names a tenant column and the other a person
    column: ``row.tenant_id == project.owner_id``.
  * ``assign`` - a tenant column given a person column's value, or the reverse:
    ``tenant_id = owner_id``.
  * ``kwarg`` - the same through a call: ``ActivityLog(tenant_id=owner_id)``, or
    ``Contact(created_by=tenant_id)``, which is the inverse shape and just as
    much a claim that the two are the same value.
  * ``sql`` - a comparison inside a string, which no AST walk can see the
    meaning of but which runs all the same: ``WHERE tenant_id = created_by``.
  * ``paired`` - one value compared against a tenant column in one clause and
    against a person column in a sibling clause inside the same call or boolean
    group: ``or_(Contact.tenant_id == owner, Contact.created_by == owner)``.
    Neither comparison is between the two columns, and together they are exactly
    the claim this file exists about: that one value could be either.

WHAT IS NOT DETECTED, DELIBERATELY

``Contact.tenant_id.is_(None)`` beside ``Contact.created_by == mine`` - in
``app/modules/module_builder/links.py`` - is not flagged, because a tenant
compared with NULL is not a tenant compared with a person: it says the row has no
tenant, and the person clause beside it is a fallback for rows that predate the
column. Flagging it would put an entry in the list that is not the shape the list
is about, and every such entry makes the real ones easier to skim past.

A value compared against ``user_id`` is not flagged either. That is the original
``tenant_is_user`` spelling and it is guarded elsewhere, by
``tests/unit/test_no_route_resolves_a_tenant_from_the_token_subject.py`` on the
resolver and by the readiness report on the data; widening this list to cover it
would double the entries without adding a shape anybody can act on.

RUNNING IT ON AN INTERPRETER OLDER THAN THE BACKEND

The backend is written for Python 3.12 and 25 of its files use syntax an older
interpreter cannot parse at all. Rather than fail on them, this guard falls back
to a textual scan of the same shapes for the files it cannot parse, and prints
how many it had to. Three of the four shapes survive that fallback; ``paired``
does not, because "two sibling clauses sharing an operand" is a fact about a tree
and not about a line, and approximating it with a regular expression would flag
every ``or_`` in the tree. So on an older interpreter this guard is a little
blinder than it is in CI, which runs 3.12 and 3.13 and parses everything. The
agreement test below is what keeps the two readers honest about the shapes they
share: over every file this interpreter *can* parse, the textual scan has to find
the same sites the AST finds, which is the evidence that the fallback is not
merely quieter.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

#: The columns that name an organization. ``organization_id`` is here as well as
#: ``tenant_id`` because both name the tenant in this schema - ADR 005's
#: migration wrote one and modules wrote the other - and a guard that covered
#: only the spelling it was written from would miss the other.
TENANT_COLUMNS = ("tenant_id", "organization_id")

#: The columns that name a person. Exactly the two the requirement names.
PERSON_COLUMNS = ("owner_id", "created_by")

#: A comparison inside a string of SQL, either way round.
_SQL_PATTERN = re.compile(
    r"(?:tenant_id|organization_id)\s*(?:=|==|!=|<>|IS)\s*[\"'\w.]*\b(?:owner_id|created_by)\b"
    r"|\b(?:owner_id|created_by)\b\s*(?:=|==|!=|<>|IS)\s*[\"'\w.]*(?:tenant_id|organization_id)"
)

#: The same three shapes the AST finds for ``compare``/``assign``/``kwarg``, for
#: files this interpreter cannot parse. ``paired`` has no textual equivalent and
#: is not attempted; see the module docstring.
_TEXTUAL_PATTERNS = (
    re.compile(
        r"[\w.]*(?:tenant_id|organization_id)[\w.]*\s*(?:==|!=|<>)[\w.\s\"']*(?:owner_id|created_by)\b"
        r"|\b(?:owner_id|created_by)\b[\w.]*\s*(?:==|!=|<>)[\w.\s\"']*(?:tenant_id|organization_id)"
    ),
    re.compile(
        # The optional ``\w+\(`` is a conversion wrapper: ``owner_id=str(tenant_id)``
        # is the same claim as ``owner_id=tenant_id`` and a reader that could not
        # see through the call would report the two differently.
        r"\b(?:tenant_id|organization_id)\s*=\s*(?:\w+\()?\s*[\"'\w.]*(?:owner_id|created_by)\b"
        r"|\b(?:owner_id|created_by)\s*=\s*(?:\w+\()?\s*[\"'\w.]*(?:tenant_id|organization_id)\b"
    ),
    _SQL_PATTERN,
)

#: Every site in the tree, with the reason it is allowed to be there. Keys are
#: ``path::kind::code``, built from the normalized source of the node rather than
#: its line number, so reformatting or moving a function does not silently
#: re-permit a site that has not been read again - and changing the code does
#: invalidate the entry, which is the point: a changed comparison is a new one.
ALLOWLIST: dict[str, str] = {
    "modules/contacts/repository.py::paired::or_(Contact.tenant_id == owner, Contact.created_by == owner)": (
        "The canonical legacy scope clause for contacts, and the one every other entry here copies. Rows written "
        "before ADR 005 carry a person's id in tenant_id and rows written before that carry no tenant at all, so "
        "the clause admits either spelling of 'mine'. contacts/models.py documents it as the scope clause the "
        "table is read through. It is a compatibility clause over historical rows and not a claim that a tenant "
        "is a person, but it is the shape this guard exists to make somebody write down."
    ),
    "modules/contacts/bridge.py::paired::or_(Contact.tenant_id == owner, Contact.created_by == owner)": (
        "The same clause inlined at two email-lookup sites in this module rather than imported from the contacts "
        "repository, for the same reason: a contact created before organizations existed is found by the person "
        "who made it. Two entries because the guard keys on the node and there are two nodes; one reason because "
        "it is one decision, and the decision is that the legacy clause stays until the rows it covers are gone."
    ),
    "modules/quality_concept/references.py::paired::or_(Contact.tenant_id == str(caller_id), "
    "Contact.created_by == str(caller_id))": (
        "The legacy contacts clause again, applied to a caller who is not an administrator so that quality "
        "references resolve only contacts that caller may see. Same historical rows, same compatibility reason, and "
        "the same consequence: it widens rather than narrows, so removing it would hide rows its readers expect."
    ),
    (
        "modules/contacts/lookup.py::paired::"
        'str(contact.tenant_id or "") != owner and '
        'str(contact.created_by or "") != owner'
    ): (
        "The negative form of the legacy clause: a contact is hidden unless the caller is named by either column. "
        "It is a refusal rather than a scope, so it fails closed for rows written after the backfill and open for "
        "the historical ones - the opposite bias to the clauses above, and the reason it needs its own entry "
        "rather than sharing one."
    ),
    "modules/contacts/bridge.py::kwarg::created_by=tenant_id": (
        "The inverse shape: a tenant written into the person column, so that a contact created after the change is "
        "still found by the legacy clause that reads both. Deliberate and load-bearing - without it a new contact "
        "would be visible to its tenant and invisible to the clause's other arm - but it is a claim that the two "
        "columns hold the same kind of value, which is what this guard refuses to let pass unread."
    ),
    "modules/design_management/service.py::kwarg::owner_id=str(tenant_id)": (
        "A tenant passed to a parameter named for a person. The repository's parameter feeds the legacy clause "
        "above, so the value is right and the name is wrong; renaming that parameter is a change to every caller "
        "of the contacts repository and belongs with the retirement of the clause, not beside it."
    ),
    "modules/pointcloud/seed.py::assign::tenant_id = owner_id": (
        "One of the two seeds ROADMAP_STATUS.md lists under Phase 02c as binding no tenant. The comment above it "
        "says the tenant boundary is the project owner, which is the resolution the service uses at register time, "
        "so a seeded scan is scoped like an uploaded one - true today and the reason it is allowed rather than "
        "fixed here. Closing Phase 02c removes this entry, and the two-way check below is what makes that removal "
        "a deliberate act instead of an edit nobody notices."
    ),
    "modules/pointcloud/service.py::assign::tenant_id = project.owner_id": (
        "The register-time resolution the seed above copies: a point cloud's tenant is taken from the project's "
        "owner, which is a person. oe_projects_project carries both owner_id and organization_id, so the "
        "organization is available at this call site and using it is the fix; it is not taken here because the "
        "rows already written were scoped by the person and changing the resolution would split one project's "
        "scans across two tenants. Named in ADR 006's section on what is not built yet."
    ),
    "modules/design_management/service.py::paired::contact is None or owner is None or owner not in "
    "(contact.tenant_id, contact.created_by)": (
        "The legacy contacts clause as a refusal rather than a scope: a contact resolves only if the caller's "
        "tenant is the value in either column, and anything else is a 404. One value accepted as either a tenant "
        "or the person who made the row is the claim this guard exists about, and it is written as a membership "
        "test on a tuple, which is why it is reported as paired rather than as a comparison of the two columns."
    ),
    "modules/value/seed.py::kwarg::tenant_id=owner_id": (
        "The second of the two seeds ROADMAP_STATUS.md lists under Phase 02c: value-engineering activity rows are "
        "written under the project owner's id, so no token naming an organization finds them. Allowed for the same "
        "reason as the pointcloud seed and removed by the same work."
    ),
}

#: How long a reason has to be to count as written down. Eight words is short
#: enough not to police prose and long enough that "legacy" on its own is not an
#: answer: the entries above each say what the site does, why it is that way and
#: what would have to change.
MINIMUM_REASON_WORDS = 8

_KINDS_WITH_A_TEXTUAL_EQUIVALENT = ("compare", "assign", "kwarg", "sql")


def _identifier_names(node: ast.AST) -> set[str]:
    """Every identifier a node mentions, however it is spelled."""
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.arg):
            found.add(child.arg)
    return found


def _normalized(segment: str) -> str:
    """One line of code, whitespace collapsed, so a reformat is not a new site."""
    return " ".join(segment.split())


def _key(relative: str, kind: str, code: str) -> str:
    return f"{relative}::{kind}::{_normalized(code)[:160]}"


def _paired_operands(compares: list[ast.Compare]) -> bool:
    """Whether one value is compared against a tenant column and against a person column.

    The two comparisons are each innocent - ``tenant_id == owner`` compares a
    tenant with a variable, and ``created_by == owner`` compares a person with
    the same variable - and together they say the variable could be either, which
    is the claim this guard exists about. Only operands that are neither column
    count as the shared value, so ``tenant_id == owner_id`` is a ``compare`` and
    not a ``paired``, and a site is never reported twice.
    """
    tenant_side: set[str] = set()
    person_side: set[str] = set()
    for compare in compares:
        names = _identifier_names(compare)
        if names & set(TENANT_COLUMNS):
            tenant_side |= names
        if names & set(PERSON_COLUMNS):
            person_side |= names
    shared = tenant_side & person_side
    return bool(shared - set(TENANT_COLUMNS) - set(PERSON_COLUMNS))


def _sites_in_tree(relative: str, tree: ast.Module, source: str) -> set[str]:
    """Every site one parsed file contains, keyed the way the allowlist is."""
    found: set[str] = set()

    def segment(node: ast.AST) -> str:
        return ast.get_source_segment(source, node) or ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            parts = [node.left, *node.comparators]
            for index, left in enumerate(parts):
                for right in parts[index + 1 :]:
                    left_names, right_names = _identifier_names(left), _identifier_names(right)
                    crossed = (left_names & set(TENANT_COLUMNS) and right_names & set(PERSON_COLUMNS)) or (
                        left_names & set(PERSON_COLUMNS) and right_names & set(TENANT_COLUMNS)
                    )
                    if crossed:
                        found.add(_key(relative, "compare", segment(node)))
        elif isinstance(node, ast.Assign):
            value_names = _identifier_names(node.value)
            for target in node.targets:
                target_names = _identifier_names(target)
                crossed = (target_names & set(TENANT_COLUMNS) and value_names & set(PERSON_COLUMNS)) or (
                    target_names & set(PERSON_COLUMNS) and value_names & set(TENANT_COLUMNS)
                )
                if crossed:
                    found.add(_key(relative, "assign", segment(node)))
        elif isinstance(node, ast.keyword) and node.arg is not None:
            value_names = _identifier_names(node.value)
            crossed = (node.arg in TENANT_COLUMNS and value_names & set(PERSON_COLUMNS)) or (
                node.arg in PERSON_COLUMNS and value_names & set(TENANT_COLUMNS)
            )
            if crossed:
                parent = node.value
                found.add(_key(relative, "kwarg", f"{node.arg}={segment(parent)}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _SQL_PATTERN.search(node.value):
            found.add(_key(relative, "sql", _SQL_PATTERN.search(node.value).group(0)))
        elif isinstance(node, (ast.Call, ast.BoolOp)):
            compares = [child for child in ast.iter_child_nodes(node) if isinstance(child, ast.Compare)]
            if len(compares) > 1 and _paired_operands(compares):
                found.add(_key(relative, "paired", segment(node)))
    return found


def _sites_in_text(relative: str, text: str) -> set[str]:
    """Every site one unparsed file contains, found by reading it rather than parsing it."""
    found: set[str] = set()
    for pattern in _TEXTUAL_PATTERNS:
        for match in pattern.finditer(text):
            found.add(_key(relative, "text", match.group(0)))
    return found


def scan(app_dir: Path = APP) -> tuple[set[str], list[str]]:
    """Every site under ``backend/app``, and the names of the files that would not parse."""
    found: set[str] = set()
    unparsed: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        relative = str(path.relative_to(app_dir)).replace("\\", "/")
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            unparsed.append(relative)
            found |= _sites_in_text(relative, source)
            continue
        found |= _sites_in_tree(relative, tree, source)
    return found, unparsed


def _written_source(kind: str) -> str:
    """A tiny module holding one site of the given kind, for the tests below."""
    bodies = {
        "compare": "def f(row, project):\n    return row.tenant_id == project.owner_id\n",
        "assign": "def f(owner_id):\n    tenant_id = owner_id\n    return tenant_id\n",
        "kwarg": "from x import Row\n\n\ndef f(owner_id):\n    return Row(tenant_id=owner_id)\n",
        "kwarg_inverse": "from x import Row\n\n\ndef f(tenant_id):\n    return Row(created_by=tenant_id)\n",
        "sql": 'QUERY = "SELECT id FROM t WHERE tenant_id = created_by"\n',
        "paired": (
            "from sqlalchemy import or_\n\n\n"
            "def f(owner):\n    return or_(Contact.tenant_id == owner, Contact.created_by == owner)\n"
        ),
        "innocent_owner": "def f(owner_id):\n    return owner_id == owner_id\n",
        "innocent_tenant": "def f(tenant_id):\n    return Row(tenant_id=tenant_id)\n",
        "innocent_null": (
            "from sqlalchemy import or_\n\n\n"
            "def f(mine):\n    return or_(Contact.tenant_id.is_(None), Contact.created_by == mine)\n"
        ),
        "innocent_person": "def f(created_by, user_id):\n    return created_by == user_id\n",
    }
    return bodies[kind]


def _scan_source(kind: str, tmp_path: Path) -> set[str]:
    """Run the AST reader over one planted site and return what it found."""
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "planted.py").write_text(_written_source(kind), encoding="utf-8")
    found, unparsed = scan(app_dir)
    assert unparsed == [], f"the planted file did not parse, so nothing about it was proven: {unparsed}"
    return found


class TestTheGuardFindsTheShapesItClaimsToFind:
    """Each shape, planted, and found - because a gate that finds nothing passes."""

    @pytest.mark.parametrize("kind", ["compare", "assign", "kwarg", "kwarg_inverse", "sql", "paired"])
    def test_one_planted_site_is_found(self, kind: str, tmp_path: Path) -> None:
        found = _scan_source(kind, tmp_path)
        assert found, f"a planted {kind} site was not found, so the guard would pass over the real one"

    @pytest.mark.parametrize("kind", ["innocent_owner", "innocent_tenant", "innocent_null", "innocent_person"])
    def test_a_shape_that_is_not_a_tenant_meeting_a_person_is_left_alone(self, kind: str, tmp_path: Path) -> None:
        found = _scan_source(kind, tmp_path)
        assert not found, (
            f"{kind} was reported as a site: {sorted(found)}. A guard that flags a person compared with "
            "itself, a tenant given a tenant, a tenant compared with NULL or a person compared with a "
            "person is a guard whose list grows until nobody reads it."
        )

    def test_the_textual_reader_finds_the_three_shapes_it_claims(self, tmp_path: Path) -> None:
        """What an interpreter too old to parse a file still catches."""
        app_dir = tmp_path / "app"
        app_dir.mkdir(parents=True)
        (app_dir / "planted.py").write_text(_written_source("compare"), encoding="utf-8")
        found = _sites_in_text("planted.py", _written_source("compare"))
        assert found, "the textual reader found nothing in a comparison it is supposed to read"
        for kind in ("assign", "kwarg", "sql"):
            assert _sites_in_text("planted.py", _written_source(kind)), f"the textual reader missed {kind}"


class TestTheTwoReadersAgreeWhereTheyOverlap:
    """The fallback is not merely quieter than the parser."""

    def test_over_every_file_this_interpreter_can_parse_the_textual_reader_finds_the_same_sites(
        self,
    ) -> None:
        disagreements: list[str] = []
        parseable = 0
        for path in sorted(APP.rglob("*.py")):
            relative = str(path.relative_to(APP)).replace("\\", "/")
            source = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            parseable += 1
            parsed = {
                key
                for key in _sites_in_tree(relative, tree, source)
                if key.split("::")[1] in _KINDS_WITH_A_TEXTUAL_EQUIVALENT
            }
            if not parsed:
                continue
            # Compared by file and code, not by kind: the AST knows whether a
            # crossing was an assignment or an argument and the textual reader
            # cannot, so the kind label is the one thing the two are allowed to
            # differ on.
            textual = {
                " ".join(match.group(0).split()) for pattern in _TEXTUAL_PATTERNS for match in pattern.finditer(source)
            }
            for key in parsed:
                code = key.split("::", 2)[2]
                if not any(code.startswith(fragment) or fragment.startswith(code) for fragment in textual):
                    disagreements.append(key)
        assert parseable > 2000, f"only {parseable} files parsed, which is not the tree this guard covers"
        assert not disagreements, (
            f"the textual reader missed {len(disagreements)} site(s) the AST found: {disagreements[:5]}. On an "
            "interpreter older than the backend those sites would be invisible, and the count printed by the "
            "test below would be a claim this one contradicts."
        )


class TestTheAllowlistIsTheTreeAndTheTreeIsTheAllowlist:
    """Both directions, because a list that only grows stops being a count of anything."""

    def test_no_site_is_outside_the_allowlist(self) -> None:
        found, unparsed = scan()
        unlisted = sorted(found - set(ALLOWLIST))
        assert not unlisted, (
            f"{len(unlisted)} site(s) compare a tenant with a person and carry no written reason: {unlisted}. "
            "Either the comparison is wrong and should be fixed, or it is right for a reason somebody has to "
            "write into ALLOWLIST in this commit. "
            f"({len(unparsed)} file(s) were read textually because this interpreter could not parse them.)"
        )

    def test_no_allowlist_entry_has_outlived_its_site(self) -> None:
        found, _unparsed = scan()
        stale = sorted(set(ALLOWLIST) - found)
        assert not stale, (
            f"{len(stale)} ALLOWLIST entry/entries no longer match anything in the tree: {stale}. An entry that "
            "outlives its site quietly re-permits whatever later takes the same shape, so closing a site means "
            "deleting its reason in the same commit."
        )

    def test_every_reason_is_written_down(self) -> None:
        thin = [
            key for key, reason in ALLOWLIST.items() if len(reason.split()) < MINIMUM_REASON_WORDS or not reason.strip()
        ]
        assert not thin, f"these entries carry no reason a reader could act on: {thin}"

    def test_the_allowlist_is_not_empty_and_the_tree_was_actually_read(self) -> None:
        """A guard whose subject moved away should not report success."""
        found, unparsed = scan()
        assert found, "no site at all was found, which means the reader stopped reading rather than the tree got clean"
        assert ALLOWLIST, "the allowlist is empty while the tree still has sites"
        files = sum(1 for _ in APP.rglob("*.py"))
        assert files > 2000, f"only {files} python files under {APP}, which is not the backend"
        assert len(unparsed) < files, "every file fell back to the textual reader, so nothing was parsed"
