# Foundation audit

This page records what the platform actually is, as measured from the tree rather than
from any description of it. It is the evidence base for
[TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md) and
[ROADMAP_STATUS.md](./ROADMAP_STATUS.md), and it is written so that a reader can
disagree with a conclusion by re-running the command next to it.

It was audited on 2026-10-28 at commit `999f3f80cf033649e92fc738f7273b7202fd695a`,
which declares version `18.8.2` in `backend/pyproject.toml`. That file is the authority
on the version. Every count below was produced by a command run against that commit and
is repeatable; where a count is a proxy for something softer, the page says so.

**What this page is not.** It is not an exhaustive security assessment, and it does not
claim to be. The runtime was never started for this audit: no database was booted, no
request was served, no model was called, and the backend test suite was not executed
locally (the reason is recorded in
[Evidence and method](#evidence-and-method)). Statements about behaviour are therefore
read from code, from tests, and from the recorded results of the continuous-integration
runs at the audited commit. Where that reading is not enough to settle a question, the
question is left open and named.

**Corrections made during Phase 02.** Three statements on this page were wrong, and all
three were wrong in the direction that matters: they understated controls that already
existed in the tree at the audited commit. Each is corrected inline and marked
*[corrected in Phase 02]* — the row-level-security provisioning path does install
`FORCE ROW LEVEL SECURITY` and does verify the runtime role at boot; the platform does
ship an RLS readiness report and a PostgreSQL-backed test suite for it; and `Role` has
seven members, not eight. Everything else was re-measured against the current tree during
Phase 02 and stands. What Phase 02 then *changed* is recorded in
[ROADMAP_STATUS.md](./ROADMAP_STATUS.md#phase-02-delivered), not here: this page stays a
record of the platform as audited, with its errors marked rather than quietly removed.

## A. Repository and governance

### Identity and licence

The project is licensed **AGPL-3.0** (`LICENSE`, 661 lines, "GNU AFFERO GENERAL PUBLIC
LICENSE Version 3, 19 November 2007"), published by DataDrivenConstruction with a
commercial option for enterprises. `backend/pyproject.toml` declares
`license-files = ["LICENSE", "NOTICE"]`, and the tree carries the rest of the governance
set: `COPYRIGHT`, `NOTICE`, `AUTHORS.md`, `CONTRIBUTORS.md`, `CITATION.cff`,
`CODE_OF_CONDUCT.md`, `CONTRIBUTING.md` (191 lines), `SECURITY.md` (180 lines),
`DEVELOPING.md`, `MODULES.md`, `SUPPORT.md`, and a 9,873-line `CHANGELOG.md`.
`.github/` adds `CODEOWNERS`, `dependabot.yml`, a `codeql/` configuration directory,
three issue templates, and a pull-request template.

`CODEOWNERS` scopes required review to legal, security, compliance and CI release paths,
naming the upstream account as formal owner.

Copyleft obligations are enforced twice, in both directions:

- `scripts/check_dependency_licences.py` fails when an installed dependency carries
  copyleft that has not been accepted, and treats AGPL as never acceptable
  (`_NEVER_ACCEPTED`), so an AGPL package cannot be added even by explicit approval.
- `.github/workflows/dependency-review.yml` denies `AGPL-1.0, AGPL-3.0, AGPL-3.0-only,
  AGPL-3.0-or-later, GPL-2.0, GPL-3.0, LGPL-2.1, LGPL-3.0, SSPL-1.0` with
  `fail-on-severity: high` and `warn-only: false`.

This matters beyond compliance: any proprietary layer built on top of an AGPL-3.0
platform has to reach it through a boundary the licence permits. That constraint shapes
the separation rule in [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md).

A contributor licence agreement is tracked at `signatures/cla.json`.

### Shape of the tree

13,678 tracked files (`git ls-files | wc -l`), distributed as:

| Path | Tracked files | What it is |
|------|---------------|------------|
| `backend/` | 7,362 | FastAPI application, 428 Alembic revisions, the test estate |
| `frontend/` | 5,060 | React single-page application |
| `packs/` | 545 | 50 regional and partner data packs, each with its own `pyproject.toml` |
| `docs/` | 235 | 118 Markdown pages, 87 SVG, 9 Mermaid sources |
| `scripts/` | 165 | Maintenance helpers, including 69 `check_*` guards |
| `desktop/` | 83 | Tauri desktop shell |
| `data/` | 68 | Seed catalogues, BIM rules, match data, templates (78 MB) |
| `tools/` | 38 | Build and data tooling |
| `deploy/` | 33 | Docker, Railway, Render, Terraform |
| `.github/` | 33 | Workflows, templates, CodeQL config |
| `packages/` | 14 | `oe-sdk`, the Python SDK for out-of-tree modules |
| `modules/` | 12 | `oe-module-template`, the module skeleton |

`packages/` holds exactly one package (`oe-sdk`) and `modules/` exactly one template. The
root `llms.txt` describes `packages/` as "shared Pydantic schemas, SDK, UI kit" and
`services/` as "standalone microservices (CAD converter, CV pipeline, AI service)";
`git ls-files services` returns one file, `services/cv-pipeline/README.md`. Those
descriptions are aspirational rather than descriptive; see
[Documentation drift](#documentation-drift).

### Branches, tags and releases

The fork carries three remote refs (`main`, this audit branch, and `HEAD`), **zero tags**
and **zero releases** (`git tag | wc -l`, and the releases API returns an empty list).
Release machinery nonetheless exists and is exercised on the canonical repository:
`release.yml`, `release-gate.yml`, `release-signing.yml` (Sigstore / cosign),
`pypi-publish.yml`, `desktop-release.yml`. A fork without tags cannot reproduce a
signed release, which is worth knowing before treating this checkout as a deployment
source.

### Continuous integration

24 workflow definitions are tracked in `.github/workflows/`, and the repository reports
25 workflow records (the extra one is the dynamic Dependabot graph updater). At the last
census of the fork, **19 are active and 5 are reported `disabled_fork`**:
`ci-full.yml`, `codeql.yml`, `e2e-cross-os.yml`, `eval-match.yml`, `scorecard.yml`.

Two of those five are security analyses. CodeQL has a committed configuration at
`.github/codeql/codeql-config.yml` and OpenSSF Scorecard has a committed workflow, so the
capability is present in the tree and switched off at the platform level on this fork.
This is a fork setting rather than a code defect, and it is not changeable from a pull
request: the actions-permissions endpoint refused this token. The consequence is precise
and should not be overstated: **on this fork, static analysis and scorecard results are
absent, while dependency review, secret scanning, repo hygiene, authors guard, CI and the
PostgreSQL CI matrix do run.**

The CI workflow itself is broad. At the audited commit, run `40512044365` completed
`success` with 20 of 20 jobs, and `40512044371` (CI PostgreSQL) with 16 of 16: six Linux
shards, three Windows and three macOS shards running against embedded PostgreSQL, three
frontend jobs across three operating systems, a dedicated **Tenant Isolation / IDOR**
job, brand-token, version-sync and zero-width guards, and an aggregate job. All 46 check
runs at that commit recorded `success`.

### Dependencies and their locks

| Manifest | Lock |
|----------|------|
| `backend/pyproject.toml` (`requires-python >= 3.12`) | `backend/requirements.txt`, `backend/requirements-desktop.lock` |
| `frontend/package.json` | `frontend/package-lock.json` |
| root `package.json` | root `package-lock.json` |
| `desktop/src-tauri/Cargo.toml` | `desktop/src-tauri/Cargo.lock` |
| `packages/oe-sdk/pyproject.toml`, 50 pack `pyproject.toml` files | none per pack |

Optional-dependency extras separate the heavy and the provider-specific from the core:
`server` (which carries `celery[redis]>=5.4.0,<6`), `vector`, `semantic-clients` and
`semantic-encoder`. Celery and Redis are therefore **not** base dependencies; 26
application files reference Celery and 31 reference Redis, all behind those extras.

### Containers and deployment

Four compose files (`docker-compose.yml`, `docker-compose.prod.yml`,
`docker-compose.quickstart.yml`, `docker-compose.quickstart.image.yml`), two Dockerfiles
(`deploy/docker/Dockerfile.unified`, `deploy/docker/Dockerfile.frontend`), and deploy
targets for Railway, Render and Terraform (`digitalocean`, `stackit`). No Kubernetes
manifests are tracked: `grep -rl 'kind: Deployment' deploy` returns nothing, although
`llms.txt` advertises Kubernetes configs.

### Documentation drift

The documentation tree is extensive and mostly current, but the prose counts in the two
architecture pages have drifted, and the drift is measurable. This is recorded here
because an audit that repeats a stale number launders it.

| Claim and where it is made | Stated | Measured at this commit | Reproduce |
|---|---|---|---|
| Backend modules, `docs/architecture/README.md` | 189 | **213** manifests (215 directories) | `ls backend/app/modules/*/manifest.py \| wc -l` |
| Backend modules, `OVERVIEW.md` | 193 | **213** | as above |
| Modules with `category="core"`, `README.md` | 119 | **136** | `grep -rhoE 'category="[a-z_]+"' backend/app/modules/*/manifest.py \| sort \| uniq -c` |
| Frontend feature folders, `README.md` and `OVERVIEW.md` | 180 | **208** | `find frontend/src/features -mindepth 1 -maxdepth 1 -type d \| wc -l` |
| `<Route path=` entries in `App.tsx`, `README.md` | 280 | **318** | `grep -c '<Route path=' frontend/src/app/App.tsx` |
| Sidebar rows in `navCatalog.ts`, `README.md` | 142 | **154** | `grep -cE "^ +\{ .*to: '" frontend/src/app/layout/navCatalog.ts` |
| Frontend plugin modules, `README.md` | 17 (15 with empty `navItems`) | **16** manifests (14 empty) | `ls frontend/src/modules/*/manifest.ts \| wc -l` |
| Platform version, `README.md` | 16.2.0 | **18.8.2** | `grep -nE '^version' backend/pyproject.toml` |
| Modules, root `llms.txt` | 193 | **213** | as above |

Two structural claims are also inaccurate. `docs/architecture/README.md` says `shared/`
holds "the generated API client"; `frontend/src/shared/api/` holds one file, and the
generated artifact is `frontend/src/shared/lib/api-types.ts`, produced by
`npm run api:generate` against a **running** backend and **not tracked in git**
(`git ls-files frontend/src/shared/lib/api-types.ts` is empty). And
`docs/rfc/25-project-intelligence-audit.md` cites `ROADMAP_v1.9.md`, which does not exist
anywhere in the tree.

The instructive part is the contrast. The one architecture artifact that is
machine-generated and guard-checked is exactly right:
`frontend/src/features/architecture/architecture_manifest.json` reports 215 backend
modules, 213 with manifests, and 208 frontend features, matching the tree, because
`scripts/check_architecture_manifest.py` fails the build when it does not. Its own
docstring records the earlier cost of not having that guard: a manifest describing 125
modules and 112 features while the tree held 195 and 188, with every gate green. Prose
counts have no such guard, and `check_public_language_counts.py` covers only `README.md`
and `DEVELOPING.md`.

## B. Application architecture

### Composition root

`backend/app/main.py` is **5,520 lines**. It is not only the app factory: it also owns
startup schema healing (`run_schema_heal`, `run_deferred_foreign_key_heal`), Alembic head
verification (`alembic_head_state`, `_expected_alembic_head`), OpenAPI document priming
off the event loop, boot-phase reporting (`_set_boot_phase`), a heartbeat thread that
keeps health checks alive through startup (`_heartbeat_through_startup`), data-repair
verdict publishing, self-upgrade, and the `/api/health` endpoint. The file documents a
real incident in its own comments: a 139.7-second startup during which a route-table
build ran on the event loop and starved `GET /api/health`.

`backend/app/dependencies.py` (1,231 lines) wires request-scoped concerns: session,
current user, `RequireRole`, and `rls_request_context`.

### Layering as built

| Layer | Where | Size |
|-------|-------|------|
| Composition root | `app/main.py`, `app/cli.py`, `app/config.py`, `app/database.py`, `app/dependencies.py` | 5,520 + 1,231 + config |
| Request pipeline | `app/middleware/` | 10 modules |
| Framework | `app/core/` | 175 entries |
| Business capability | `app/modules/` | 215 directories, 213 manifests |
| UI | `frontend/src/{app,features,modules,shared,stores}` | 208 features, 42 stores |

`docs/architecture/README.md` describes `core/` as "the reusable framework, the parts
that are not tied to any one feature". The directory partly matches that description and
partly does not. Alongside genuine framework code (`module_loader.py` 918 lines,
`events.py` 513, `hooks.py` 171, `validation/` 16 files and 18,767 lines,
`permissions.py` 374, `storage.py` 1,830, `job_runner.py` 487, `rate_limiter.py` 191)
sit construction-domain modules: `cpm.py` (critical-path method), `boq_target.py`,
`cde_states.py`, `cde_naming.py`, `classification_registry.py`, `country_coverage.py`,
`calendar.py` and `calendar_day.py`, `cost_scope.py`. Domain logic and framework logic
share one namespace, which is the main structural obstacle to keeping a separate
intelligence layer from ERP internals.

### The module contract

A module is a directory under `backend/app/modules/<name>/` carrying a `manifest.py` that
builds a `ModuleManifest` (`core/module_loader.py`): `name`, `version`, `display_name`,
`description`, `author`, `category`, `depends`, `optional_depends`, `auto_install`,
`enabled`, and an `inference` declaration. The loader scans, sorts by declared
dependencies, imports, mounts `router.py` at `/api/v1/<name>/`, registers `models.py`,
loads `hooks.py` / `events.py` / `validators.py` / `pipeline_nodes.py`, and calls
`on_startup`. Manifests whose `category` is `core` cannot be disabled at runtime.

Categories at this commit:

| Category | Modules |
|---|---|
| `core` | 136 |
| `business` | 38 |
| `regional` | 14 |
| `extension` | 7 |
| `controls` | 6 |
| `integration` | 4 |
| `enterprise` | 3 |
| `project_controls`, `infra`, `estimation`, `developer_tools`, `compliance` | 1 each |

The `inference` declaration is the most interesting part of the contract for an
intelligence layer. `InferenceDeclaration` carries `role` (an `InferenceRole` such as
`calls_model` or `consumes_result`), `when`, `what` and `basis`, and the loader validates
it, for example refusing a `consumes_result` declaration that does not say whose
inference it consumes. `scripts/check_module_inference_declarations.py` guards it and
three test files cover it, including `test_module_inference_gate.py`. **Adoption is 9 of
213 manifests** (`grep -l "inference=" backend/app/modules/*/manifest.py | wc -l`). The
mechanism for declaring inference provenance exists and is tested; it is barely used.

### Data model and migrations

The generated architecture manifest reports **788 models, 9,172 columns, 240
relationships, 4,627 routes and 4,120 schemas** across 2,810 backend Python files, with
187 of 208 frontend features mapped to a backend counterpart.

428 Alembic revisions are tracked under `backend/alembic/versions/`. They are not the
whole story. `core/postgres_migrator.py` (1,345 lines) is a **startup auto-migrator**:
its docstring states that upgraded installs are "alembic-STAMPED, not alembic-run", so
indexes and columns added later are created at boot with `CREATE INDEX IF NOT EXISTS`
inside a transaction-scoped advisory lock, best-effort per statement, with skipped
statements collected and logged (`SkippedStatement`, `heal_is_incomplete`,
`_log_skipped`). `main.py` adds deferred foreign-key healing and a set of unvalidated
constraint statements (`UNVALIDATED_CONSTRAINTS_SQL`).

The design is deliberate and well documented, and it buys a genuinely easy upgrade path
for a self-hosted single-node install. The cost is that the live schema is not
reproducible from migration history alone, and constraints may exist in an unvalidated
state. Both facts complicate audit and disaster recovery.

### Authentication, authorization and tenancy

Authentication is JWT over `HTTPBearer`, decoded through `core/jwt_keys.decode_jwt` with
`python-jose`. Role authorization is a real engine: `core/permissions.py` defines
`Role` as a `StrEnum` with **seven** members — `admin`, `manager`, `editor`, `viewer` and
the three design-stage field roles `site_inspector`, `site_foreman`, `field_worker` —
together with a `ROLE_HIERARCHY` that ranks the field roles *below* `viewer` (negative
ranks, so the legacy `-1` floor cannot promote a field worker above a viewer) and a
`ROLE_ALIASES` map of eight industry titles (`estimator`, `qs`, `quantity_surveyor`,
`owner`, `superuser`, `guest`, `readonly`, `user`) resolved onto those members. It also
defines a `PermissionRegistry` whose `register(permission, min_role)` defaults to editor.

> *[Corrected in Phase 02: this page first reported eight roles and listed `owner`,
> `estimator` and `guest` as members while omitting `editor` and `field_worker`. Those
> three are aliases, and the difference is not cosmetic — `role_has_permission` denies an
> unknown permission outright, and an alias is resolved to a member before the hierarchy
> is read, so reasoning from the wrong list predicts the wrong answer for every field
> role.]*
171 module directories carry their own `permissions.py`, declaring 215 distinct dotted
action keys (`<module>.<view|edit|manage|approve|delete|create|export>`), and
`scripts/check_permission_registration.py` guards registration. Routers depend on
`RequirePermission`, for example `dependencies=[Depends(RequirePermission("ai_agents.run"))]`.

Tenancy has two overlapping models, and the difference between them is the single most
consequential finding in this audit.

1. **Project-scoped visibility, applied in the application.** `project_id` appears in 416
   column declarations across 147 of 169 `models.py` files, making the project the real
   isolation unit. `core/cost_scope.py` (308 lines) binds the caller with a context
   variable (`bind_caller`, `caller_scope`, `shared_scope`, `system_scope`), installs a
   SQLAlchemy session hook, and exposes `visible_cost_items(user_id)` and `visible_ids`.
   `core/tenant_scope.py` (203 lines) resolves a tenant from the token payload.
   `RequestScopeMiddleware` binds scope per request.
2. **Row-level security, present but off by default.** `core/rls.py` (142 lines) stamps a
   transaction-local `app.current_tenant` GUC through an `after_begin` listener and, in
   the same hook, moves the transaction into the policied role with
   `SET LOCAL ROLE "oe_app"`. `core/rls_setup.py` creates that role
   `NOSUPERUSER NOINHERIT NOLOGIN`, creates `oe_system` as `NOSUPERUSER NOINHERIT NOLOGIN
   BYPASSRLS` for the system work that must see every row, grants `oe_app` exactly
   `USAGE` on the public schema, `SELECT`/`INSERT`/`UPDATE`/`DELETE` on all tables and
   `USAGE`/`SELECT` on sequences plus two `ALTER DEFAULT PRIVILEGES` statements, and on
   every table carrying a `tenant_id` column issues `ENABLE` **and `FORCE`** `ROW LEVEL
   SECURITY` followed by a guarded `CREATE POLICY "oe_tenant_isolation"`. Provisioning
   runs once per boot under a named advisory lock with `SET LOCAL lock_timeout = '3s'`, so
   a busy table defers to the next boot instead of stalling traffic, and
   `verify_rls_role(engine)` raises `RLSConfigurationError` at startup when `oe_app` is
   missing, is a superuser, carries `BYPASSRLS` or cannot be assumed. That verification
   sits *outside* the best-effort handler in `main.py`, so a mis-provisioned role refuses
   to boot rather than merely logging.

   > *[Corrected in Phase 02: this page first reported that no `FORCE ROW LEVEL SECURITY`
   > is installed and that the subsystem is inert but for the superuser bypass. Both were
   > wrong, and the reading also missed `verify_rls_role`, the `rls_readiness_report` /
   > `readiness_blockers` census, and the PostgreSQL-backed suites under
   > `backend/tests/pg/` and `backend/tests/modules/`. `FORCE` matters because it binds
   > the table owner as well as other roles; without it, owning a table would be enough to
   > read every row in it.]*

   Three qualifications remain, and all three are documented in the modules themselves:
   `settings.rls_enforce` defaults to **False** (`OE_RLS_ENFORCE`), so none of the above
   runs on a default install; when it is off the app connects as the cluster superuser,
   and a superuser bypasses every policy; and the predicate the rollout installs is
   deliberately permissive,
   `tenant_id IS NULL OR tenant_id::text = current_setting('app.current_tenant', true)`,
   because `tenant_id` is "only partly rolled out". Only **29 `tenant_id` column
   declarations across 15 of 169 `models.py` files** exist, and no Alembic revision
   mentions row-level security, because the policies are created by `rls_setup.py` at
   startup rather than by a migration. Six global reference tables are deliberately
   excluded from any policy — `oe_costs_item`, `oe_costs_catalog`, `oe_catalog_resource`,
   `oe_regional_indices` and the two join tables `oe_cost_item_usage` and
   `oe_cost_item_resource` — which is the platform's explicit definition of a record that
   is not tenant-owned.

   Phase 02 added a **strict** mode beside the permissive one, and a readiness gate in
   front of it; see [ROADMAP_STATUS.md](./ROADMAP_STATUS.md#phase-02-delivered). It does
   not change any measurement above, and it does not close the fourth qualification that
   the audit's reading missed: a session whose tenant was never resolved keeps the
   connecting role, so background work — agent runs, scheduled jobs — is not policied
   unless the caller bound a tenant first.

So tenant isolation is enforced where code remembers to enforce it, and the database
backstop that would catch a forgotten filter covers a small minority of tables and is
switched off unless an operator opts in. Against that, the authorization test estate is
strong: **45 integration test files** target cross-tenant and IDOR behaviour
(`find backend/tests -iname "*idor*" -o -iname "*cross_tenant*"`), and CI runs them in a
dedicated job whose selection chain is spelled out in `.github/workflows/ci.yml`,
including a comment explaining why the bare token `idor` also matches `corridor`.

### Asynchronous work and events

`core/job_runner.py` (487 lines) and `core/jobs_tasks.py` (204) provide in-process
background execution; `core/events.py` (513) is a publish-subscribe bus with dot-notation
names and a detached-publish variant that lets a request commit before subscribers run;
`core/hooks.py` (171) provides priority-ordered filters, which propagate errors, and
fire-and-forget actions, which log and swallow them. Celery with a Redis broker is
available through the `server` extra for a scaled deployment.

### Validation engine

`core/validation/` is 16 files and **18,767 lines**: `engine.py`, a `rules/` tree, a
`dsl/` tree, `messages/`, `pack_coverage.py`, `project_context.py`, `identifiers.py`,
`address.py`, `phone.py`, `poz_catalogue.py`. Rules declare an id, a standard, a severity
and a category, group into named sets (`din276`, `gaeb`, `nrm`, `masterformat`,
`boq_quality`, `bim_compliance`, plus regional sets), and produce a `ValidationReport`
with a severity-weighted quality score in which a blocking error caps the result. This is
the largest single concentration of encoded construction knowledge in the repository.

### Storage, uploads and retrieval

`core/storage.py` (1,830 lines) abstracts `LocalStorageBackend` and `S3StorageBackend`
behind a `StorageBackend` ABC, with presigned URLs, multipart sessions, and HMAC-signed
local upload tokens (`_sign_local_upload_token`, `_verify_local_upload_token`). Upload
safety lives in `core/upload_guards.py`, `core/upload_streaming.py` and
`core/pdf_paint_scan.py`.

Semantic matching is a substantial subsystem: `core/match_service/` is 24 files and 7,803
lines with format-specific extractors and an AI reranker, supported by `core/vector.py`,
`core/vector_index.py` and `core/vector_routes.py`; 52 application files reference
Qdrant. [ADR-003](../adr/003-vector-match-service.md) records the design. Vector search
is optional: the platform boots without a reachable vector backend and disables only the
semantic features.

Note that `modules/retrieval` is **not** vector retrieval. Its own docstring describes a
facet query over documents, correspondence and change orders, project-scoped, with
bounded reads per source, plus saved searches.

### Frontend

React 18 with TypeScript under `frontend/src/`: `app/` (shell, routing, layout, i18n),
208 `features/` directories, `modules/` (16 plugin manifests plus a registry),
`shared/{api,auth,hooks,lib,types,ui}`, 42 `stores/`, and a `pwa/` surface. 45 locale
files are tracked. 4,295 TypeScript files, of which the architecture manifest maps 3,579
to features.

Two registration systems coexist and differ in what they give you for free, which
`docs/architecture/README.md` explains well: a feature folder is wired by hand in
`App.tsx` (318 route entries) and again in `navCatalog.ts` (154 rows), while a plugin
module gets its routes automatically from `MODULE_REGISTRY` after two hand-written lines
and still needs a manual sidebar row.

### Observability

Logging is structured (`structlog` is a base dependency) with a correlation id
(`middleware/request_id.py`), an actor context (`middleware/actor_context.py`), a slow
request logger, request fingerprinting, body-size limiting, and secret redaction
(`core/log_redaction.py`). `core/crash_diagnostics.py` exists, and `/api/health` is
served from `main.py`. No metrics endpoint was found: a search for `metrics`, `ready`,
`live` and `healthz` route decorators in `main.py` returns only `/api/health`. There is no
distributed tracing.

## C. Construction business capabilities

Classification uses five values. **Implemented** means code plus tests exist and CI
exercises them. **Partial** means the capability works for part of its domain, or works
without the intelligence its name implies. **Placeholder** means structure without
substance. **Documentation-only** means described in prose with no corresponding code.
**Unverified** means the audit could not settle it from static evidence.

Test counts below are name-matched file counts
(`find backend/tests -name "test_*<area>*.py" | wc -l`) and are a **proxy** for coverage
depth, not a measurement of it: a file name says what a test is about, not what it
asserts. They are reported because they are reproducible and because the alternative,
claiming coverage without a number, is worse.

| Capability | Modules (evidence) | Test files | Class |
|---|---|---|---|
| Projects, companies, administration | `projects`, `project_route`, `admin`, `users`, `teams`, `contacts`, `stakeholders`, `legal_entities`, `global_presence`, `onboarding`, `portal`, `dashboard`, `dashboards`, `bi_dashboards` | broad | **Implemented** |
| BOQ and cost planning | `boq`, `costs`, `cost_plan`, `cost_match`, `cost_explorer`, `cost_recovery`, `costmodel`, `catalog`, `assemblies`, `allowances`, `markups`, `waste_factors`, `price_breakdown`, `price_index`, `estimate_basis`, `estimate_rollup`, `rom_estimate`, `postcalc`, `labor_rates`, `measurement`, `norm_expansion` | 100 (boq) + 103 (cost) | **Implemented** — the deepest area in the platform |
| Quantities and takeoff | `takeoff`, `dwg_takeoff`, `rebar_schedule`, `formwork`, `temporary_works`, `preliminaries`, `pointcloud` | 39 (takeoff) | **Implemented** |
| Scheduling and progress | `schedule`, `schedule_advanced`, `timeline`, `deadlines`, `tasks`, `progress`, `plan_room`, `project_controls`, plus `core/cpm.py` | 74 (schedule) + 11 (cpm) | **Implemented**; milestone modelling is spread across `timeline` and `deadlines` rather than a dedicated module — **Partial** for milestones as a first-class concept |
| Contracts and commercial | `contracts`, `contract`-adjacent `commitment_release`, `payment_release`, `payment_clock`, `certified_payroll`, `payroll`, `tax_withholding`, `finance`, `economics`, `eac`, `full_evm`, `cvr`, `reconciliation` | 86 (contract) | **Implemented** |
| Procurement and supply | `procurement`, `procurement_concept`, `supplier_catalogs`, `subcontractors`, `rfq_bidding`, `tendering`, `bid_management`, `moc`, `equipment`, `assets`, `plant_register`, `site_inventory`, `resources`, `staffing` | 36 (procurement), 19 (tender), 10 (subcontractor), 7 (supplier), 5 (rfq) | **Implemented** for procurement and tendering; **Partial** for supplier management and RFQ, where test depth is thin relative to module count |
| Site operations | `site_supervision`, `site_logistics`, `site_prep`, `field_diary`, `field_time`, `fieldreports`, `daily_diary`, `inspections`, `punchlist`, `phonelog`, `accommodation`, `waste` | 9 (daily diary), 6 (inspection), 8 (punchlist) | **Partial** — modules exist and are wired, but inspection and diary test depth is low compared with commercial areas |
| Quality, HSE and compliance | `qms`, `quality_concept`, `ncr`, `defects_liability`, `hse_frame`, `hse_advanced`, `safety`, `compliance`, `compliance_docs`, `permits`, `authority_submission`, `esg`, `carbon` | 13 (quality), 10 (qms), 14 (safety), 11 (hse) | **Implemented** for QMS and HSE framing; **Partial** for carbon and ESG |
| Documents, drawings and evidence | `documents`, `cde`, `transmittals`, `submittals`, `correspondence`, `record_publishing`, `file_versions`, `file_approvals`, `file_comments`, `file_distribution`, `file_references`, `file_search`, `file_tags`, `file_transmittals`, `file_trash`, `file_favorites`, `file_saved_views`, `uploads`, `resumable_uploads`, `signing`, `retrieval`, `review_authority`, `opencde_api` | 40 (document), 16 (cde), 10 (submittal), 7 (transmittal) | **Implemented** — a full common-data-environment surface with versioning, approval and transmittal flows |
| Change, claims and risk | `changeorders`, `variations`, `change_intelligence` (24 files, 10,720 lines), `claims_evidence`, `risk` (14 files, 4,763 lines), `clash_cost_impact`, `interface_management` | 27 (variation), 36 (claim), 17 (risk), 12 (changeorder) | **Implemented** for change and claims handling; **Partial** for risk, which is a register rather than a quantitative engine |
| BIM and coordination | `bim_hub`, `bim_requirements`, `bimlv`, `bcf`, `clash`, `coordination_hub`, `architecture_map`, `design_management`, `design_options`, `engineering_brief`, `requirements` | 63 (bim), 26 (clash) | **Implemented** on the canonical-format rule: sources are converted, never parsed natively ([ADR-002](../adr/002-no-ifcopenshell-ddc-canonical-only.md)) |
| Invoicing and regional tax | `einvoice`, `einvoice_clearance`, `einvoice_eta`, `einvoice_ksef`, `einvoice_zatca`, `fx`, `currency_registry` (core) | 62 (invoice) | **Implemented** for the jurisdictions named; each is a separate integration |
| Regional and partner packs | 13 in-tree regional modules plus 50 directories under `packs/` | per pack | **Implemented**; [ADR 2026-05-28](../adr/2026-05-28-partner-pack-architecture.md) records the architecture |
| Artificial intelligence | `ai` (10 files, 5,988 lines), `ai_agents` (30 files, 8,903), `ai_estimator` (19 files, 11,016), `compliance_ai`, `clash_ai_triage`, `project_intelligence` (11 files, 4,899), `erp_chat`, `inbound_email`, `inbound_capture`, `cv`, `voice`, `core/ai/pricing.py`, `core/match_service/reranker_ai.py`, `core/translation/llm_translator.py` | agent and estimator suites | **Partial** — see below |
| Platform and developer tooling | `module_builder`, `validation`, `methodology`, `connectors`, `integrations`, `webhook_leads`, `jobs`, `notifications`, `backup`, `client_errors`, `closeout`, `credentials`, `search`, `smart_views`, `saved_views`, `enterprise_workflows`, `approval_routes`, `project_governance`, `pipelines`, `cases`, `service`, `crm`, `teams` | various | **Implemented** |

### Why the AI row is Partial and not Implemented

This deserves precision, because the module sizes suggest more than the behaviour
delivers and the repository itself has already said so.

What genuinely exists:

- **A working agent runtime.** `modules/ai_agents/base.py` (652 lines) documents its loop
  as `user_input -> [LLM] -> tool_call -> [registry.run] -> observation`, with a `Tool`
  protocol, a `FunctionTool`, and a `ToolRegistry`. It bounds observations
  (`max_observation_chars`, `_truncate_observation`) so an oversized read cannot flood the
  context, applies a per-step timeout (`effective_step_timeout`), and emits structured
  fields (`agent`, `step_idx`, `role`, `token_count`, `tool`, `tokens_total`).
- **A provider-neutral bridge.** `ai_agents/llm.py` (175 lines) is "deliberately
  deterministic and lightweight - no provider-specific" logic: it formats tool schemas
  into the prompt for providers without native tool use, and parses a structured response
  with explicit JSON error handling. Provider clients live in the optional
  `semantic-clients` extra.
- **Eight domain agents** under `ai_agents/agents/`: `advisors`, `boq_drafter`,
  `cost_classifier`, `document_analyst`, `estimate_reviewer`, `progress_reporter`,
  `project_analyst`, `rate_benchmarker`, with `agents/_access.py` (79 lines) providing
  `assert_user_can_access_project` and `coerce_user_id`.
- **A propose-then-apply path.** `ai_agents/proposals.py` (339 lines) models
  `PositionProposal` and `ApplyOutcome`, extracts proposals from agent steps, and applies
  them to a BOQ while rejecting cross-currency applies. Nothing is auto-applied.
- **A trust envelope.** `ai_agents/trust.py` (458 lines) gives every agent one structured
  wrapper: a calibrated confidence in 0..1 that is "honest, never fabricated", a
  rationale, `sources` cited "by REAL id / path (never invented)", and
  `what_would_increase_confidence`. `parse_envelope_from_text` recovers an envelope from
  free text, `clamp_confidence` bounds it, and `envelope_instructions` tells the model how
  to emit one. It is pure standard library by design.
- **Accuracy accounting.** `accuracy.py`, `accuracy_service.py` (218 lines) and
  `feedback_summary.py` score recorded runs against an `actual_outcome` to build a
  calibration scoreboard. Runs persist in `oe_ai_agents_run` with a `trust` payload and a
  `trigger_source`.
- **Triggers and scheduling.** `triggers.py` (135 lines) defines `EventTrigger`,
  `list_event_triggers`, `normalise_triggers` and — importantly —
  `required_permission_for_tool(tool_name)`. `scheduler.py` (182 lines) runs agents on a
  schedule. `sandbox.py` (314 lines) seeds clearly labelled sample runs for an empty demo
  install, deterministically by `uuid5`, writing only rows a real run would write.

What is missing, in the repository's own words. `docs/rfc/25-project-intelligence-audit.md`
(status: draft) assesses `project_intelligence` as follows: "scoring is 13 hardcoded
if-then rules; an LLM narration sits on top, but no ML, no anomaly detection, no
forecast", and finds the module "misaligned with its users", a project-readiness checklist
rather than a cost-analysis dashboard, with 50% of the domain weight going to BOQ and
validation rendered as compliance metrics rather than financial ones. The RFC's widget
inventory cites `scorer.py` gap and achievement rule tables and an `AIAdvisorPanel.tsx`
with "optional LLM, fallback rule text".

So: the agent **plumbing** is implemented and well engineered; the **intelligence** above
it is largely rule-based, and the forecasting and anomaly-detection capabilities the
names suggest are not there. Classifying this row as Implemented would repeat exactly the
error the RFC was written to correct.

### Lifecycle workflow coverage

| Workflow | State |
|---|---|
| Company knowledge and historical projects | **Partial** — `cost_match`, `catalog`, `price_index`, `match_service` and 78 MB of seed catalogues under `data/` carry historical pricing; there is no cross-project learning layer |
| Documents and drawings | **Implemented** — CDE, versioning, transmittals, approval routes, signing |
| Schedules | **Implemented** — CPM in core, advanced schedule, timeline |
| Costs | **Implemented** — the strongest area in the platform |
| Procurement | **Implemented**, supplier side **Partial** |
| Site operations | **Partial** — modules wired, thinner test depth |
| Risk | **Partial** — register and scoring rules, not a quantitative engine |

## D. Security and enterprise readiness

This section is **not exhaustive**. It reports what static reading and CI evidence
support. No penetration testing, no dependency on runtime observation, and no review of
the 4,627 routes individually.

### Controls that are present and enforced

- **Weak JWT secrets are refused, not merely warned about.** `config.py` and `main.py`
  both raise outside development: a known-weak default is rejected because it is
  "admin-forgeable by anyone who reads the public OpenConstructionERP source", and a
  secret shorter than 32 bytes is rejected because HS256 forgery is tractable below that
  length. In development the bundled default is rotated to a strong random secret and
  persisted with mode 600 so a restart does not invalidate live sessions (the code cites
  the bug this fixed). The warning logs the secret's length, never its value.
- **CORS wildcards are refused in production.** `main.py` raises on `*` in
  `settings.cors_origins` when `is_production`, filters it out, and falls back to an
  explicit origin rather than allowing everything.
- **Security headers.** `middleware/security_headers.py` (177 lines) sets
  `X-Frame-Options: SAMEORIGIN`, HSTS in production, and a Content-Security-Policy with
  `frame-src 'self' blob: data:` so same-origin PDF preview keeps working.
- **Rate limiting.** `core/rate_limiter.py` (191 lines) provides per-user and per-IP
  limits configurable through `AI_RATE_LIMIT` (default 10 per minute), `API_RATE_LIMIT`
  (100) and `LOGIN_RATE_LIMIT` (10), with trusted-network parsing and a private-range
  check on the client IP. 26 files use it.
- **Request hardening.** Body-size limiting, request correlation ids, actor context,
  request fingerprinting, and log redaction are all in the middleware and core layers.
- **Upload safety.** Signed local upload tokens, streaming uploads, dedicated upload
  guards, and a PDF paint scan.
- **Audit trail.** `core/audit_log.py` (552 lines) with an `AuditContext` bound to a
  context variable and an `ActivityLog` model, plus `audit.py` (223), `audit_gates.py`
  (113), `audit_prune.py` (111) and `audit_router.py` (311). State payloads are bounded
  before storage and user agents truncated. Alembic revisions `v3033_audit_log`,
  `v3140_qms_audit_log`, `v3144_audit_log_extend`, `v3149_audit_indexes_and_fks` and
  `v191_cde_audit` show the trail being extended and indexed over time.
- **Authorization testing.** 45 IDOR and cross-tenant integration test files, run by a
  dedicated CI job.
- **Supply chain.** `dependency-review.yml` with a copyleft denylist and
  `fail-on-severity: high`; `sbom-and-licenses.yml`; `secret-scan.yml`;
  `check_dependency_licences.py`; `check_secret_denylist_single_source.py`; Dependabot
  configured.
- **Release integrity.** `release-gate.yml` and `release-signing.yml` (Sigstore / cosign).
- **Backup and restore.** `modules/backup` (7 files) builds a registry from every module's
  models, curates it, and redacts sensitive fields (`_is_sensitive_field`);
  `scripts/check_backup_freshness.py` guards staleness.

### Gaps, ranked by consequence

1. **The database-level tenant backstop is off by default and covers a minority of
   tables.** `rls_enforce` is `False`; the app connects as superuser, which bypasses all
   policies; the policy admits rows whose `tenant_id` is NULL; and only 29 `tenant_id`
   columns exist across 15 of 169 `models.py` files. Isolation therefore depends on each
   code path applying `cost_scope` or an explicit filter correctly. The 45 IDOR tests are
   the compensating control, and they are good, but tests cover the paths someone thought
   to write. Any new data path — a graph projection, an embedding index build, an agent
   tool — inherits no database-level guarantee. For a platform about to expose data to
   automated agents, this is the first thing to fix.

   **Phase 02 narrowed this and did not close it.** `rls_setup.py` can now install a
   strict predicate with no `IS NULL` arm, so an unowned row belongs to nobody and an
   anonymous caller matches nothing; it is refused unless
   `GET /api/v1/admin/rls/readiness` reports the database ready, it falls back to the
   permissive policy rather than leaving a table unpolicied, and mode changes are applied
   in both directions so switching it off again is a real rollback. What is still true:
   enforcement is off by default, the readiness report names `tenant_is_user` as a blocker
   on a stock install (the login issues no tenant claim, so `resolve_tenant_id` falls back
   to the user id and every user would become a tenant of one), coverage is still 29
   columns across 15 of 169 `models.py` files, and a session that never resolved a tenant
   keeps the connecting role. The gap is now measured and gated rather than assumed away.
2. **Tool-level authorization is a convention, not a gate.** *(Closed in Phase 02.)*
   `base.py` injects `__agent_context__` into every tool call "so tools that need the
   invoking user / project can re-verify permission". That is the right seam and it is
   documented, but it is opt-in per tool. `triggers.py` maps a tool name to a required
   permission, which shows the intent; nothing in the registry enforces that mapping at
   call time.

   Phase 02 put the check in the runner, immediately before the single
   `await tool.run(call_args)` statement every supported execution path reaches, so a
   protected tool call is now refused by the platform rather than by whichever tool
   remembered to check. All fifteen registered tools name their permission, pinned by a
   test, and all three production call sites pass an authorizer, also pinned. What is
   still a tool's own job is resolving a resource identifier to the project that owns it
   — a `boq_id` means nothing to the gate — so the per-tool discipline this finding
   describes is still required, it is just no longer the only control.
3. **The Content-Security-Policy permits `script-src 'self' 'unsafe-inline' 'unsafe-eval'
   blob:`.** The file describes the policy as "relaxed for SPA". With inline script and
   eval allowed, the policy will not stop an injected script from executing; it remains
   valuable for `frame-src`, `img-src` and exfiltration limits, but it is not an XSS
   control in the strong sense.
4. **The rate limiter is in-process.** Its own docstring says "For production, replace
   with Redis-based implementation". Behind more than one worker or replica, the effective
   limit is the configured limit multiplied by process count, and login throttling is
   weakest exactly where it matters most.
5. **Schema state is not reproducible from migration history.** Startup auto-migration
   stamps rather than runs Alembic, heals indexes and columns at boot, defers foreign-key
   creation, and applies constraints in an unvalidated state. Operationally convenient;
   hard to audit, and a hazard for point-in-time recovery, because the schema a backup
   restores into is produced by application code at boot rather than by a numbered
   revision chain.
6. **Observability is log-only.** `/api/health` exists and there is a documented incident
   about it being starved during a 139.7-second startup, but there is no metrics endpoint
   and no tracing. Capacity, latency-regression and saturation questions cannot be
   answered from the platform itself.
7. **The API contract is not reproducible offline.** `api-types.ts` is generated from a
   running backend and is not tracked, so a frontend build cannot type-check against the
   API without booting it. 21 of 208 frontend features have no backend mapping in the
   architecture manifest.
8. **Inference provenance is declared by 9 of 213 modules**, so the platform cannot yet
   answer "which surfaces produce machine-derived conclusions" from manifests alone.
9. **Documentation drift**, itemised in section A. Operator-facing prose states a version
   two majors behind the declared one, and two structural claims describe code that is not
   there. Stale architecture documentation is a security-adjacent problem: it is what a
   new maintainer reasons from.
10. **Fork-side analysis is disabled.** CodeQL and OpenSSF Scorecard are `disabled_fork`,
    so this checkout has no static-analysis signal of its own.

### Enterprise readiness

| Dimension | State | Evidence |
|---|---|---|
| Multi-tenancy | Partial | project-scoped visibility in code; optional RLS covering 29 columns |
| Authorization model | Strong | 8-role hierarchy, permission registry, 171 module permission files, registration guard |
| Auditability of user actions | Strong | audit log with context, gates, pruning, router, indexed migrations |
| Auditability of schema | Weak | stamped-not-run migrations, boot-time DDL, unvalidated constraints |
| Horizontal scale | Partial | Celery plus Redis available as an extra; in-process rate limiter; no metrics |
| Observability | Partial | structured logs, correlation id, redaction, health endpoint; no metrics or tracing |
| Backup and recovery | Partial | backup module with sensitive-field redaction and a freshness guard; recovery path not evidenced |
| Deployment | Strong | 4 compose files, 2 Dockerfiles, Railway, Render, 2 Terraform targets, desktop shell, signed releases |
| Supply-chain security | Strong | dependency review with copyleft denylist, SBOM, licence guard, secret scan, Dependabot |
| Static analysis on this fork | Absent | CodeQL and Scorecard `disabled_fork` |
| Localization | Strong | 45 locale files, 44 offered languages, RTL and Hijri dependencies (`arabic-reshaper`, `python-bidi`, `hijridate`), an LLM translation path, and 12 locale-specific guards |
| Test estate | Strong | 1,819 backend unit files, 334 integration files, 1,196 frontend test files, 69 guards |

## Evidence and method

Everything above was read from the tree at `999f3f80cf033649e92fc738f7273b7202fd695a` or
from the GitHub API for that commit. The commands are inline so each number can be
challenged.

What was **not** possible in this environment, recorded rather than worked around:

- **The backend test suite was not executed.** `backend/pyproject.toml` declares
  `requires-python >= 3.12` and the audit environment provides Python 3.11.2; 25 of 3,199
  files under `backend/app` do not parse on 3.11, and a 3.12 interpreter could not be
  obtained. Collection also fails without the dependency set installed, and
  `backend/tests/conftest.py` raises when it cannot boot embedded PostgreSQL. Results run
  with `--noconftest` are partial by construction and are not reported as passes here.
  **CI is the authority on test outcomes**, and its recorded results at this commit are
  46 of 46 check runs `success`, including 20 of 20 CI jobs and 16 of 16 PostgreSQL jobs.
- **No runtime observation.** No server was started, so no endpoint, header, policy or
  latency claim here comes from a live response.
- **Workflow job logs could not be downloaded** from this environment; job conclusions,
  step timings and check annotations were used instead.
- **No model was called.** Statements about AI behaviour are read from code and from
  RFC 25, not observed.
- **Fork platform settings could not be changed**: the actions-permissions, rerun and
  dispatch endpoints all refused this token, so the `disabled_fork` workflows are
  reported, not fixed.

## Where to go next

- [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md) — the intelligence layer this
  foundation has to carry, and the boundaries it must respect.
- [ROADMAP_STATUS.md](./ROADMAP_STATUS.md) — what to keep, adapt, refactor, replace and
  build, with the evidence for each decision.
- [Architecture overview](./README.md) — how the codebase fits together today.
- [ADR index](../adr/README.md) and [RFC index](../rfc/README.md) — the recorded
  decisions, starting with
  [ADR-002](../adr/002-no-ifcopenshell-ddc-canonical-only.md) on the canonical format and
  [RFC 25](../rfc/25-project-intelligence-audit.md) on project intelligence.
