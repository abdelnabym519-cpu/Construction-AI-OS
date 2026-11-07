# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A PDF, a COBie workbook, a SARIF log, an IFC file and an e-mail all name one product.

These five artefacts leave the process. Once a file is on somebody's disk the
application cannot retract what is written into it, so the name stamped in the
producer, creator and originating-system fields has to be right at the moment of
export - and it has to be the same name the UI shows, or a user holding a
document and looking at a screen sees two products.

Each of the five already reads the one display-name constant, ``app_name`` in
config, through its own resolver: ``pdf_branding.DEFAULT_BRAND``,
``sarif_exporter.TOOL_NAME``, ``email.templates.BRAND_NAME``,
``demo_placeholders._brand()`` for the generated IFC header and placeholder PDF,
and ``DEFAULT_BRAND`` again for the COBie workbook's properties and its Company
and ExtSystem columns. What nothing held them to was an agreement between the
five, which is what this asserts: that they are one string, that the string is
the configured display name, and that it carries no upstream or open-source
wording. The COBie fields are checked at source level because building a workbook
needs the exporter's whole input, and the question here is which name it reaches
for, not whether the workbook is valid.

``tests/unit/test_the_display_name_is_one_constant.py`` pins the constant to the
config default; this pins the artefacts to the constant.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

# What must never be stamped into a file a user keeps.
FORBIDDEN = (
    "open source",
    "open-source",
    "opensource",
    "AGPL",
    "GitHub",
    "Telegram",
    "OpenConstructionERP",
    "DataDrivenConstruction",
    "Data Driven Construction",
)


def _offenders(text: str) -> list[str]:
    return [w for w in FORBIDDEN if w.lower() in str(text).lower()]


def test_the_five_artefact_producers_are_one_string() -> None:
    from app.core.demo_placeholders import _brand
    from app.core.email.templates import BRAND_NAME
    from app.core.pdf_branding import DEFAULT_BRAND
    from app.modules.validation.sarif_exporter import TOOL_NAME

    names = {
        "pdf producer": DEFAULT_BRAND,
        "sarif driver": TOOL_NAME,
        "e-mail brand": BRAND_NAME,
        "ifc and placeholder producer": _brand(),
    }
    distinct = set(names.values())
    assert len(distinct) == 1, f"the artefacts disagree about the product name: {names}"
    assert str(distinct.pop()).strip(), "the display name resolved to nothing"


def test_no_artefact_producer_carries_upstream_or_open_source_wording() -> None:
    from app.core.demo_placeholders import _brand
    from app.core.email.templates import BRAND_NAME
    from app.core.pdf_branding import DEFAULT_BRAND
    from app.modules.validation.sarif_exporter import TOOL_NAME

    for label, value in (
        ("pdf producer", DEFAULT_BRAND),
        ("sarif driver", TOOL_NAME),
        ("e-mail brand", BRAND_NAME),
        ("ifc and placeholder producer", _brand()),
    ):
        assert not _offenders(value), f"{label} is stamped as {value!r}, which carries {_offenders(value)}"


def test_the_producer_is_the_configured_display_name_not_a_literal() -> None:
    """The shipped name follows the setting, so white-labelling reaches the files.

    A hardcoded name would pass every assertion above and still be wrong: the
    point of the resolvers is that a workspace which renames the product renames
    it in its exports too.
    """
    from app.config import get_settings
    from app.core.pdf_branding import DEFAULT_BRAND

    configured = str(get_settings().app_name or "").strip()
    if configured:
        assert configured == DEFAULT_BRAND


def test_the_cobie_workbook_reaches_for_the_shared_brand_constant() -> None:
    """Source-level, because the question is which name the exporter uses."""
    path = BACKEND / "app" / "modules" / "bim_hub" / "exporters" / "cobie.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        n.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.pdf_branding"
        for n in node.names
    }
    assert "DEFAULT_BRAND" in imported, "cobie.py no longer imports the shared brand constant"

    # Every producer-shaped field is assigned from the constant, not a literal.
    producer_fields = ("creator", "lastModifiedBy", "description", "Company", "ExternalSystem", "ExtSystem")
    stamped: dict[str, int] = {}
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Attribute):
            target = node.targets[0].attr
            value = node.value
        elif isinstance(node, ast.keyword) and node.arg:
            target = node.arg
            value = node.value
        elif isinstance(node, ast.Dict):
            continue
        else:
            continue
        if target in producer_fields:
            src = ast.unparse(value)
            assert "DEFAULT_BRAND" in src, f"COBie {target} is set from {src!r}, not the shared brand constant"
            stamped[target] = stamped.get(target, 0) + 1
    # The dictionary form ("Company": DEFAULT_BRAND) is counted separately, since
    # a Dict node's keys are not Assign targets.
    text = path.read_text(encoding="utf-8")
    for field in ("Company", "ExternalSystem", "ExtSystem"):
        assert re.search(rf'"{field}":\s*DEFAULT_BRAND', text), f"COBie column {field} is not the shared brand constant"
    assert stamped.get("creator"), "the workbook's creator property is no longer stamped"


def test_no_hardcoded_product_name_survives_in_the_exporters() -> None:
    """The names above are checked by value; this checks the files that write them."""
    for rel in (
        "app/core/pdf_branding.py",
        "app/modules/validation/sarif_exporter.py",
        "app/core/email/templates.py",
        "app/modules/bim_hub/exporters/cobie.py",
    ):
        text = (BACKEND / rel).read_text(encoding="utf-8")
        lines = text.splitlines()
        # The copyright header is the legal floor, and docstrings are not rendered:
        # they are where a removal is explained, so they have to be able to name
        # the thing removed. Both are skipped for the same reason the promotional
        # guard skips them - a check that flags them trains whoever reads it to
        # ignore the check.
        doc_lines: set[int] = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    doc_lines.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
        for ln, line in enumerate(lines, 1):
            if ln <= 8 or ln in doc_lines:
                continue
            code = line.split("#", 1)[0]
            for word in ("OpenConstructionERP", "DataDrivenConstruction"):
                assert word not in code, f"{rel}:{ln} hardcodes {word!r}: {line.strip()[:100]}"
