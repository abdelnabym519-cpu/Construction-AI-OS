# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every gate evidence provider that reads the wall clock takes the day as a parameter.

A gate decides whether a project may proceed, and several of those decisions are
about time: a commitment whose decision date has passed, a permit phase whose
window has closed, a stakeholder review that is overdue. A provider that reads
``datetime.now()`` itself cannot be tested against a date, so the test that covers
it runs against whatever day the suite happens to run on. Those tests pass for
months and then fail on a calendar boundary nobody chose - which is exactly what
happened here, and why ``plant_readiness`` grew a ``today`` parameter first.

This is the ratchet on that fix. It reads the source of every module's
``evidence.py`` with the standard library only - no application import, no
database, no event loop - and requires two things of each wall-clock read: that it
sits behind a seam, and that the function it sits in declares a keyword-only
``today`` defaulting to ``None``. Keyword-only and defaulting to ``None`` is what
keeps the production default the real date: the evidence registry calls these
providers without the argument, so nothing about the shipped behaviour depends on
a caller remembering to pass one.

Reading source rather than importing is deliberate. An importing test could only
check the five providers somebody remembered to list, and would itself need the
whole application to construct; this one globs the tree, so a provider added next
year is covered on the day it is written, and it runs on any Python.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

MODULES = Path(__file__).resolve().parents[2] / "app" / "modules"

WALL_CLOCK = re.compile(r"datetime\.now\(|date\.today\(|\.utcnow\(")
# The accepted seams. Each one lets a caller supply the day and falls back to the
# real clock when they do not, which is the whole point: production keeps reading
# the wall clock, a test does not have to.
SEAM = re.compile(
    r"if\s+today\s+is\s+not\s+None\s+else|today\s+or\s+datetime|\bnow\s+or\s+datetime|\bas_of\s+or\s+date"
)


def _evidence_files() -> list[Path]:
    return sorted(MODULES.glob("*/evidence.py"))


def _providers_with_a_clock(tree: ast.AST) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    out: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) or isinstance(sub, ast.Call):
                    src = ast.unparse(sub)
                    if WALL_CLOCK.search(src):
                        out[node.name] = node
                        break
    return out


def test_there_are_evidence_modules_to_check() -> None:
    """The glob is the scope, so an empty scope would silently pass everything."""
    assert len(_evidence_files()) >= 5, [p.parent.name for p in _evidence_files()]


def test_no_evidence_provider_reads_the_wall_clock_unconditionally() -> None:
    offenders: list[str] = []
    for path in _evidence_files():
        for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not WALL_CLOCK.search(line) or line.lstrip().startswith("#"):
                continue
            if not SEAM.search(line):
                offenders.append(f"{path.parent.name}/evidence.py:{ln}: {line.strip()[:100]}")
    assert not offenders, (
        "these gate evidence providers read the clock directly, so no test can hold the day still:\n  "
        + "\n  ".join(offenders)
    )


def test_every_provider_behind_a_seam_declares_today_as_a_keyword_only_default() -> None:
    """The seam has to be reachable, and reaching it must not change production.

    Keyword-only, because the evidence registry calls these providers
    positionally and a new positional parameter would silently shift an argument.
    Defaulting to None, because the shipped path must keep reading the real date.
    """
    bad: list[str] = []
    for path in _evidence_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, fn in _providers_with_a_clock(tree).items():
            kwonly = {a.arg: d for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=False)}
            if "today" not in kwonly:
                bad.append(f"{path.parent.name}.{name}: reads the clock but has no keyword-only 'today'")
            elif kwonly["today"] is None or ast.unparse(kwonly["today"]) != "None":
                bad.append(
                    f"{path.parent.name}.{name}: 'today' does not default to None, so production would depend on a caller"
                )
    assert not bad, "\n  ".join(bad)


def test_the_five_reported_time_bombs_are_among_those_now_seamed() -> None:
    """Named, so the list this work was given cannot quietly shrink.

    These five sites were the ones reported as failing on a calendar boundary.
    plant_register is here too: it was fixed first and is the shape the others
    follow, and losing its seam would be the same regression.
    """
    expected = {
        "commitment_release": ["overdue_decisions"],
        "permits": ["roadmap_and_register", "start_and_handover"],
        "quality_concept": ["quality_readiness"],
        "stakeholders": ["governance_readiness"],
        "plant_register": ["plant_readiness"],
    }
    missing: list[str] = []
    for module, fns in expected.items():
        path = MODULES / module / "evidence.py"
        if not path.exists():
            missing.append(f"{module}/evidence.py does not exist")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}
        for fn in fns:
            if fn not in found:
                missing.append(f"{module}.{fn} is gone")
                continue
            kwonly = {a.arg for a in found[fn].args.kwonlyargs}
            if "today" not in kwonly:
                missing.append(f"{module}.{fn} lost its keyword-only 'today'")
    assert not missing, "\n  ".join(missing)
