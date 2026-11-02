"""Start the product the way an installer starts it, and prove it answers.

The pytest lanes cannot see a startup-ordering defect. Every backend shard runs
with ``DATABASE_URL`` already in the environment - the Linux shards point it at
a postgres:16 service container, and conftest honours an already-set URL instead
of booting the embedded cluster - so a ``Settings`` object built before the
cluster exists already carries a working URL and a stale cache is harmless. The
product can be broken for everybody who installs it and still show twenty green
jobs. That happened: ``app/database.py`` builds its engine from
``get_settings()`` at import time, the CLI reads settings while it builds its
argument parser, and every cold start died with "DATABASE_URL must be a
PostgreSQL URL" while the cluster was up.

This probe is the lane that cannot be fooled that way. It refuses to run at all
if ``DATABASE_URL`` or ``DATABASE_SYNC_URL`` is already set, because on a
runner that already has one this test would prove nothing. It then starts the
real command - ``python -m app.cli serve`` - against an empty data directory
with demo data off, exactly the first run of a new install, and waits for
``/api/health`` (unauthenticated by design) to answer 200.

Stdlib only, so it runs on the three runner images without installing anything
beyond the backend itself. The child's stdout and stderr are inherited, so the
CLI's own STAGE lines and any traceback land in the job log verbatim.

Usage:
    python scripts/ci/cli_cold_start_probe.py
    python scripts/ci/cli_cold_start_probe.py --port 8765 --timeout 900
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

#: The backend package lives here; the CLI is started from it.
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

#: Variables that would let the CLI skip booting its own cluster, which is the
#: only thing this probe is interested in.
FORBIDDEN_ENV = ("DATABASE_URL", "DATABASE_SYNC_URL")


def _fail(message: str, child: subprocess.Popen | None = None, data_dir: Path | None = None) -> int:
    """Report why the product did not come up, then stop it.

    The embedded cluster's own log is named because that is where the cause of a
    database failure is, and a job log that ends at "timed out" sends the next
    reader looking in the wrong place.
    """
    print(f"\n::error::CLI cold start failed: {message}", flush=True)

    if data_dir is not None:
        pg_log = data_dir / "pgdata" / "log"
        print(f"data directory: {data_dir}", flush=True)
        if data_dir.exists():
            print("data directory contents: " + ", ".join(sorted(p.name for p in data_dir.iterdir())), flush=True)
        if pg_log.is_dir():
            logs = sorted(pg_log.glob("*.log"), key=lambda p: p.stat().st_mtime)
            if logs:
                print(f"--- tail of {logs[-1].name} ---", flush=True)
                tail = logs[-1].read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
                print("\n".join(tail), flush=True)

    _stop(child)
    return 1


def _stop(child: subprocess.Popen | None) -> None:
    """Stop the CLI, and on Windows its whole tree.

    The embedded postmaster is a grandchild: terminating the Python process
    alone would leave it holding the data directory on a Windows runner.
    """
    if child is None or child.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)], check=False, capture_output=True)
        return
    child.terminate()
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()


def _probe_health(port: int) -> tuple[int, str]:
    """One attempt at /api/health. Returns (status, body)."""
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - fixed loopback URL
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # a status is an answer, just not a good one
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="port the CLI should bind (default 8765)")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait for health (default 900)")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="data directory to use; it must be empty or absent (default: a fresh temporary one)",
    )
    parser.add_argument(
        "--keep-data-dir",
        action="store_true",
        help="leave a data directory this probe created behind (for a later step to inspect)",
    )
    args = parser.parse_args()

    inherited = [name for name in FORBIDDEN_ENV if os.environ.get(name)]
    if inherited:
        print(
            f"::error::{' and '.join(inherited)} is already set in this environment. This probe exists to start the "
            "product with no database configured, the way a new install does; with one set it would prove nothing. "
            "Unset it in the calling job.",
            flush=True,
        )
        return 1

    if not BACKEND_DIR.is_dir():
        print(f"::error::no backend directory at {BACKEND_DIR}", flush=True)
        return 1

    created = False
    data_dir = args.data_dir
    if data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="oe-cold-start-"))
        created = True
    elif data_dir.exists() and any(data_dir.iterdir()):
        print(f"::error::--data-dir {data_dir} is not empty; a cold start needs a clean one", flush=True)
        return 1

    env = dict(os.environ)
    # Demo data off: a cold start must come up on an empty database, and the
    # seeder would only make the first boot slower and less representative.
    env["SEED_DEMO"] = "false"
    for name in (*FORBIDDEN_ENV, "OE_DATA_DIR", "DATA_DIR", "OE_CLI_DATA_DIR"):
        env.pop(name, None)

    command = [
        sys.executable,
        "-m",
        "app.cli",
        "serve",
        "--data-dir",
        str(data_dir),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
    ]
    print(f"data directory: {data_dir}", flush=True)
    print("starting: " + " ".join(command), flush=True)

    started = time.monotonic()
    child: subprocess.Popen | None = None
    try:
        child = subprocess.Popen(
            command,
            cwd=str(BACKEND_DIR),
            env=env,
            stdin=subprocess.DEVNULL,  # no terminal: the first-run prompt must not be able to block the lane
        )

        deadline = started + args.timeout
        last_status = 0
        announced = 0.0
        while time.monotonic() < deadline:
            exit_code = child.poll()
            if exit_code is not None:
                return _fail(f"the CLI exited with code {exit_code} before /api/health answered", child, data_dir)

            last_status, body = _probe_health(args.port)
            if last_status == 200:
                elapsed = time.monotonic() - started
                print(f"\n/api/health answered 200 after {elapsed:.1f}s", flush=True)
                try:
                    print("health payload: " + json.dumps(json.loads(body), sort_keys=True), flush=True)
                except json.JSONDecodeError:
                    print(f"health payload (not JSON): {body[:400]}", flush=True)

                # The cluster has to be the embedded one this boot created, not
                # something that was already there: a cold start that answers
                # without having initialised a database is not a cold start.
                pg_version = data_dir / "pgdata" / "PG_VERSION"
                if not pg_version.exists():
                    return _fail(f"health answered but {pg_version} was never written", child, data_dir)
                print(f"embedded cluster initialised: {pg_version.read_text(encoding='utf-8').strip()}", flush=True)
                return 0

            now = time.monotonic()
            if now - announced >= 15:
                announced = now
                state = f"last status {last_status}" if last_status else "not listening yet"
                print(f"  waiting for /api/health ... {now - started:.0f}s ({state})", flush=True)
            time.sleep(2)

        return _fail(f"/api/health did not answer 200 within {args.timeout:.0f}s", child, data_dir)
    finally:
        _stop(child)
        if created and not args.keep_data_dir:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
