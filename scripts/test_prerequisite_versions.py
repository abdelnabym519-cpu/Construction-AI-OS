"""The runtimes the README asks a developer to install are declared where a tool reads them.

`README.md` tells a person what to have before `make dev` can work: "Needs
Python 3.12+ and Node.js 22 or newer." Half of that sentence was backed by
something and half was not. Python's floor is `requires-python` in
`backend/pyproject.toml`, which pip and uv refuse to install past. Node's floor
was prose: nothing in the repository declared it, so nothing could contradict
it, and the fourteen places that actually choose a Node - fifteen workflow pins
and two Docker base images - were free to drift away from the sentence a
developer reads first.

Every check here is a comparison between two things that already exist, so none
of them can pass by finding nothing:

  * the README's own numbers, read out of its text;
  * the two manifests that declare a floor a package manager enforces;
  * every Node and Python the repository runs, in CI and in Docker.

A floor stated in prose and enforced nowhere is not a floor, it is a hope. The
point of tying the three together is that changing any one of them without the
others is now a failing test rather than a sentence nobody re-reads.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
FRONTEND_MANIFEST = ROOT / "frontend" / "package.json"
BACKEND_MANIFEST = ROOT / "backend" / "pyproject.toml"

#: Where the repository chooses a runtime, and the shapes those choices take.
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
DOCKERFILES = sorted((ROOT / "deploy" / "docker").glob("Dockerfile*"))

_NODE_PIN = re.compile(r"node-version:\s*['\"]?(\d+)(?:\.\d+)*")
_NODE_MATRIX = re.compile(r"^\s*node:\s*\[([^\]]*)\]", re.MULTILINE)
_NODE_IMAGE = re.compile(r"FROM node:(\d+)")
_PYTHON_PIN = re.compile(r"python-version:\s*['\"]?(\d+)\.(\d+)")
_PYTHON_MATRIX = re.compile(r"^\s*python:\s*\[([^\]]*)\]", re.MULTILINE)
_QUOTED_VERSION = re.compile(r"['\"](\d+)(?:\.(\d+))?['\"]")


def _readme_node_majors() -> list[int]:
    text = README.read_text(encoding="utf-8")
    return [int(major) for major in re.findall(r"Node\.js\s+v?(\d+)(?:\.\d+)*", text)]


def _readme_python_floors() -> list[tuple[int, int]]:
    text = README.read_text(encoding="utf-8")
    return [(int(major), int(minor)) for major, minor in re.findall(r"Python\s+(\d+)\.(\d+)", text)]


def _declared_node_floor() -> int:
    """The major version `frontend/package.json` refuses to run below."""
    manifest = json.loads(FRONTEND_MANIFEST.read_text(encoding="utf-8"))
    engines = manifest.get("engines") or {}
    node = engines.get("node")
    assert isinstance(node, str), (
        f"{FRONTEND_MANIFEST.relative_to(ROOT)} declares no engines.node, so the Node floor a developer is told "
        "to meet is written in one place only, the README, and nothing that installs or runs the frontend can "
        "disagree with it - which is how a sentence and a toolchain drift apart without either of them failing."
    )
    match = re.fullmatch(r"\s*>=\s*(\d+)(?:\.\d+)*\s*", node)
    assert match is not None, f"engines.node is {node!r}, which is not a floor of the form '>=<major>'"
    return int(match.group(1))


def _declared_python_floor() -> tuple[int, int]:
    """The version `backend/pyproject.toml` refuses to install below."""
    with BACKEND_MANIFEST.open("rb") as handle:
        declared = tomllib.load(handle)["project"]["requires-python"]
    match = re.fullmatch(r"\s*>=\s*(\d+)\.(\d+)\s*", declared)
    assert match is not None, f"requires-python is {declared!r}, which is not a floor of the form '>=<major>.<minor>'"
    return int(match.group(1)), int(match.group(2))


def _node_choices() -> dict[str, list[tuple[str, int]]]:
    """Every Node major this repository picks, by the kind of place that picks it.

    Keyed by kind rather than returned as one list, so a collector that stops
    collecting is a failure of its own and not a smaller number in a pile that
    still has entries from somewhere else.
    """
    pins: list[tuple[str, int]] = []
    images: list[tuple[str, int]] = []
    matrices: list[tuple[str, int]] = []
    for path in [*WORKFLOWS, *DOCKERFILES]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        where = str(path.relative_to(ROOT))
        pins += [(where, int(major)) for major in _NODE_PIN.findall(text)]
        images += [(where, int(major)) for major in _NODE_IMAGE.findall(text)]
        for body in _NODE_MATRIX.findall(text):
            matrices += [(where, int(major)) for major, _minor in _QUOTED_VERSION.findall(body) if major]
    return {"a workflow's node-version pin": pins, "a Docker base image": images, "a workflow matrix": matrices}


def _python_choices() -> dict[str, list[tuple[str, tuple[int, int]]]]:
    """Every Python major.minor this repository picks, by the kind of place that picks it."""
    pins: list[tuple[str, tuple[int, int]]] = []
    matrices: list[tuple[str, tuple[int, int]]] = []
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8", errors="ignore")
        where = str(path.relative_to(ROOT))
        pins += [(where, (int(major), int(minor))) for major, minor in _PYTHON_PIN.findall(text)]
        for body in _PYTHON_MATRIX.findall(text):
            matrices += [
                (where, (int(major), int(minor))) for major, minor in _QUOTED_VERSION.findall(body) if major and minor
            ]
    return {"a workflow's python-version pin": pins, "a workflow matrix": matrices}


#: The kinds each comparison has to have read something from. A workflow matrix
#: is not in the Node list because no workflow carries one today; the collector
#: reads it anyway, so the first one that appears is checked.
REQUIRED_NODE_SOURCES = ("a workflow's node-version pin", "a Docker base image")
REQUIRED_PYTHON_SOURCES = ("a workflow's python-version pin", "a workflow matrix")


def test_the_readme_states_a_floor_for_both_runtimes() -> None:
    """Guard the guards: the sentences this file reads are still there.

    Without this, a reworded README makes every comparison below an empty one
    and the file passes by measuring nothing.
    """
    assert _readme_node_majors(), f"{README.name} no longer names a Node.js version anywhere"
    assert _readme_python_floors(), f"{README.name} no longer names a Python version anywhere"
    assert len(WORKFLOWS) > 10, f"only {len(WORKFLOWS)} workflows found; the glob has moved"
    assert DOCKERFILES, "no Dockerfiles found under deploy/docker; the path has moved"


def test_the_readme_states_one_node_floor_and_the_manifest_declares_it() -> None:
    stated = sorted(set(_readme_node_majors()))
    declared = _declared_node_floor()

    assert stated == [declared], (
        f"{README.name} tells a developer Node.js {stated} and {FRONTEND_MANIFEST.relative_to(ROOT)} declares "
        f"engines.node '>= {declared}'. One floor, stated once to a person and once to npm."
    )


def test_the_readme_states_one_python_floor_and_the_manifest_declares_it() -> None:
    stated = sorted(set(_readme_python_floors()))
    declared = _declared_python_floor()

    spelled = ", ".join(f"{major}.{minor}" for major, minor in stated)
    assert stated == [declared], (
        f"{README.name} tells a developer Python {spelled} and "
        f"{BACKEND_MANIFEST.relative_to(ROOT)} declares requires-python '>= {declared[0]}.{declared[1]}'."
    )


def test_every_node_this_repository_runs_meets_the_declared_floor() -> None:
    choices = _node_choices()
    floor = _declared_node_floor()
    read_nothing = [kind for kind in REQUIRED_NODE_SOURCES if not choices[kind]]
    below = sorted({(where, major) for entries in choices.values() for where, major in entries if major < floor})

    assert read_nothing == [], (
        f"no Node version was read from {read_nothing}, so this comparison ran on part of the evidence. Every "
        "kind named in REQUIRED_NODE_SOURCES is a place this repository chooses a Node today."
    )
    assert below == [], (
        f"{len(below)} place(s) run a Node older than the engines.node floor of {floor}: {below}. Either the "
        "floor is wrong and should be lowered in the manifest and the README together, or that runner is "
        "building the frontend on a Node no developer was told about."
    )


def test_every_python_this_repository_runs_meets_the_declared_floor() -> None:
    choices = _python_choices()
    floor = _declared_python_floor()
    read_nothing = [kind for kind in REQUIRED_PYTHON_SOURCES if not choices[kind]]
    below = sorted({(where, pair) for entries in choices.values() for where, pair in entries if pair < floor})

    assert read_nothing == [], (
        f"no Python version was read from {read_nothing}, so this comparison ran on part of the evidence."
    )
    assert below == [], (
        f"{len(below)} place(s) run a Python older than requires-python's {floor[0]}.{floor[1]}: {below}. A "
        "lane on an older interpreter is not testing what ships, and the guards that parse this repository's "
        "own source quietly answer a different question on one."
    )


@pytest.mark.parametrize("path", [FRONTEND_MANIFEST, BACKEND_MANIFEST], ids=lambda p: p.name)
def test_the_manifests_that_carry_the_floors_are_the_ones_the_tools_read(path: Path) -> None:
    """A floor in a file nothing installs from is the same prose with extra steps."""
    assert path.is_file(), f"{path.relative_to(ROOT)} is not in the repository"
    text = path.read_text(encoding="utf-8")

    if path is FRONTEND_MANIFEST:
        manifest = json.loads(text)
        assert manifest.get("name") == "openconstructionerp-frontend"
        assert "engines" in manifest, "the frontend manifest carries no engines block"
    else:
        assert "[project]" in text
        assert "requires-python" in text
