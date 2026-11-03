#!/usr/bin/env python3
"""No promotional or upstream-attribution strings in anything the product renders.

The owner ships this product as their own. Nothing a user can see should tell
them it is open source, that it is built on somebody else's project, or that
there is a community to join somewhere else. This guard is what keeps that true
after the cleanup: it fails when a *rendered* string carries one of the claims
below, so the next marketing line cannot be added by accident.

What is scanned, and why only that:

  * locale values - the `"key": "value"` pairs in frontend/src/app/locales/*.ts.
    These are what users actually read, in every language.
  * component strings - string literals and JSX text in .tsx/.ts under
    frontend/src. Comments are deliberately NOT scanned: they are not rendered,
    and the comments that explain why a promotional surface was removed have to
    be able to name it. Same for test files, which assert absence and so must be
    allowed to quote the thing that is absent.

What is exempt, and why:

  * the legal floor - LICENSE, NOTICE, COPYRIGHT and the /legal page. Stating the
    licence is an obligation under AGPL-3.0 section 5 and 13, not promotion.
  * the copyright header on the first lines of every source file (same reason).
  * generated files, which would otherwise fail on content nobody wrote.
  * scripts/promotional_allowlist.json - entries with a written reason. These are
    functional, not promotional: the converter and CWICR components are downloaded
    from where their publisher puts them, so the product has to name that source,
    and the Telegram/Slack/Teams entries under integrations.* are notification
    channels the user configures, not a community to join.
  * scripts/promotional_debt_baseline.json - a per-file count of violations that
    were already in the tree when this guard landed. This is labelled debt, NOT
    permission: the guard fails if any file's count grows or a new file appears,
    so the number can only go down. Work through it and lower the baseline in the
    same diff.

Exit 0 means clean. Exit 1 prints every new violation with its file and line.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "frontend" / "src" / "app" / "locales"
SRC = ROOT / "frontend" / "src"
ALLOWLIST = ROOT / "scripts" / "promotional_allowlist.json"
BASELINE = ROOT / "scripts" / "promotional_debt_baseline.json"

# Claims that must not reach a user. Case-insensitive except where the spelling
# itself is the tell (#1, and the project names).
FORBIDDEN = [
    "open source",
    "open-source",
    "opensource",
    "AGPL",
    "GitHub",
    "Telegram",
    "openconstructionerp",
    "DataDrivenConstruction",
    "Data Driven Construction",
    "#1",
]
# "free" only in its marketing sense. On its own the word is everywhere in this
# product for legitimate reasons (free text, free-form, duty-free, risk-free),
# so it is matched in the collocations that make it a sales claim.
FREE_MARKETING = re.compile(
    r"\b(?:100%|completely|totally|entirely|absolutely|forever|always)\s+free\b"
    r"|\bfree\s+(?:and\s+)?open[\s-]?source\b"
    r"|\bfree\s+forever\b"
    r"|\b(?:it'?s|it\s+is)\s+free\b",
    re.I,
)

# Files that are the legal floor, or that this guard must not read as product UI.
EXEMPT_PATHS = (
    "frontend/src/features/legal/",  # the one plain Legal page
    "frontend/public/legal/",
)
EXEMPT_NAMES = {"LICENSE", "NOTICE", "COPYRIGHT", "LICENSE.txt", "NOTICE.txt"}
GENERATED = {"architecture_manifest.json"}
HEADER_LINES = 8  # the copyright/SPDX header every source file carries


def hits(text: str) -> list[str]:
    found = [w for w in FORBIDDEN if w.lower() in text.lower()]
    if FREE_MARKETING.search(text):
        found.append("free (marketing)")
    return found


def locale_value(line: str) -> str | None:
    m = re.match(r'\s*"([^"]+)":\s*"(.*)",?\s*$', line)
    return m.group(2) if m else None


STRING_RE = re.compile(r"'([^'\n]{2,400})'" + r'|"([^"\n]{2,400})"' + r"|`([^`\n]{2,400})`")
JSX_TEXT_RE = re.compile(r">\s*([^<>{}\n]{2,200}?)\s*<")


def component_strings(text: str) -> list[tuple[int, str]]:
    """Quoted literals and JSX text nodes, with comments excluded.

    Line-based on purpose. An earlier version walked the file character by
    character to track string and comment state, and one unterminated state in a
    6,000-line TSX file swallowed the rest of the file: every later hit was
    reported at the same line number, quoting comment text as if it were a
    string. A guard that reports the wrong line is worse than no guard, because
    it sends whoever reads it to the wrong place.

    What this covers: single, double and back-quoted literals on one line (so
    `defaultValue: 'GitHub repository'` is caught) and text between two tags on
    one line (so `<span>GitHub</span>` is caught). What it does not cover is a
    literal or a text node split across lines. That is a real limitation and it
    is accepted because nearly every string this product renders goes through
    t() and so is scanned as a locale value above, where the coverage is exact.
    """
    out: list[tuple[int, str]] = []
    in_block = False
    for ln, line in enumerate(text.splitlines(), 1):
        if in_block:
            if "*/" in line:
                in_block = False
            continue
        stripped = line.strip()
        if stripped.startswith(("/*", "{/*", "/**")):
            if "*/" not in line:
                in_block = True
            continue
        if stripped.startswith(("//", "*", "*/")):
            continue
        for m in STRING_RE.finditer(line):
            val = next((g for g in m.groups() if g is not None), "")
            if val:
                out.append((ln, val))
        for m in JSX_TEXT_RE.finditer(line):
            if m.group(1).strip():
                out.append((ln, m.group(1).strip()))
    return out


def main() -> int:
    allow: dict[str, str] = {}
    if ALLOWLIST.exists():
        allow = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    baseline: dict[str, int] = {}
    if BASELINE.exists():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8")).get("counts", {})

    violations: dict[str, list[str]] = {}

    def record(rel: str, key: str, text: str, found: list[str]) -> None:
        if rel in EXEMPT_PATHS or any(rel.startswith(p) for p in EXEMPT_PATHS):
            return
        # An allowlist entry is either the exact i18n key, or a substring that
        # identifies a literal (component lines have no stable key, so a pattern
        # is the only handle that survives an unrelated edit above them).
        if key in allow or any(pat in text for pat in allow):
            return
        violations.setdefault(rel, []).append(f"{key}: {', '.join(found)} -> {text[:90]!r}")

    for p in sorted(LOCALES.glob("*.ts")):
        rel = str(p.relative_to(ROOT))
        for ln, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if ln <= HEADER_LINES:
                continue
            val = locale_value(line)
            if val is None:
                continue
            key = line.split('"', 2)[1]
            found = hits(val)
            if found:
                record(rel, key, val, found)

    for p in sorted(SRC.rglob("*")):
        if not p.is_file() or p.suffix not in {".ts", ".tsx"}:
            continue
        rel = str(p.relative_to(ROOT))
        if p.name in GENERATED or ".test." in p.name or "__tests__" in rel or "/locales/" in rel:
            continue
        if any(rel.startswith(x) for x in EXEMPT_PATHS) or p.name in EXEMPT_NAMES:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not any(w.lower() in text.lower() for w in FORBIDDEN) and not FREE_MARKETING.search(text):
            continue
        for ln, s in component_strings(text):
            found = hits(s)
            if found and ln > HEADER_LINES:
                record(rel, f"line {ln}", s, found)

    new: list[str] = []
    for rel, items in sorted(violations.items()):
        allowed_n = baseline.get(rel, 0)
        if len(items) > allowed_n:
            new.append(f"{rel}: {len(items)} violation(s), baseline allows {allowed_n}")
            new.extend(f"    {i}" for i in items[allowed_n:])
    for rel in sorted(baseline):
        if rel not in violations:
            print(f"note: {rel} is clean now - lower its baseline entry ({baseline[rel]} -> 0)")

    if "--write-baseline" in sys.argv:
        counts = {rel: len(items) for rel, items in sorted(violations.items())}
        BASELINE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Per-file counts of promotional strings that were already in the tree when "
                        "check_no_promotional_strings.py landed. This is labelled debt, not "
                        "permission: the guard fails if any count grows or a new file appears, so "
                        "these numbers can only go down. Lower an entry in the same diff that "
                        "removes the strings it counts."
                    ),
                    "counts": counts,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"baseline written: {len(counts)} file(s), {sum(counts.values())} violation(s) recorded as debt"
        )
        return 0

    if new:
        print("ERROR: promotional or upstream-attribution strings reach the product UI.")
        print("\n".join(new[:120]))
        if len(new) > 120:
            print(f"... and {len(new) - 120} more")
        print(
            "\nFix by removing the string. If it is functional rather than promotional "
            "(a converter download source, a notification channel), add the key to "
            "scripts/promotional_allowlist.json with a written reason. Never raise a "
            "baseline count to make this pass."
        )
        return 1
    total = sum(len(v) for v in violations.values())
    print(
        f"no promotional strings OK: {len(violations)} file(s) carrying {total} baselined "
        f"violation(s), {len(allow)} allowlisted key(s), 0 new"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
