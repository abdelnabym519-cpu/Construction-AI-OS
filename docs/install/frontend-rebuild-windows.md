# Rebuilding the frontend on Windows

Written for an operator who has this repository on a Windows machine and needs
the UI the CLI serves to match the source they are looking at. Every step below
is exact. Steps that were not executed on a Windows machine while this document
was written are marked **UNVERIFIED** - they follow from how the build is wired,
not from a run anybody has observed.

## What the CLI actually serves

The server does not run a dev server and it does not read `frontend/src`. It
serves a **prebuilt bundle**:

* `backend/app/cli_static.py` exposes `get_frontend_dir()`, which resolves to
  `app/_frontend_dist` **inside the installed Python package**.
* `backend/pyproject.toml` puts it there: the wheel target force-includes
  `../frontend/dist` as `app/_frontend_dist`.
* `backend/app/cli.py` passes `serve_frontend` when it starts the app. If no
  bundle is found it says so and serves the API only, at `/api/docs`. Its own
  repair hint is: *"Reinstall the pip package to get the bundled UI, or run
  `npm run build` in frontend/"*.

Two consequences that surprise people:

1. **`git pull` does not update the UI.** The bundle lives in the installed
   package, not in the checkout. Pulling new frontend source changes nothing
   until the bundle is rebuilt *and* the server is pointed at the new one.
2. **`pip install openconstructionerp` gives you the bundle that was built when
   that wheel was published** - not one built from your working tree.

So there are two different jobs, and it matters which one you want:

| You want | Do |
| --- | --- |
| the published UI, as shipped | `pip install --upgrade openconstructionerp` |
| the UI from *your* working tree | rebuild `frontend/dist`, then reinstall or repoint (below) |

## Prerequisites

* Node.js **(see package.json engines)** and npm. Check with `node -v` and `npm -v`.
  **UNVERIFIED** on Windows: the version constraint is read from
  `frontend/package.json`; whether a given Windows Node installer satisfies it
  was not tested here.
* The repository checked out, with `frontend/` present.
* Enough free RAM for the type check. See the heap note below - this is the step
  that fails on ordinary machines.

## Step 1 - install the frontend dependencies

```powershell
cd C:\path\to\OpenConstructionERP\frontend
npm ci --no-audit --no-fund
```

`npm ci` rather than `npm install`: it installs exactly what `package-lock.json`
records and refuses to drift from it. On a warm npm cache this took **17 seconds**
on the Linux machine where the cleanup was done; a cold cache on Windows will be
slower. **UNVERIFIED** on Windows.

## Step 2 - build

The build script is:

```
npm run typecheck && node --max-old-space-size=12288 ./node_modules/vite/bin/vite.js build
```

That is a whole-program `tsc --noEmit` type check followed by the Vite build, and
the type check is given a heap ceiling of **8192 MB** (`NODE_OPTIONS=--max-old-space-size=8192`).
This frontend is large enough that the type check needs it. On a machine with
less RAM than that, the process is killed by the operating system and the build
dies with an out-of-memory error rather than a TypeScript diagnostic - which
looks like a code problem and is not one.

### If `npm run build` runs out of memory

Build the bundle without the whole-program type check:

```powershell
npx vite build
```

This produces `frontend/dist` - the artefact the CLI serves - and skips the
`tsc --noEmit` pass that needs 8192 MB. **The bundle is the same either way**; what you
give up is the guarantee that no type error exists anywhere in the program. CI
runs the type check on every push, so a branch that builds here and fails there
is caught before it reaches anybody.

Two smaller levers if you would rather keep the type check:

* raise the page file (virtual memory) so the process can commit more than
  physical RAM - slow, but it completes. **UNVERIFIED**.
* lower the ceiling instead of raising it, e.g.
  `$env:NODE_OPTIONS="--max-old-space-size=6144"; npx tsc --noEmit` - this makes
  the failure faster and clearer, it does not make it pass. **UNVERIFIED**.

## Step 3 - get the new bundle in front of the server

Pick one.

**A. Reinstall the package from your tree** (what the wheel does in CI):

```powershell
cd C:\path\to\OpenConstructionERP\backend
pip install -e .
```

An editable install resolves `app/_frontend_dist` against your checkout, so the
bundle you just built is the one served. **UNVERIFIED** on Windows: the
force-include rule in `backend/pyproject.toml` is what makes the path line up,
and it was read, not exercised, here.

**B. Copy the bundle into the installed package.** Find where the package lives:

```powershell
python -c "import app.cli_static as c; print(c.get_frontend_dir())"
```

then copy `frontend\dist\*` over that directory and restart the server.
**UNVERIFIED** on Windows.

**C. Serve the fresh `dist` directly** while developing, and skip the bundle
entirely:

```powershell
cd C:\path\to\OpenConstructionERP\frontend
npx vite --host 0.0.0.0
```

The dev server proxies `/api` to the backend, so this is the fastest loop - but
it is a dev server, not what you ship. **UNVERIFIED** on Windows.

## Step 4 - confirm it took

```powershell
python -c "import app.cli_static as c, os; p=c.get_frontend_dir(); print(p, os.path.isdir(p))"
```

`True` means the server will serve a UI; `False` means it will start API-only and
say *"frontend not bundled"*. Then start the server and load the app. On first
boot the backend also creates tables, runs migrations and seeds the demo
workspace, which takes on the order of a minute; during that window the UI can
show a sign-in screen that cannot reach the API yet. That is a start-up state,
not a broken install, and the sign-in page now says so instead of reporting a
connection error.

## Other scripts, for reference

| Script | Command |
| --- | --- |
| tests | `vitest` |
| lint | `eslint .` |
| type check only | `node --max-old-space-size=12288 ./node_modules/typescript/bin/tsc --noEmit --incremental --tsBuildInfoFile node_modules/.cache/typecheck.tsbuildinfo && npm run typecheck:e2e` |

`npm run test` and `npm run typecheck` inherit the same heap ceiling as the
build, so the same out-of-memory note applies. Running a single test file
(`npx vitest run path/to/file.test.tsx`) needs far less memory than the whole
suite and is the practical way to check one change on a small machine.
