"""Unit tests for the demo-seed choice mechanism (issue #272 / demo sign-in).

These cover the precedence that ``seed_demo_enabled()`` encodes and that both
CLI flags rely on: ``SEED_DEMO`` in the environment wins, then the persisted
``demo_seed_choice.json`` in the data dir, then the out-of-the-box default
of off: demo data is opt-in, so a fresh install with no answer yet boots an
empty workspace. ``serve --no-demo`` persists a ``False`` choice;
``serve --demo`` persists ``True`` to opt in and bring the demo sign-in
back.

Pure filesystem + environment, no database, so this runs everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.demo_seed import (
    CHOICE_FILENAME,
    choice_path,
    read_demo_seed_choice,
    seed_demo_enabled,
    write_demo_seed_choice,
)


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the choice resolver at a scratch dir with no SEED_DEMO override."""
    monkeypatch.setenv("OE_DATA_DIR", str(tmp_path))
    for env_name in ("SEED_DEMO", "DATA_DIR", "OE_CLI_DATA_DIR"):
        monkeypatch.delenv(env_name, raising=False)
    return tmp_path


def test_defaults_to_disabled_when_nothing_is_set(isolated_data_dir: Path) -> None:
    # Opt-in: with no env var and no persisted choice, nothing seeds. The
    # interactive first run asks ("Load demo projects? [y/N]") and persists
    # the answer; unattended starts stay empty until someone opts in.
    assert read_demo_seed_choice() is None
    assert seed_demo_enabled() is False


def test_no_demo_choice_disables_seeding(isolated_data_dir: Path) -> None:
    # This is what ``serve --no-demo`` (and the in-app "remove demo data"
    # action) persist.
    assert write_demo_seed_choice(False) is True
    assert (isolated_data_dir / CHOICE_FILENAME).exists()
    assert read_demo_seed_choice() is False
    assert seed_demo_enabled() is False


def test_demo_choice_reenables_seeding(isolated_data_dir: Path) -> None:
    # The regression path: an install that opted out, then ``serve --demo``
    # writes ``True`` and the demo sign-in returns.
    write_demo_seed_choice(False)
    assert seed_demo_enabled() is False

    assert write_demo_seed_choice(True) is True
    assert read_demo_seed_choice() is True
    assert seed_demo_enabled() is True


def test_env_var_overrides_persisted_choice(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # SEED_DEMO always wins over the file, in both directions.
    write_demo_seed_choice(False)
    monkeypatch.setenv("SEED_DEMO", "true")
    assert seed_demo_enabled() is True

    write_demo_seed_choice(True)
    for falsy in ("false", "0", "no"):
        monkeypatch.setenv("SEED_DEMO", falsy)
        assert seed_demo_enabled() is False


def test_explicit_data_dir_is_honoured(tmp_path: Path) -> None:
    # The CLI passes its resolved --data-dir explicitly; the choice must land
    # in and read back from that exact dir.
    write_demo_seed_choice(False, tmp_path)
    assert choice_path(tmp_path) == tmp_path / CHOICE_FILENAME
    assert read_demo_seed_choice(tmp_path) is False
    write_demo_seed_choice(True, tmp_path)
    assert read_demo_seed_choice(tmp_path) is True


def test_corrupt_choice_file_is_treated_as_unset(isolated_data_dir: Path) -> None:
    (isolated_data_dir / CHOICE_FILENAME).write_text("{ not json", encoding="utf-8")
    assert read_demo_seed_choice() is None
    assert seed_demo_enabled() is False
