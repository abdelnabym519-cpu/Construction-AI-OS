# Roadmap status

Phase status and the disposition of every significant capability: what to keep, adapt,
refactor, replace or build, with the evidence for each decision. The measurements behind
these decisions are in [FOUNDATION_AUDIT.md](./FOUNDATION_AUDIT.md); the architecture they
serve is in [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md).

Status is recorded at commit `999f3f80cf033649e92fc738f7273b7202fd695a`, audited
2026-10-28.

This file is the roadmap-status record for the intelligence programme. The repository has
no other: `docs/rfc/25-project-intelligence-audit.md` cites a `ROADMAP_v1.9.md` that does
not exist in the tree, and `git ls-files | grep -i roadmap` returns only permits-domain
code. That dangling reference is left alone here, because a path matching
`docs/ROADMAP_v*.md` is on the internal-artifact denylist in
`scripts/check_repo_hygiene.py` and creating it would fail the repo-hygiene gate.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| **01 — Foundation Audit** | Verify the live state, audit repository, architecture, capabilities and security, classify every capability, write these three documents | **Complete.** Documentation only. No code, dependency, schema, configuration or workflow change |
| **02 — ERP Hardening** | Close the control gaps that must be closed before automated agents touch data | **Not started.** Recommended next |
| **03 — Intelligence Core foundations** | Versioned domain contract, ontology, graph and evidence projections, governed tool platform | Not started |
| **04 — Reasoning** | Cross-domain dependency, impact and quantitative risk engines | Not started |
| **05 — Interaction** | Unified simulation, ranked decision support, proactive detection, multimodal ingestion, voice | Not started |

Phase numbering after 02 is a proposal. It should be re-derived from evidence when Phase 02
closes, not treated as committed.

## Decision summary

| Disposition | Count | Meaning |
|---|---|---|
| **KEEP** | 21 | Working and tested. Build on it; do not duplicate it |
| **ADAPT** | 14 | Right mechanism, insufficient reach or coverage. Extend in place |
| **REFACTOR** | 5 | Right capability, wrong structure. Change the shape, not the behaviour |
| **REPLACE** | 2 | A named implementation is wrong for the requirement, and the replacement is narrow |
| **BUILD** | 11 | Does not exist. No working code would be duplicated by building it |

The KEEP count is the important one. This is a mature platform with 213 modules, 4,627
routes, 788 models and a test estate in the thousands of files. Most of what an
intelligence layer needs is already here, and the cheapest available mistake is building a
second one of something that works.

## KEEP

Kept as-is, with the evidence that justifies keeping rather than rewriting.

| Capability | Evidence | Why it is kept |
|---|---|---|
| Module system and loader | `core/module_loader.py` (918 lines), 213 manifests, dependency-sorted loading, auto-mounted routers and models | The platform's best structural idea. Drop-in extension is real on the backend and is the seam the intelligence layer plugs into |
| RBAC permission engine | `core/permissions.py` (374 lines), 8-role hierarchy with aliases, 171 module `permissions.py` files, 215 dotted action keys, `scripts/check_permission_registration.py` | Complete, guarded, and already used at the router edge via `RequirePermission` |
| Validation engine | `core/validation/` — 16 files, 18,767 lines, rules plus a DSL, severity-weighted reporting | The largest concentration of encoded construction knowledge in the repository, and the base for the ontology |
| Canonical conversion approach | [ADR-002](../adr/002-no-ifcopenshell-ddc-canonical-only.md); DWG, DGN, RVT and IFC as inputs, one canonical JSON internally | A deliberate, recorded decision that removes a heavy native dependency. Reversing it would need an argument nobody has made |
| BOQ and cost domain | 21 cost-related modules, 100 BOQ and 103 cost test files | Deepest area in the platform. Nothing here should be reimplemented |
| Common data environment | `documents`, `cde`, 14 `file_*` modules, `transmittals`, `submittals`, `signing`, `opencde_api`, 40 document and 16 CDE test files | A full document-control surface with versioning and approval |
| Scheduling | `schedule`, `schedule_advanced`, `timeline`, `core/cpm.py`, 74 plus 11 test files | Real CPM logic in core, not a stub |
| Contracts and commercial control | `contracts`, `eac`, `full_evm`, `cvr`, `commitment_release`, `payment_release`, `reconciliation`, 86 contract test files | Earned-value and commitment machinery is expensive to rebuild and already tested |
| BIM and coordination | `bim_hub`, `bim_requirements`, `bcf`, `clash`, `coordination_hub`, `pointcloud`, 63 BIM and 26 clash test files | Substantial, on the canonical format |
| Storage abstraction | `core/storage.py` (1,830 lines), local and S3 backends, presigned URLs, multipart, signed upload tokens | One ABC over two backends, exactly the right shape for evidence storage |
| Audit trail | `core/audit_log.py` (552 lines) with `AuditContext` and `ActivityLog`, plus gates, pruning and a router; five Alembic revisions extending and indexing it | Already records actor, action and bounded state. Agent runs write into the same discipline |
| Trust envelope | `ai_agents/trust.py` (458 lines): calibrated confidence, rationale, sources by real id or path, `what_would_increase_confidence` | This is the evidence-traceability requirement, already implemented in pure standard library |
| Accuracy scoreboard | `accuracy.py`, `accuracy_service.py` (218 lines), `feedback_summary.py`, runs stored with a trust payload and `actual_outcome` | Stores the outcomes needed to calibrate later phases. Rare and valuable |
| Agent runtime | `ai_agents/base.py` (652 lines): tool loop, observation truncation, per-step timeouts, structured token accounting | A real runtime, not a wrapper. Extend it; do not replace it |
| Propose-then-apply | `ai_agents/proposals.py` (339 lines) with cross-currency rejection | The human-approval pattern, already working for BOQ |
| Event bus and hooks | `core/events.py` (513 lines) with detached publish, `core/hooks.py` (171 lines) separating error-propagating filters from swallowed actions | The decoupling mechanism that lets an intelligence layer subscribe without importing modules |
| Authorization test estate | 45 IDOR and cross-tenant integration files plus a dedicated CI job with a documented selection chain | The compensating control for weak database-level isolation, and the regression net for Phase 02 |
| Guard battery | 69 `check_*` scripts, including the architecture-manifest guard that keeps a generated artifact honest | Encodes hard-won lessons. Every phase adds guards rather than removing them |
| Regional pack architecture | 50 packs under `packs/`, 13 regional modules, [ADR 2026-05-28](../adr/2026-05-28-partner-pack-architecture.md) | Extension point for jurisdiction-specific intelligence |
| Localization | 45 locale files, 44 offered languages, RTL and Hijri dependencies, an LLM translation path, 12 locale guards | Arabic and English are already first-class, which the long-term vision requires |
| Supply-chain and release gates | `dependency-review.yml` with a copyleft denylist and `fail-on-severity: high`, `sbom-and-licenses.yml`, `secret-scan.yml`, `check_dependency_licences.py`, `release-signing.yml` | Working security policy. Phase 02 adds analysis, never relaxes these |
| Secret and CORS enforcement | refusal to boot on a weak or short `JWT_SECRET` outside development, with dev-mode rotation and persistence; production CORS wildcard refusal | Correctly fail-closed already |

## ADAPT

The mechanism exists and is sound; its reach, coverage or enforcement is what falls short.

| # | Capability | Evidence | What changes | Dependencies | Risks | Phase |
|---|---|---|---|---|---|---|
| A1 | Row-level security | `rls.py` (142 lines), `rls_setup.py` (443 lines); `rls_enforce` defaults to False; predicate admits NULL `tenant_id`; only 29 `tenant_id` columns across 15 of 169 `models.py` files | Enable on a documented path: connect as the non-superuser `oe_app` role, backfill `tenant_id`, then tighten to a strict predicate. The code already describes this as the intended sequence | A database role change, a backfill per table, and the 45 IDOR tests as the acceptance net | Breaking existing installs if the strict predicate lands before the backfill; mitigated by keeping the permissive policy until coverage is measured per table | **02** |
| A2 | Tool platform governance | `ToolRegistry`, `FunctionTool`, `__agent_context__` injection in `base.py`; `required_permission_for_tool` in `triggers.py`; `agents/_access.py::assert_user_can_access_project` | Move the check from convention into the registry: resolve the required permission, verify caller authorization and project access, then invoke. No tool signature changes | A1 for scoped reads; the existing permission registry | Over-blocking legitimate tools; mitigated by failing closed only for tools that declare a permission, and logging every denial | **02** |
| A3 | Inference declarations | `InferenceDeclaration` validated by the loader, guarded by `check_module_inference_declarations.py`, tested by 3 files, adopted by **9 of 213** manifests | Extend adoption to every surface that produces a machine-derived conclusion, then make the guard ratchet so the count cannot fall | None | Large but mechanical; the risk is declaring inaccurately, which is worse than not declaring | **02** |
| A4 | Project intelligence | `project_intelligence` (11 files, 4,899 lines); [RFC 25](../rfc/25-project-intelligence-audit.md) records "13 hardcoded if-then rules", "no ML, no anomaly detection, no forecast", status draft | Implement RFC 25's chosen Option A: keep the shell, retarget the widgets to financial metrics, tighten the cache, reposition the advisor. The RFC already specifies endpoints, frontend shape and a testing plan | The domain contract (B1) for financial metrics; A2 for any agent involvement | Duplicating RFC 25's work by designing a parallel module. The RFC's Option B was explicitly deferred | **03** |
| A5 | Risk engine | `risk` (14 files, 4,763 lines); `accuracy_service.py` stores recorded outcomes | Grow from register to quantitative exposure, calibrated against recorded outcomes that already exist | B1, B4 for evidence links | Producing numbers with no basis; mitigated by the trust envelope's rule that confidence is never fabricated | **04** |
| A6 | Impact analysis | `change_intelligence` (24 files, 10,720 lines), `clash_cost_impact`, `variations`, `eac`, `estimate_rollup` | Connect per-domain impact into cross-domain propagation from one originating event | B3 dependency graph | Double-counting impact across domains; needs one propagation owner | **04** |
| A7 | Semantic match service | `core/match_service/` (24 files, 7,803 lines), `core/vector*.py`, [ADR-003](../adr/003-vector-match-service.md), optional `vector` extra | Reuse as the derived evidence index rather than building a second vector path; add provenance per embedding | B1 contract versioning | Treating a projection as authority, which ground rule 1 forbids | **03** |
| A8 | Proactive triggers | `triggers.py` (135 lines) with `EventTrigger`, `scheduler.py` (182 lines), `core/events.py` | Add the detection layer that decides what is worth raising; the trigger and scheduling machinery stays | A2 governance, since proactive runs act without a person asking | Unsolicited agent runs are the highest-risk surface; they need the strictest permission defaults | **05** |
| A9 | Retrieval | `modules/retrieval` (10 files, 1,826 lines), facet query over documents, correspondence and change orders, project-scoped with bounded reads | Extend to evidence-linked search returning citations, not only records | B4 | Unbounded reads; the module already bounds per source and that discipline must survive the extension | **03** |
| A10 | Rate limiting | `core/rate_limiter.py` (191 lines), in-process, its own docstring says "For production, replace with Redis-based implementation"; 26 usage points | Same interface, Redis-backed implementation when more than one process serves traffic. `celery[redis]` is already available via the `server` extra | A Redis deployment | Changing the interface would touch 26 files; the adaptation is deliberately behind the existing one | **02** |
| A11 | Caller scoping | `core/cost_scope.py` (308 lines) with `bind_caller`, `caller_scope`, `system_scope`, `visible_ids`; `RequestScopeMiddleware` | Reuse as the scope every agent read passes through, so tool reads inherit the same visibility rules as HTTP reads | A2 | Background sessions deliberately bypass scope; that distinction must stay explicit for agent work | **02** |
| A12 | Backup and recovery | `modules/backup` (7 files) with a derived model registry and sensitive-field redaction; `check_backup_freshness.py` | Extend to cover derived stores and to document a restore path, including the boot-time schema heal it must survive | A1, R3 | A restore that lands on a schema produced by boot-time DDL is not reproducible today | **02** |
| A13 | Architecture documentation | prose counts stale across `README.md`, `OVERVIEW.md` and `llms.txt` while the generated manifest is exact | Apply the remedy the repository already proved: generate the numbers, or guard them, as `check_architecture_manifest.py` and `check_public_language_counts.py` do | None | Low. The risk of not doing it is that the next reader trusts 16.2.0 | **02** |
| A14 | Multimodal ingestion | `match_service` extractors including DWG, `cv`, `pdf_paint_scan.py`, `inbound_capture`, `ai_estimator` (19 files, 11,016 lines) | Unify into one ingestion path that emits evidence-linked observations with citations | B4, A7 | Duplicating four working extractors; the unification is a façade over them, not a rewrite | **05** |

## REFACTOR

Behaviour is correct and tested; structure is what obstructs the next phase.

| # | Target | Evidence | Reason | Dependencies | Risks | Phase |
|---|---|---|---|---|---|---|
| R1 | Composition root | `backend/app/main.py` is **5,520 lines** and owns app construction plus schema healing, Alembic head checks, OpenAPI priming, boot-phase reporting, a heartbeat thread, data-repair publishing, self-upgrade and `/api/health`. Its comments record a 139.7-second startup that starved the health check | A file that constructs the application should not also perform operational surgery on the database. Splitting it makes boot stages individually testable and makes startup latency measurable | R3 shares the boot path, so sequence them | Touching startup is high-risk; mitigated by extracting pure functions first, one commit per stage, with the existing startup tests as the net | **02** |
| R2 | Framework versus domain in `core/` | `core/` holds 175 entries mixing framework code (`module_loader.py`, `events.py`, `storage.py`, `validation/`) with construction-domain code (`cpm.py`, `boq_target.py`, `cde_states.py`, `cde_naming.py`, `classification_registry.py`, `country_coverage.py`, `calendar.py`, `cost_scope.py`), while `docs/architecture/README.md` describes `core/` as "not tied to any one feature" | Ground rule 6 requires the layers to be distinguishable by location. A separate intelligence package cannot depend on a domain contract that lives inside the framework namespace | B1, which is the contract the extracted domain publishes | Very high blast radius: `core/` is imported everywhere. Must be incremental, module by module, with import guards added as each move lands | **03** |
| R3 | Migration reproducibility | `core/postgres_migrator.py` (1,345 lines) stamps rather than runs Alembic, heals indexes and columns at boot under an advisory lock, best-effort per statement with skipped statements logged; `main.py` adds deferred foreign-key healing and `UNVALIDATED_CONSTRAINTS_SQL`; 428 revisions are tracked | The live schema is not reproducible from migration history, and constraints can exist unvalidated. That obstructs audit, point-in-time recovery and any claim about data integrity | R1, A12 | The boot-time heal is what makes self-hosted upgrades easy, so removing it outright would regress a real user benefit. The refactor keeps the convenience and adds a reproducible path alongside it, rather than replacing it silently | **02** |
| R4 | Frontend registration | 318 `<Route path=` entries hand-written in `App.tsx`, 154 sidebar rows hand-maintained in `navCatalog.ts`, 208 feature folders that nothing discovers, 16 plugin manifests that do get their routes automatically | Two registration systems with different guarantees, both hand-maintained, is where screens go missing. The plugin registry already proves the automatic path works | None | Regressions in navigation are user-visible immediately; the 1,196 frontend test files and the route-death checks are the net | **03** |
| R5 | API contract artifact | `api-types.ts` is generated from a running backend by `npm run api:generate` and is **not tracked** (`git ls-files` returns nothing); `docs/architecture/README.md` nonetheless describes a "generated API client" in `shared/`, which holds one file | A frontend cannot type-check against the API without booting the backend, and 21 of 208 features have no backend mapping in the manifest. A published contract is also the natural source for typed agent tools | B12 | Committing a generated file invites drift; the alternative is generating it in CI, which needs the backend bootable there — already true for the PostgreSQL matrix | **02** |

## REPLACE

Only two implementations are wrong for the requirement, and both replacements are narrow.
Nothing here is a wholesale rewrite, and no working functionality is duplicated.

| # | Replaced | Evidence | Reason | Replacement | Dependencies | Risks | Phase |
|---|---|---|---|---|---|---|---|
| P1 | The hardcoded rule scorer inside `project_intelligence` | RFC 25: "scoring is 13 hardcoded if-then rules; an LLM narration sits on top, but no ML, no anomaly detection, no forecast"; `_GAP_RULES` and `_ACHIEVEMENT_RULES` in `scorer.py`; the RFC rates the achievements card "Low — vanity metric" | Rule tables cannot produce the financial readiness signal the page is asked for, and RFC 25 has already decided the remedy | The RFC's Option A: keep the shell, swap the widgets for KPI cards and analytics, drop the vanity metric, tighten the cache. Option B (a parallel new module) was explicitly deferred and stays deferred | A4, B1 | Building a second intelligence module beside this one, which is precisely what Option B warned against | **03** |
| P2 | The in-memory rate limiter implementation | `core/rate_limiter.py` docstring: "Simple in-memory rate limiter (no Redis required) ... For production, replace with Redis-based implementation" | Per-process counting makes the effective limit the configured limit times the process count, and login throttling is weakest under horizontal scale — the condition it exists to protect against | A Redis-backed implementation of the same interface, selected by configuration, keeping the in-process one for single-node and desktop installs where it is correct | A10, the existing `server` extra | Losing the zero-dependency install path. Avoided by making it a backend choice behind the same interface, not a new requirement | **02** |

## BUILD

Does not exist in any form. Each item is checked against the tree before being listed, so
none of these duplicates working code.

| # | Item | Evidence it is absent | Reason it is needed | Dependencies | Risks | Phase |
|---|---|---|---|---|---|---|
| B1 | Versioned canonical construction domain contract | The canonical JSON form covers CAD and BIM inputs ([ADR-002](../adr/002-no-ifcopenshell-ddc-canonical-only.md)) but no single contract spans projects, cost, schedule, procurement, documents and site records; the 788 models are the de facto contract and are not published | A separate intelligence package needs something stable to depend on instead of importing 788 models. This is the precondition for ground rule 5 | R2 | Over-specifying early and freezing a wrong shape; mitigated by versioning the contract and shipping it read-only first | **02** |
| B2 | Construction ontology layer | `core/validation/` encodes rules and registries; nothing exposes typed relations between construction concepts as queryable meaning | Rules can check data but cannot answer what something is or how it relates, which every reasoning capability needs | B1 | Duplicating the validation rule set; the ontology must reference the existing rules, not restate them | **03** |
| B3 | Project Intelligence Graph | No entity graph over project data exists. The only dependency graph in the tree is over modules, in `architecture_manifest.json` | Cross-domain reasoning needs edges between cost items, activities, documents, resources and risks | B1, B2, A7 | A graph that becomes a second source of truth; ground rule 1 forbids it, and the projection must be rebuildable and provenanced | **03** |
| B4 | Evidence Graph | `TrustEnvelope` cites sources per answer; sources are not linked into a traversable structure | "What supports this conclusion" must be answerable by traversal, and every citation must resolve to a record and a version | B3, the CDE, `claims_evidence` | Citations that resolve to stale versions; each edge must carry the document version | **03** |
| B5 | Cross-domain Dependency Engine | `depends` resolves module load order; `core/cpm.py` resolves schedule networks; nothing links a cost line to the drawing and activity it derives from | Impact and simulation both need dependency traversal across domains | B3 | Cycle handling and ambiguous derivation; needs an explicit, cited derivation record rather than inference | **04** |
| B6 | Unified simulation scenarios | BOQ what-if and `schedule_advanced` simulate inside their own domains; there is no scenario object holding a set of assumptions and reporting combined effect | What-if answering requires one assumption set evaluated across cost, schedule and procurement | B5 | Non-reproducible scenarios; every result must record the assumptions and the data version | **05** |
| B7 | Proactive detection layer | `triggers.py` fires on declared events and `scheduler.py` runs on a schedule; nothing decides that an unasked-about condition warrants attention | Proactive intelligence is the difference between a tool that answers and one that warns | A8, B5 | Alert fatigue and unrequested agent action; the strictest permission defaults and a citation requirement per alert | **05** |
| B8 | Agent authorization policy service | Permission is enforced at the HTTP edge by `RequirePermission` and, by convention, inside tools; `required_permission_for_tool` maps names to permissions but the registry does not enforce it | Ground rule 4 and the execution contract need one place where an agent's authority is defined, checked and logged | A2, A3 | Duplicating the RBAC engine; the service must consume it, never reimplement it | **02** |
| B9 | Approval records for agent-proposed mutations | `proposals.py` implements the flow for BOQ positions; `approval_routes` exists as a module; there is no general record of "an agent proposed X, person Y approved it at Z" across domains | Human approval must be auditable for every sensitive operation, not only BOQ applies | B8, the audit log | Two approval systems; extend the audit discipline already in place | **02** |
| B10 | Metrics and tracing | Only `/api/health` was found; no `metrics`, `ready` or `live` route in `main.py`; no tracing | Capacity, latency and saturation cannot be answered from the platform, and the recorded 139.7-second startup incident shows the cost of that blindness | R1 | Metric cardinality explosion over 4,627 routes; label discipline must be defined before the first endpoint | **02** |
| B11 | Import-boundary guard for the intelligence package | No such guard exists among the 69 `check_*` scripts, and nothing today prevents a new package from importing `app.core` internals | Ground rule 5 has to be enforced mechanically, or the separation survives only as long as reviewers notice. The repository's own history, recorded in `check_architecture_manifest.py`, is an argument for guards over memory | B1 | A guard that fires on legitimate code gets deleted; scope it to the new package only | **03** |

## Phase 02 scope, as recommended

Phase 02 is ERP hardening: close the control gaps before automated agents are given wider
reach. It contains no new intelligence capability, on purpose.

1. **A1** — the RLS enablement path: non-superuser connection, measured `tenant_id`
   coverage, backfill, then a strict predicate, with the 45 IDOR tests as acceptance.
2. **A2 and B8** — enforce tool authorization in the registry, and give agent authority one
   defined home.
3. **B9** — general approval records for agent-proposed mutations.
4. **A3** — inference declarations beyond the current 9 of 213, with a ratcheting guard.
5. **R3** — a reproducible migration path alongside the boot-time heal.
6. **R1** — split the composition root, stage by stage.
7. **B10** — metrics and tracing, with label discipline agreed first.
8. **A10 and P2** — a Redis-backed rate limiter behind the existing interface.
9. **R5 and B1** — a published, versioned API and domain contract.
10. **A13** — correct or generate the stale documentation counts, including the structural
    claims in `llms.txt` about `services/`, Kubernetes and a root `alembic/` directory.
11. **CSP tightening plan** — `script-src` currently allows `'unsafe-inline'` and
    `'unsafe-eval'`; a nonce or hash migration is a project in itself and Phase 02 should
    produce the plan and the first measured step, not a claim that it is solved.

Explicitly out of Phase 02: the ontology, the graphs, simulation and proactive detection.
They depend on the contract and the authorization gate, and starting them first would build
reasoning on top of an unenforced boundary.

## Blockers and limitations

Recorded so that no later reader mistakes them for passing results.

- **The backend test suite could not be executed in the audit environment.**
  `backend/pyproject.toml` requires Python 3.12 or newer; the environment provides 3.11.2;
  25 of 3,199 files under `backend/app` do not parse on 3.11; a 3.12 interpreter could not
  be obtained; and `backend/tests/conftest.py` raises when it cannot boot embedded
  PostgreSQL. **CI is the authority on test results.** At the audited commit its recorded
  state is 46 of 46 check runs `success`, with CI at 20 of 20 jobs and the PostgreSQL
  matrix at 16 of 16.
- **No runtime was started.** Every behavioural statement comes from code, tests and
  recorded CI results, not from an observed response.
- **Fork platform settings are not changeable from here.** The actions-permissions, rerun
  and dispatch endpoints refused the available token, so the five `disabled_fork`
  workflows — including CodeQL and OpenSSF Scorecard — are reported rather than enabled.
  Enabling them is an owner action on the fork.
- **Workflow job logs could not be downloaded**; job conclusions, step timings and check
  annotations were used instead.
- **Test-file counts are a proxy.** They are name-matched counts of files, reproducible
  with `find`, and they say what tests are about rather than what they assert.
- **Capability statuses are static readings.** A module classified Partial here may be
  further along at runtime than its code suggests, or less; where the repository has
  already assessed itself, as RFC 25 does for project intelligence, that assessment is
  cited rather than second-guessed.

## Where to go next

- [FOUNDATION_AUDIT.md](./FOUNDATION_AUDIT.md) — the measurements these decisions rest on.
- [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md) — the architecture and its ground
  rules.
- [Architecture overview](./README.md) — how the codebase fits together today.
- [ADR index](../adr/README.md) and [RFC index](../rfc/README.md).
