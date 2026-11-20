"""Startup must finish wiring DATABASE_URL before anything caches Settings.

``app/database.py`` builds its engine while the module is being imported
(``engine = create_engine_from_settings()``), and that function refuses to
continue unless ``get_settings().database_url`` starts with ``postgresql``.
The refusal is correct and stays: it is what stops a misconfigured install
from silently falling back to another engine.

``get_settings()`` is ``@lru_cache``d, though, and ``Settings`` reads the
environment once, when it is constructed. The embedded cluster does not exist
yet at that point - ``embedded_pg.boot()`` writes ``DATABASE_URL`` and
``DATABASE_SYNC_URL`` into the process environment only after PostgreSQL has
come up. So a Settings object built *before* the boot keeps an empty
``database_url`` forever, and the first import of ``app.database`` dies with
"DATABASE_URL must be a PostgreSQL URL" even though the cluster is running and
the environment is correct. ``app/core/embedded_pg.py`` says as much in its
ordering contract: boot() must run before ``get_settings()`` is cached.

The CLI cannot honour that contract by ordering alone, because the read
happens before any command runs: ``main()`` builds the argument parser first,
and the parser's help and description strings carry the display name
(``_app_name()``, which reads ``app_name`` from Settings). Building a parser
therefore caches a Settings object that predates the database. The bare
first-run greeting reads the same name a second time.

These tests drive that real sequence - build the parser, resolve the data
directory, run ``_setup_env`` - for every way the product is started from a
terminal, with demo data off, and then assert what ``app/database.py`` is
about to see. Only the cluster acquisition is replaced, by a double that
performs the observable tail of the real ``boot()``: turning the cluster URI
into the two environment variables. That keeps the test in the fast lane on
all three operating systems instead of needing a PostgreSQL binary.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app import cli
from app.config import get_settings
from app.core import embedded_pg

#: Shaped like the URI pixeltable-pgserver hands back on the TCP (Windows)
#: path. The password is a placeholder: nothing connects to it.
_CLUSTER_URI = "postgresql://oe:placeholder-not-used@127.0.0.1:5432/oe"

#: How a terminal starts the product, and how the data directory is chosen.
#:
#: ``serve`` takes ``--data-dir``. The bare invocation does not: the top-level
#: parser never gets ``_add_common_server_args``, so its data directory comes
#: from ``OE_DATA_DIR`` / ``DATA_DIR`` (see ``_data_dir_from_args``). Both
#: spellings of "an explicit data directory" are covered here, each the way the
#: product actually accepts it, because the regression is about ordering and
#: not about one flag.
_STARTERS = (
    pytest.param((), "default", id="bare-command-default-data-dir"),
    pytest.param((), "explicit", id="bare-command-data-dir-from-the-environment"),
    pytest.param(("serve",), "default", id="serve-default-data-dir"),
    pytest.param(("serve",), "explicit", id="serve-explicit-data-dir"),
)


@pytest.fixture
def first_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A process that has not been told where the database is.

    ``_setup_env`` writes several process-wide variables of its own
    (``SERVE_FRONTEND``, ``OE_CLI_*``, ``JWT_SECRET``, ...), so the whole
    environment is snapshotted and put back rather than restored key by key:
    a startup test that leaks a data directory into the rest of the suite
    would be worse than no test.
    """
    saved = dict(os.environ)

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_SYNC_URL", raising=False)
    # Demo data off: the seeder is not part of a startup contract.
    monkeypatch.setenv("SEED_DEMO", "false")

    # DEFAULT_DATA_DIR is resolved once, when app.cli is imported, from
    # OE_DATA_DIR / DATA_DIR / OE_CLI_DATA_DIR - and it is what both
    # _data_dir_from_args and the parser's --data-dir default fall back to.
    # Anchoring it (and the variable it came from) keeps a startup test from
    # creating directories in the runner's real home.
    default_volume = tmp_path / "default-volume"
    monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", default_volume)
    monkeypatch.setenv("OE_DATA_DIR", str(default_volume))
    get_settings.cache_clear()

    try:
        yield tmp_path
    finally:
        os.environ.clear()
        os.environ.update(saved)
        get_settings.cache_clear()


def _cluster_came_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the cluster acquisition, and nothing else in the startup path.

    The double performs exactly the tail of the real ``boot()`` - the "URL
    wiring" block in ``app/core/embedded_pg.py`` that turns ``srv.get_uri()``
    into ``DATABASE_URL`` and ``DATABASE_SYNC_URL`` - and reports success.
    ``_setup_env`` then runs its real body, including everything it does after
    a successful boot.
    """

    def _boot(data_dir: Path) -> bool:
        base = make_url(_CLUSTER_URI)
        os.environ["DATABASE_URL"] = base.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
        os.environ["DATABASE_SYNC_URL"] = base.set(drivername="postgresql+psycopg2").render_as_string(
            hide_password=False
        )
        return True

    monkeypatch.setattr(embedded_pg, "is_requested", lambda: True)
    monkeypatch.setattr(embedded_pg, "boot", _boot)
    monkeypatch.setattr(
        embedded_pg,
        "auto_migrate_legacy_sqlite",
        lambda data_dir: "skipped: no legacy SQLite database",
    )


def _start_the_product(argv_tail: tuple[str, ...], data_dir_choice: str, root: Path, monkeypatch) -> Path:
    """Run main()'s real order up to the point where the server is imported.

    Returns:
        The data directory the invocation resolved.
    """
    argv = list(argv_tail)
    if data_dir_choice == "explicit":
        elsewhere = root / "chosen-volume"
        if argv:
            argv += ["--data-dir", str(elsewhere)]
        else:
            # The bare invocation declares no --data-dir (the top-level parser
            # never gets _add_common_server_args), so an operator points it
            # somewhere else with OE_DATA_DIR - which app.cli already resolved
            # into DEFAULT_DATA_DIR at import time. Setting both is the same
            # process, expressed where this test can reach it.
            monkeypatch.setattr(cli, "DEFAULT_DATA_DIR", elsewhere)
            monkeypatch.setenv("OE_DATA_DIR", str(elsewhere))

    # main() builds the parser before it looks at the command. Four of the
    # parser's help strings carry the display name, so this is a real
    # get_settings() read happening before any command has run - the read that
    # predates the database.
    parser = cli._build_parser()
    args = parser.parse_args(argv)

    _cluster_came_up(monkeypatch)
    resolved = cli._data_dir_from_args(args)
    cli._setup_env(resolved, "127.0.0.1", 8765)
    return resolved


def test_building_the_parser_caches_settings_before_a_database_exists(first_run: Path) -> None:
    """The premise, pinned so the fix cannot be reverted into an ordering argument.

    If this ever fails, the parser stopped reading Settings early - which would
    be a legitimate way to fix the regression too, and would make the rest of
    this file redundant rather than wrong.
    """
    cli._build_parser()

    assert get_settings.cache_info().currsize >= 1, "the parser no longer caches Settings; re-read this file"
    assert get_settings().database_url == "", (
        "Settings already knew a database URL before the cluster was booted, so this "
        "process is not reproducing a first run"
    )


@pytest.mark.parametrize(("argv_tail", "data_dir_choice"), _STARTERS)
def test_every_way_of_starting_ends_with_a_postgres_url_the_engine_accepts(
    argv_tail: tuple[str, ...],
    data_dir_choice: str,
    first_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the startup path has run, the URL exists and the engine builds."""
    _start_the_product(argv_tail, data_dir_choice, first_run, monkeypatch)

    url = get_settings().database_url
    assert url.startswith("postgresql"), (
        f"get_settings() still returns {url!r} after the startup path booted the cluster: "
        "Settings was cached before DATABASE_URL existed and was never invalidated. "
        "app/database.py builds its engine from this value at import time and refuses "
        "to start on anything that is not a PostgreSQL URL."
    )

    # The consequence rather than the setting: this is the call
    # app/database.py makes while it is being imported.
    from app.database import create_engine_from_settings

    engine = create_engine_from_settings()
    try:
        assert engine.url.drivername.startswith("postgresql")
    finally:
        engine.dispose()


@pytest.mark.parametrize(("argv_tail", "data_dir_choice"), _STARTERS)
def test_the_startup_path_reaches_the_data_directory_it_was_given(
    argv_tail: tuple[str, ...],
    data_dir_choice: str,
    first_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The double must not be masking a startup path that never ran.

    ``_setup_env`` creates the data directory and publishes it for the banner;
    if it silently did nothing, the URL assertion above could pass for the
    wrong reason.
    """
    resolved = _start_the_product(argv_tail, data_dir_choice, first_run, monkeypatch)

    assert resolved.is_dir(), f"_setup_env did not create the data directory at {resolved}"
    assert first_run in resolved.parents, f"the startup path left the data directory at {resolved}"
    assert os.environ["OE_CLI_DATA_DIR"] == str(resolved)


def test_the_serve_command_can_import_the_application_after_the_startup_path(
    first_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``cmd_serve``'s last act is ``uvicorn.run("app.main:create_app")``.

    This is the import that failed with "DATABASE_URL must be a PostgreSQL
    URL": app.main pulls in app.dependencies, which pulls in app.database,
    which builds the engine at import time.
    """
    _start_the_product(("serve",), "explicit", first_run, monkeypatch)

    import app.main

    assert callable(app.main.create_app)
