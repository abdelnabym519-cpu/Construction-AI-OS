# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The strings a user reads out of the backend name this product and nothing else.

``scripts/check_no_promotional_strings.py`` reads the same surfaces statically and
is the ratchet that keeps them clean. These tests are the other half: they exercise
the code paths that actually produce the text, so a string that reaches a user
through a route the static scan does not cover still fails here.

Three things are asserted. The CLI's own help, because ``--help`` is the first
thing an operator reads and it is built from argparse metadata rather than from a
template the scan sees. The name stamped into generated placeholder documents,
because that lands inside an exported file where no UI can retract it. And the
guard's two baselines, because a guard whose debt file can silently grow is a
guard that only looks like a wall.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from app.cli import _build_parser
from app.core.demo_placeholders import _brand
from app.core.pdf_branding import DEFAULT_BRAND

REPO_ROOT = Path(__file__).resolve().parents[3]

# What must never reach a user. "openconstructionerp" is deliberately absent: it
# is this product's own distribution name - the console script, the pip package,
# the data directory - and the CLI help has to print the command the operator
# actually types. It is allowlisted in the guard for the same reason.
FORBIDDEN = (
    "open source",
    "open-source",
    "opensource",
    "AGPL",
    "GitHub",
    "Telegram",
    "DataDrivenConstruction",
    "Data Driven Construction",
)
RANK_CLAIM = re.compile(r"\b(?:the|world'?s|region'?s|industry'?s)\s+#1\b|^#1(?![=\w-])", re.I)


def _offenders(text: str) -> list[str]:
    found = [w for w in FORBIDDEN if w.lower() in text.lower()]
    if RANK_CLAIM.search(text):
        found.append("#1 (rank claim)")
    return found


def test_the_cli_help_names_only_this_product() -> None:
    """``openconstructionerp --help`` describes the product, not its origins."""
    help_text = _build_parser().format_help()
    assert help_text.strip(), "the parser produced no help text at all"
    assert not _offenders(help_text), f"--help carries {_offenders(help_text)}"


def test_the_cli_help_still_describes_what_the_product_does() -> None:
    """Removing the advertising left the help useful, not empty.

    A cleanup that deletes until nothing is objectionable is not a fix, so this
    holds the other side: the description still says what the tool is and the
    subcommands an operator needs are still listed.
    """
    help_text = _build_parser().format_help()
    assert "construction cost estimation platform" in help_text
    for subcommand in ("serve", "doctor"):
        assert subcommand in help_text, f"--help no longer mentions {subcommand}"


def test_a_generated_placeholder_document_is_stamped_with_the_display_name() -> None:
    """The name in an exported file is the one constant the UI also uses."""
    assert _brand() == DEFAULT_BRAND
    assert not _offenders(_brand())


def test_both_promotional_debt_baselines_are_empty() -> None:
    """The debt files are empty, and the guard agrees with the tree.

    Read directly rather than trusted: an empty baseline next to a dirty tree
    would mean the guard had stopped looking, which is the one failure mode a
    ratchet has to refuse.
    """
    for name in ("promotional_debt_baseline.json", "promotional_debt_baseline_backend.json"):
        counts = json.loads((REPO_ROOT / "scripts" / name).read_text(encoding="utf-8"))["counts"]
        assert counts == {}, f"{name} still carries debt: {counts}"
    done = subprocess.run(
        [sys.executable, "scripts/check_no_promotional_strings.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "0 new" in done.stdout, done.stdout
