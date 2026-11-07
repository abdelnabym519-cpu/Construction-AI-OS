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

# Every relative path below is taken with as_posix(), never str(). str() of a
# Path uses the platform separator, so on a Windows checkout the exemption
# patterns - "frontend/src/features/legal/", "/locales/", "__tests__", "/tests/"
# - matched nothing at all, and the guard reported the licence page and all 45
# locale bundles as new violations. It failed on Windows while passing on Linux,
# which is what it did in CI inside a backend unit test that ran it as a
# subprocess. The baseline files are keyed with forward slashes as well, so
# normalising keeps their counts comparable across platforms too.
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
# "#1" only as the marketing claim. Unqualified it matched CSS hex colours
# ('#10b981') and licence placeholders ("CA License #1234567"), which is how 106
# component hits and 38 locale hits entered the baseline that were never a claim
# about being number one. A hex colour always continues with a hex digit; the
# claim never does.
HEX_TAIL = re.compile(r"#1[0-9a-fA-F]")
# "#1" only when it claims to be first. Two more classes turned up once the
# backend was scanned: IFC/STEP entity identifiers are written "#1=IFCPROJECT",
# where the number is a handle and not a rank, and ordinary ordinals - "Weekly
# progress meeting #1", "Pay application #1" - number a document in a sequence.
# Neither says anything about the product. The claim always reads "the #1 <noun>"
# or stands alone as a headline, so it is matched in that shape only. The
# headline form needs the lookahead rather than a word boundary: in "#1=IFCPROJECT"
# the "=" is itself a boundary, so \b let every IFC entity identifier through as a
# rank claim. What follows a headline "#1" is a space or nothing, never "=".
RANK_CLAIM = re.compile(r"\b(?:the|world'?s|region'?s|industry'?s)\s+#1\b|^#1(?![=\w-])", re.I)
# AGPL as a word, not a substring. Matched loosely it fired inside ordinary words
# in two languages: Danish "Tagplantrum" (a roof plant room) and Filipino
# "Magplano" (to plan) both contain "agpl". Twenty keys across da.ts and fil.ts
# were baselined as licence claims for that reason alone. The acronym is always
# written in capitals in real use, and a word boundary keeps "AGPL-3.0" matching
# while leaving both languages alone.
AGPL_WORD = re.compile(r"\bAGPL\b")
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
# "free text" / "free-text" is a field type in this domain, not a price claim, and
# "whether it is free text" matched the collocation above in four backend files.
# A price claim never has the word "text" or "form" immediately after it.
FREE_NOT_MARKETING = re.compile(r"\bfree[\s-]+(?:text|texts|form|format|response|entry|issue|hand)\b", re.I)

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
    if "#1" in found and not RANK_CLAIM.search(HEX_TAIL.sub("", text)):
        found.remove("#1")
    if "AGPL" in found and not AGPL_WORD.search(text):
        found.remove("AGPL")
    if FREE_MARKETING.search(text) and not FREE_NOT_MARKETING.search(text):
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


BACKEND = ROOT / "backend" / "app"
BACKEND_BASELINE = ROOT / "scripts" / "promotional_debt_baseline_backend.json"

# Only the backend surfaces whose strings reach a user, and only because most of
# backend/app is internal: a variable name, a log line, a SQL fragment or a
# docstring is not something an operator reads. Each entry below produces text
# that leaves the process - rendered into an e-mail, stamped into a PDF or an
# exported file, served as API documentation, or printed by the CLI.
#
# What this list deliberately does NOT cover: router and service modules whose
# strings go to logs, exception messages and internal identifiers. A promotional
# string added there would not be caught. That is a real limit and it is stated
# here rather than left for someone to discover by trusting a green run.
BACKEND_USER_VISIBLE = (
    "backend/app/cli.py",
    "backend/app/cli_static.py",
    "backend/app/main.py",
    "backend/app/core/email/",
    "backend/app/core/pdf_branding.py",
    "backend/app/core/pdf_appearance.py",
    "backend/app/core/pdf_stamp.py",
    "backend/app/core/register_export.py",
    "backend/app/core/demo_placeholders.py",
    "backend/app/core/partner_pack/",
    "backend/app/modules/validation/sarif_exporter.py",
    "backend/app/modules/bcf/",
    "backend/app/modules/bim_hub/exporters/",
    "backend/app/modules/bim_requirements/exporters",
    "backend/app/modules/design_options/exporters",
    "backend/app/modules/boq/pdf_export.py",
    "backend/app/modules/tendering/pdf_documents.py",
    "backend/app/modules/module_builder/",
)


def python_strings(text: str) -> list[tuple[int, str]]:
    """String literals in Python source, with comments and docstrings excluded.

    Docstrings are excluded for the same reason the frontend scan excludes
    comments: they are not rendered, and the ones explaining why a promotional
    surface was removed have to be able to name it. A docstring is recognised by
    position - it is the first statement after a ``def``, ``class``, decorator or
    ``:`` line, or of a file - and both shapes are handled, the multi-line block
    and the single line that opens and closes at once. An earlier draft skipped
    only the opening line, so every docstring body was scanned as prose and
    inflated the count with text no user ever sees.
    """
    out: list[tuple[int, str]] = []
    in_doc: str | None = None
    in_str: str | None = None
    prev = ""
    for ln, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if in_doc:
            if in_doc in stripped:
                in_doc = None
            continue
        if in_str:
            if in_str in line:
                out.append((ln, line.split(in_str)[0]))
                in_str = None
            else:
                out.append((ln, line))
            continue
        if stripped.startswith("#"):
            continue
        doc_pos = prev == "" or prev.endswith(":") or prev.startswith(("def ", "class ", "async def ", "@"))
        opened = next((d for d in ('"""', "'''") if line.count(d) % 2 == 1), None)
        if opened:
            if doc_pos:
                in_doc = opened
            else:
                in_str = opened
                out.append((ln, line.split(opened, 1)[1]))
            prev = stripped
            continue
        if doc_pos and stripped.startswith(('"""', "'''")):
            prev = stripped
            continue  # a docstring that opens and closes on one line
        code = line
        hash_at = code.find(" #")
        if hash_at > 0 and code[:hash_at].count('"') % 2 == 0 and code[:hash_at].count("'") % 2 == 0:
            code = code[:hash_at]
        for m in STRING_RE.finditer(code):
            val = next((g for g in m.groups() if g is not None), "")
            if val:
                out.append((ln, val))
        prev = stripped
    return out


def backend_files() -> list[Path]:
    out: list[Path] = []
    for pat in BACKEND_USER_VISIBLE:
        p = ROOT / pat
        if p.is_dir():
            out.extend(sorted(x for x in p.rglob("*.py") if x.is_file()))
        elif p.is_file():
            out.append(p)
        elif ROOT.glob(pat + "*"):
            out.extend(sorted(x for x in ROOT.glob(pat + "*") if x.is_file() and x.suffix == ".py"))
        elif p.parent.is_dir():
            out.extend(sorted(x for x in p.parent.rglob(p.name + "*.py") if x.is_file()))
    seen: set[Path] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def main() -> int:
    allow: dict[str, str] = {}
    if ALLOWLIST.exists():
        allow = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    # An entry written "=literal" matches only a string that is exactly that
    # literal; every other entry matches as a substring. The distinction exists
    # because some functional strings are single words that also appear inside
    # promotional sentences. Allowing the substring "Telegram" would exempt the
    # bare channel identifier defaultName: 'Telegram' and, with it, any future
    # "join our Telegram community". Allowing "=Telegram" exempts the identifier
    # and nothing else. The same reasoning kept "on GitHub" out of the allowlist:
    # it would have re-permitted the tour step this cleanup deleted.
    allow_exact = {k[1:] for k in allow if k.startswith("=")}
    allow_sub = [k for k in allow if not k.startswith("=")]

    def allowed(text: str, key: str) -> bool:
        return key in allow or text in allow_exact or any(pat in text for pat in allow_sub)

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
        if allowed(text, key):
            return
        violations.setdefault(rel, []).append(f"{key}: {', '.join(found)} -> {text[:90]!r}")

    for p in sorted(LOCALES.glob("*.ts")):
        rel = p.relative_to(ROOT).as_posix()
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
        rel = p.relative_to(ROOT).as_posix()
        if p.name in GENERATED or ".test." in p.name or "__tests__" in rel or "/locales/" in rel:
            continue
        if any(rel.startswith(x) for x in EXEMPT_PATHS) or p.name in EXEMPT_NAMES:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not any(w.lower() in text.lower() for w in FORBIDDEN) and not FREE_MARKETING.search(text):
            continue
        lines = text.splitlines()
        for ln, lit in component_strings(text):
            found = hits(lit)
            if not found or ln <= HEADER_LINES:
                continue
            # A defaultValue on the line that renders an allowlisted key is the
            # same string the key stands for, so it gets the same treatment.
            # boq.source_open is the case that needs it: its default is the two
            # words "Open source", where "Open" is a verb and "source document"
            # is what it opens. Allowlisting the bare phrase would exempt every
            # real occurrence of it, so the exemption is tied to the key.
            src = lines[ln - 1] if ln - 1 < len(lines) else ""
            if any(f"'{k}'" in src or f'"{k}"' in src for k in allow):
                continue
            record(rel, f"line {ln}", lit, found)

    backend_violations: dict[str, list[str]] = {}
    for p in backend_files():
        rel = p.relative_to(ROOT).as_posix()
        if "/tests/" in rel or rel.endswith("_test.py"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not any(w.lower() in text.lower() for w in FORBIDDEN) and not FREE_MARKETING.search(text):
            continue
        for ln, lit in python_strings(text):
            if ln <= HEADER_LINES:
                continue
            found = hits(lit)
            if not found:
                continue
            if allowed(lit, f"line {ln}"):
                continue
            backend_violations.setdefault(rel, []).append(f"line {ln}: {', '.join(found)} -> {lit[:90]!r}")

    new: list[str] = []
    for rel, items in sorted(violations.items()):
        allowed_n = baseline.get(rel, 0)
        if len(items) > allowed_n:
            new.append(f"{rel}: {len(items)} violation(s), baseline allows {allowed_n}")
            new.extend(f"    {i}" for i in items[allowed_n:])
    for rel in sorted(baseline):
        if rel not in violations:
            print(f"note: {rel} is clean now - lower its baseline entry ({baseline[rel]} -> 0)")

    backend_baseline: dict[str, int] = {}
    if BACKEND_BASELINE.exists():
        backend_baseline = json.loads(BACKEND_BASELINE.read_text(encoding="utf-8")).get("counts", {})
    backend_new: list[str] = []
    for rel, items in sorted(backend_violations.items()):
        allowed_n = backend_baseline.get(rel, 0)
        if len(items) > allowed_n:
            backend_new.append(f"{rel}: {len(items)} violation(s), baseline allows {allowed_n}")
            backend_new.extend(f"    {i}" for i in items[allowed_n:])
    for rel in sorted(backend_baseline):
        if rel not in backend_violations:
            print(f"note: {rel} is clean now - lower its backend baseline entry ({backend_baseline[rel]} -> 0)")

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
        print(f"baseline written: {len(counts)} file(s), {sum(counts.values())} violation(s) recorded as debt")
        BACKEND_BASELINE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Per-file counts of promotional strings in the backend surfaces whose text "
                        "reaches a user: e-mail templates, PDF and export producer strings, OpenAPI "
                        "documentation and CLI output. Labelled debt, not permission - the guard "
                        "fails if a count grows or a new file appears. The target is an empty file."
                    ),
                    "counts": {rel: len(items) for rel, items in sorted(backend_violations.items())},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"backend baseline written: {len(backend_violations)} file(s), "
            f"{sum(len(v) for v in backend_violations.values())} violation(s) recorded as debt"
        )
        return 0

    if backend_new:
        print("ERROR: promotional or upstream-attribution strings reach users from the backend.")
        print("\n".join(backend_new[:120]))
        if len(backend_new) > 120:
            print(f"... and {len(backend_new) - 120} more")
        print(
            "\nFix by removing the string or by naming this product instead. If it is functional "
            "(a download source, a package identifier, a licence statement the law requires), add "
            "it to scripts/promotional_allowlist.json with a written reason."
        )
        return 1

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
    backend_total = sum(len(v) for v in backend_violations.values())
    print(
        f"no promotional strings OK: {len(violations)} frontend file(s) carrying {total} baselined "
        f"violation(s), {len(backend_violations)} backend file(s) carrying {backend_total}, "
        f"{len(allow)} allowlisted key(s), 0 new"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
