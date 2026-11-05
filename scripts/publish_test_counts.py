#!/usr/bin/env python3
# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Turn a pytest JUnit report into counts a reviewer can read without the logs.

Why this exists: this repository's CI runs on a fork, the Actions job logs are not
downloadable from here, and a green check mark does not say what ran. A job that
concludes ``success`` after collecting nothing looks exactly like one that ran
eleven tests, and a target test that is silently skipped - because an environment
variable was not set, or because the file was never collected by the selector - is
indistinguishable from one that passed. Both have happened here.

So the workflow writes a JUnit report per pytest step, and this script reads the
reports back and publishes, per named target file: how many tests were collected,
how many passed, failed, errored and were skipped. It then fails when a target
collected nothing or when a target test was skipped in the PostgreSQL lane, which
is the part of the contract that cannot be checked by reading a conclusion.

Usage::

    python scripts/publish_test_counts.py \
        --junit "$RUNNER_TEMP/pg-lane.xml" \
        --target tests/pg/test_tenancy_backfill_on_a_populated_database.py \
        --fail-on-skip

The summary is appended to ``$GITHUB_STEP_SUMMARY`` when that variable is set, and
written to stdout otherwise, so the same command is useful locally.

Standard library only, and it parses rather than imports: it has to run in a job
whose Python may not have the application's dependencies installed.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def _module_of(classname: str) -> str:
    """The dotted module a ``classname`` belongs to, without any test class.

    pytest reports ``tests.pg.test_x`` for a module-level test and
    ``tests.pg.test_x.TestGroup`` for one inside a class, so the module is the
    longest dotted prefix that corresponds to a real file.
    """
    parts = [part for part in classname.split(".") if part]
    if not parts:
        # A collection error carries no classname at all, because there is no test
        # to attribute it to. Empty means "no file to point at", not "the root".
        return ""
    for index in range(len(parts), 0, -1):
        candidate = Path(*parts[:index])
        if candidate.with_suffix(".py").exists() or (candidate / "__init__.py").exists():
            return ".".join(parts[:index])
    return classname


def collect(junit_paths: list[Path]) -> dict[str, dict[str, int]]:
    """Per dotted module, the outcome counts from every report given."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for path in junit_paths:
        tree = ET.parse(path)
        for case in tree.getroot().iter("testcase"):
            module = _module_of(case.get("classname") or "")
            bucket = counts[module]
            bucket["collected"] += 1
            if case.find("failure") is not None:
                bucket["failed"] += 1
            elif case.find("error") is not None:
                bucket["errored"] += 1
            elif case.find("skipped") is not None:
                bucket["skipped"] += 1
            else:
                bucket["passed"] += 1
    return {module: dict(bucket) for module, bucket in counts.items()}


#: GitHub keeps at most ten annotations of each level per step, so a suite with
#: sixty failures annotates the first ten and says how many it left out. Folding
#: that count into the totals notice rather than adding an eleventh is the point:
#: an annotation past the cap is dropped silently, and a reader who sees ten
#: errors and no mention of the rest believes ten is the answer.
_ANNOTATION_LIMIT = 10


def _row_name(module: str) -> str:
    """A module dotted path as a file, or a label when there is no module."""
    return module.replace(".", "/") + ".py" if module else "<collection errors>"


def _escape(text: str) -> str:
    """Escape a payload for a ``::notice::``/``::error::`` workflow command.

    These commands are one line each, so a message carrying a newline would end
    the command and print the rest of the traceback as ordinary output. Percent
    is escaped first because the escapes it produces contain one.
    """
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def failures(junit_paths: list[Path]) -> list[dict[str, str]]:
    """Every failed or errored test case, with the file to attach it to."""
    found: list[dict[str, str]] = []
    for path in junit_paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            node = case.find("failure")
            kind = "failure"
            if node is None:
                node, kind = case.find("error"), "error"
            if node is None:
                continue
            classname = case.get("classname") or ""
            message = (node.get("message") or node.text or "").strip().splitlines()
            found.append(
                {
                    "file": _row_name(_module_of(classname)) if classname.strip(".") else "",
                    "line": case.get("line") or "",
                    "test": f"{classname}::{case.get('name')}",
                    "kind": kind,
                    "message": (message[0] if message else kind)[:300],
                }
            )
    return found


def annotate(rows: list[tuple[str, dict[str, int]]], found: list[dict[str, list]], prefix: str) -> None:
    """Emit the counts and the failures as workflow commands.

    A step summary is only readable in the web UI: the REST API returns an empty
    ``output.summary`` for a job, and the job log is not downloadable from every
    environment. Annotations are readable through ``/check-runs/<id>/annotations``,
    which is the difference between "the job was green" and "these tests ran, with
    these counts, and this one failed for this reason".
    """
    totals = {
        key: sum(bucket.get(key, 0) for _, bucket in rows)
        for key in ("collected", "passed", "failed", "errored", "skipped")
    }
    for target, bucket in rows[: _ANNOTATION_LIMIT - 1]:
        counts = " ".join(
            f"{key}={bucket.get(key, 0)}" for key in ("collected", "passed", "failed", "errored", "skipped")
        )
        print(f"::notice::{_escape(f'{target}: {counts}')}")
    omitted_rows = len(rows) - (_ANNOTATION_LIMIT - 1)
    summary = (
        f"totals: collected={totals['collected']} passed={totals['passed']} "
        f"failed={totals['failed']} errored={totals['errored']} skipped={totals['skipped']}"
    )
    if omitted_rows > 0:
        summary += f"; {omitted_rows} more target(s) in the step summary table"
    omitted_failures = len(found) - _ANNOTATION_LIMIT
    if omitted_failures > 0:
        summary += f"; {omitted_failures} more failure(s) beyond the ten annotated"
    print(f"::notice::{_escape(summary)}")

    for item in found[:_ANNOTATION_LIMIT]:
        # A collection error has no file to attach to, and an annotation naming a
        # path that does not exist is worse than one that names nothing: the API
        # returns it either way, but only one of them can be clicked.
        where = f"file={prefix}{item['file']}" if item["file"] else ""
        if where and item["line"]:
            where += f",line={item['line']}"
        print(f"::error{' ' + where if where else ''}::{_escape(item['test'] + ' ' + item['message'])}")


def render(rows: list[tuple[str, dict[str, int]]], title: str) -> str:
    """The markdown table that goes into the step summary."""
    lines = [
        title,
        "",
        "Counts read back from the pytest JUnit reports this job wrote, so a green",
        "conclusion is not the only evidence that these ran.",
        "",
        "| target | collected | passed | failed | errored | skipped |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for target, bucket in rows:
        lines.append(
            f"| `{target}` | {bucket.get('collected', 0)} | {bucket.get('passed', 0)} | "
            f"{bucket.get('failed', 0)} | {bucket.get('errored', 0)} | {bucket.get('skipped', 0)} |"
        )
    totals = {
        key: sum(bucket.get(key, 0) for _, bucket in rows)
        for key in ("collected", "passed", "failed", "errored", "skipped")
    }
    lines.append(
        f"| **total** | **{totals['collected']}** | **{totals['passed']}** | "
        f"**{totals['failed']}** | **{totals['errored']}** | **{totals['skipped']}** |"
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--junit",
        action="append",
        required=True,
        help="a pytest JUnit XML report; repeatable",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=None,
        help="a test file to report on; repeatable. Without it every module in the report is a row",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="also emit the counts and every failure as workflow-command annotations",
    )
    parser.add_argument(
        "--title",
        default="### Access-control and tenancy test counts",
        help="heading for the step summary table",
    )
    parser.add_argument(
        "--fail-on-skip",
        action="store_true",
        help="exit non-zero if any test in a target file was skipped",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="directory the target paths are relative to (default: the repository root)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[1]
    backend = root / "backend"
    search = backend if backend.is_dir() else root
    os.chdir(search)

    reports = [Path(item) for item in args.junit]
    missing = [str(item) for item in reports if not item.is_file()]
    if missing:
        print(f"::error::no JUnit report at {', '.join(missing)}", file=sys.stderr)
        return 2

    counts = collect(reports)
    prefix = "backend/" if search.name == "backend" else ""
    rows: list[tuple[str, dict[str, int]]] = []
    problems: list[str] = []
    if not args.target:
        rows = [(_row_name(module), bucket) for module, bucket in sorted(counts.items())]
        if args.fail_on_skip:
            problems.extend(
                f"{target} skipped {bucket['skipped']} test(s)"
                for target, bucket in rows
                if bucket.get("skipped") and bucket.get("collected")
            )
        found = failures(reports)
        if args.annotate:
            annotate(rows, found, prefix)
        table = render(rows[:60], args.title)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as handle:
                handle.write(table + "\n")
        print(table)
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        return 1 if problems else 0

    for target in args.target:
        relative = Path(target).relative_to(root) if Path(target).is_absolute() else Path(target)
        module = ".".join(relative.with_suffix("").parts)
        bucket = counts.get(module, {})
        rows.append((str(relative), bucket))
        if not bucket.get("collected"):
            problems.append(f"{relative} collected no tests")
        elif args.fail_on_skip and bucket.get("skipped"):
            problems.append(f"{relative} skipped {bucket['skipped']} test(s)")

    if args.annotate:
        annotate(rows, failures(reports), prefix)

    table = render(rows, args.title)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(table + "\n")
    print(table)

    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
