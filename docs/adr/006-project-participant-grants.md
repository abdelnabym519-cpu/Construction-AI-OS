# ADR 006: Project participant grants

Status: **proposed, design only. Nothing in this document is implemented.** No migration, no
model, no route and no policy in this ADR exists in the code yet. Where a number below is
measured, the command that measured it is given; where a claim is not yet measured it is marked
**unverified** and stays marked until somebody runs it.

Supersedes nothing. Extends [ADR 005, organization is the tenant](./005-organization-is-the-tenant.md),
which made a company the tenant and left one question deliberately unanswered: what happens when
work on a project belongs to more than one company.

Owner decision recorded for this ADR: **projects may span multiple organizations.** A project
belongs to one organization; other organizations participate through explicit project
participant grants. The target row-level security shape is:

> a row is visible when the caller's organization owns it, **or** the caller's organization holds
> a grant on the project the row belongs to.

This is a design because Phase 02b does not have the evidence to build it, and because building
it before the tenancy backfill is provably complete would put an authorization decision on top of
data whose owner is still ambiguous.

## 1. What the code stores today

Verified against the tree at the commit this ADR was written on, not assumed.

| Question | Answer in the code today | Evidence |
|---|---|---|
| What does a project store about ownership? | Two different keys. `owner_id`, a **person**, `NOT NULL` in practice; and `organization_id`, a **nullable** company reference with `ON DELETE SET NULL` | `app/modules/projects/models.py:102` and `:126` |
| Which of the two does product code read? | Overwhelmingly the person. **75** read sites of `Project.owner_id` across `app/`. `Project.organization_id` had **one** reader in product code, added by the Phase 02b repair work, plus the repair's own docstring | `grep -rn "Project.owner_id" app --include=*.py \| wc -l` = 75 |
| Who may reach a project's routes? | `verify_project_access`: the owner, an admin, or a team member. Both "missing" and "denied" answer 404 so a UUID cannot be probed | `app/dependencies.py:920`, `app/modules/teams/access.py::is_project_member` |
| What is team membership? | Person to team to project: `Team(project_id=...)`, `TeamMembership(team_id=..., user_id=...)`. There is **no** organization-level membership of a project anywhere in the schema | `tests/pg/governance_fixtures.py::add_project` builds exactly this; no model in `app/modules` names an organization as a project participant |
| How many rows are tenant-owned, and how many are project-scoped? | **30** `tenant_id` columns across **16** model files, against **419** `project_id` columns across **147** model files. **11** files carry both | `grep -rn "tenant_id: Mapped" app/modules --include=models.py \| wc -l` = 30; the same for `project_id: Mapped` = 419 |
| What is the RLS surface? | `tenant_tables()` returns the tables that carry a `tenant_id` column, minus an explicit never-policied set. So the policied surface today is the 30-column set, not the 419 project-scoped columns | `app/core/rls_setup.py:202` |
| What does the strict predicate say? | `(tenant_id)::text = current_setting('app.current_tenant', true)`, one GUC, one value, equality | measured on PostgreSQL 16.11 in Phase 02b; `app/core/rls_setup.py` |

Two consequences follow directly, and they are the reason this ADR exists.

**The tenant boundary and the project boundary are not the same shape.** Tenancy is a single
scalar per row and a single scalar per request, compared for equality. Project participation is a
many-to-many relation between companies and projects. Equality cannot express it, so a grant has
to become part of the predicate rather than part of the data.

**419 project-scoped columns against 30 tenant-owned ones means most of the schema cannot be
policed by `tenant_id` at all.** A delivery note, a diary entry, a BIM issue and a certificate are
project-scoped rows. Today they are protected by `verify_project_access` in the route, which is an
application-level gate, not a database-level one. Grants change what that gate must answer, and
eventually what the database must answer for those tables too.

### 1.1 The seam this ADR was written next to

`ProcurementConceptService.project_frameworks` is the one place in product code that already
implements cross-company visibility on a project, and it is worth reading as the prototype of both
the feature and its hazard:

```python
tenants = [t for t in dict.fromkeys((await self.project_tenant(project_id), tenant_id)) if t]
```

A project member sees the frameworks of the project's company **and** of their own. Before the
fix that accompanies this ADR, `project_tenant` read `Project.owner_id`, so it returned a person
where every tenant-scoped row now carries a company, the first half of that union matched
nothing, and a project member from another company saw an empty list. Two PostgreSQL tests failed
on exactly that, and both pass now that it reads `organization_id`.

Note what that union is: **any team member of a project can read another company's frameworks,
with no grant, no expiry and no audit row.** That is today's behaviour, it is intentional in the
product sense, and under the decision recorded above it is precisely what a grant is meant to
make explicit. This ADR treats that call site as the first thing the grant predicate replaces,
and as the standing example of why "team membership implies cross-company read" needs an owner's
decision rather than an accident of a column read.

## 2. The grant entity

One row per participating company per project. Deliberately not per user: a company participates,
and its members inherit that participation through their membership of the company, which is the
composition ADR 005 already established.

Table `oe_projects_participant_grant`:

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `project_id` | `UUID` FK to `oe_projects_project`, `ON DELETE CASCADE` | a grant on a deleted project is meaningless |
| `grantee_organization_id` | `UUID` FK to `oe_users_organization`, `ON DELETE CASCADE` | the participating company. Never a user id: that is the ADR 005 mistake, and a grant that named a person would reintroduce it |
| `granted_by_user_id` | `UUID` FK to `oe_users_user`, `ON DELETE SET NULL` | the human who decided. Kept after they leave, so the decision survives the person |
| `granted_on_behalf_of_organization_id` | `UUID` FK, `ON DELETE CASCADE` | the owning company at grant time. Recorded because ownership can move and the authority to grant moves with it |
| `scope` | `VARCHAR(32)`, `NOT NULL` | what the grantee may do. Initial vocabulary: `read`, `contribute`, `steer`. Not a free string; a check constraint or an enum in the model |
| `status` | `VARCHAR(16)`, `NOT NULL`, default `active` | `active`, `suspended`, `revoked`. Mirrors `Membership.status`, so the two authorization layers read the same way |
| `valid_from` / `valid_until` | `TIMESTAMPTZ`, both nullable | open-ended when null. A tender period or a site handover is the normal reason to set them |
| `revoked_at`, `revoked_by_user_id`, `revocation_reason` | nullable | revocation is an event with an author and a reason, not a deletion |
| `created_at`, `updated_at` | `TIMESTAMPTZ`, `NOT NULL` | |
| `metadata_` | `JSON` | consistent with the rest of the schema |

Constraints that carry the design:

* **`UNIQUE (project_id, grantee_organization_id)`** where `status <> 'revoked'`, expressed as a
  partial unique index. One live grant per company per project. Re-granting after a revocation
  inserts a new row, so the history survives; the index keeps the live set unambiguous, which is
  what the predicate reads.
* **The owning company cannot hold a grant on its own project.** Enforced in the service, not the
  database, because it compares two columns across tables; a test asserts it.
* **`valid_until > valid_from`** when both are set.
* A grant does **not** create team memberships and does not create users. It authorizes a company;
  the people are already the grantee company's members.

## 3. How it composes

```
Organization --< Membership >-- User            (ADR 005: who the caller acts as)
     |
     +-- Project.organization_id                (the owning company, exactly one)
     |
     +--< ParticipantGrant >-- Project          (participating companies, zero or more)

Project --< Team --< TeamMembership >-- User    (today's route-level gate, person-scoped)
```

The two layers answer different questions and both stay:

* **Company layer (new).** Is the caller's organization the owner of this project, or a live
  grantee on it? This is the tenant question, and it is the one that belongs in the database.
* **Person layer (existing).** Is this specific person on the project's team, and what role do
  they hold? `verify_project_access` keeps answering it. A grant does not put anybody on a team.

A caller is authorized on a project when **both** layers answer yes: their company participates,
and they personally are on the team (or are an admin). A company-level grant with no team
membership must not open a project, because that would let a company's entire staff read a
project nobody assigned them to. Team membership with no company participation must not open it
either, which is the case that exists today and is the hole this ADR closes.

That is a tightening, and it needs an owner's decision (open question 1) because it will refuse
access that currently succeeds.

## 4. The RLS predicate

### 4.1 Shape

Two GUCs, both set by the same `bound_tenant` call that sets today's one, so there is still a
single place where a request's identity enters the database:

* `app.current_tenant` - the caller's organization id, as today.
* `app.granted_projects` - the comma-separated project ids the caller's organization participates
  in, computed **once per request** from the grant table. Empty string when there are none.

```sql
USING (
  (tenant_id)::text = current_setting('app.current_tenant', true)
  OR (project_id)::text = ANY (
       string_to_array(current_setting('app.granted_projects', true), ',')
     )
)
```

Computing the visible-project list per request rather than per row is the whole performance
argument. A correlated `EXISTS` against the grant table inside `USING` re-plans per row and is
the shape that turns a scan into a nested loop over a million rows.

Fail-closed behaviour is inherited and must not change: when `app.current_tenant` is unset or
empty, the first arm matches nothing; when `app.granted_projects` is empty, `string_to_array`
yields `{""}` and the second arm matches nothing either, because no project id is the empty
string. A test asserts both arms refuse separately.

### 4.2 Performance on a 1M-row table - **unverified, with the harness named**

No measurement exists yet and this ADR will not invent one. What is known:

* The repository already has a million-row PostgreSQL harness,
  `tests/pg/test_a_million_rows_are_rewritten_fast_enough_to_boot_on.py`, which builds 4 x 250,000
  rows and asserts a floor of 20,000 rows per second. It measured 23,141 to 26,857 rows/s for the
  tenancy repair on this sandbox's embedded PostgreSQL 16.11. The same harness is the vehicle for
  the grant predicate.
* The predicate above is an `OR` of two equality tests, one against a column and one against a
  small in-memory array. PostgreSQL can serve it with a bitmap OR of two index scans **only if
  both sides are indexable**. `tenant_id` is indexed on the policied tables; `project_id` is
  indexed on some of the 419 and not on others, and that census is part of stage 1 below.
* The risk is not the array test, it is the `OR`: an `OR` across two different columns defeats a
  single index scan and can degrade to a sequential scan on a wide table. If measurement shows
  that, the fallback is a **denormalized visibility column** - stamp the owning organization on
  project-scoped rows at write time, so the predicate becomes one equality again. That is a
  bigger migration and is why it is the fallback and not the plan.

Acceptance bar to hold the design to, once measured: a filtered read of a 1M-row project-scoped
table under the grant predicate must not be slower than the same read under today's single-GUC
predicate by more than a factor to be agreed with the owner (open question 6), and must not fall
below the 20,000 rows/s floor the existing harness already enforces.

### 4.3 Grant lookup cost

`app.granted_projects` is one indexed query per request against a table whose size is bounded by
projects x participating companies, not by rows. It belongs in the same request-scoped binding
path as `bound_tenant`, and it must be cached per request, not per row and not per statement.
Background paths that bind no tenant today (the Phase 02c list) would bind no grants either, so
they must not silently gain the empty-list arm as a way to see everything: empty means nothing,
in both GUCs.

## 5. Project-scoped versus tenant-owned

The measured split, and what each side needs:

| Class | Count | Examples | What polices it today | What polices it under grants |
|---|---|---|---|---|
| Tenant-owned | **30** columns, **16** model files | `oe_procurement_framework_agreement`, `oe_contacts_contact`, `oe_activity_log`, `oe_compliance_dsl_rule`, `oe_eac_block_graph` | `tenant_id` equality, RLS strict where enabled | unchanged, plus the project arm only where the table also carries `project_id` |
| Both | **11** model files carry `tenant_id` and `project_id` | the procurement concept tables | both columns exist; only `tenant_id` is policied | **the seam.** These get the two-arm predicate first, because both sides of the `OR` are already columns on the row |
| Project-scoped only | **419** columns, **147** model files | deliveries, diary entries, BIM issues, certificates, teams, routes, governance areas, gates, documents | `verify_project_access` in the route; no database policy | a project-level policy, or a denormalized owner column. Stage 4, and the largest single body of work in this ADR |

The order matters: the 11 files that carry both columns are where a grant can be enforced in the
database with no schema change at all. The 147 project-only files cannot be policied by
`tenant_id` because they do not have one, and adding one to all of them is the "mechanically add
`tenant_id` to every model" move that Phase 02 explicitly rejected.

## 6. Who may create and revoke a grant

* **Create:** a caller whose organization owns the project, holding a manage-level permission on
  it, acting through the ordinary authorization path. Not the grantee: a company cannot invite
  itself.
* **Revoke:** the same authority, plus a platform admin. Revocation is immediate and is an event,
  not a delete (see section 8).
* **Accept:** open question 2. Whether the grantee company must accept before the grant is live is
  a commercial and legal question, not a technical one. The schema supports both - `status` can
  carry `offered` before `active` - and the predicate only ever reads `active`.
* **Agents:** any grant write reached through an AI agent path must pass the same central
  authorization gate as every other sensitive operation
  (`app/modules/ai_agents/tool_authorization.py`), must be tenant-bound, and must produce an audit
  row. No agent path may write this table directly, and no agent may execute SQL against it.

## 7. What a grantee organization can read and write

Per `scope`, and stated as refusals so the tests have something to assert:

| Scope | Reads | Writes |
|---|---|---|
| `read` | project-scoped rows of the project, and the owning company's rows that the project's routes deliberately expose - today that is the `project_frameworks` union | nothing. Every write route refuses |
| `contribute` | as `read` | creates and edits **its own** rows on the project: diary entries, deliveries, documents it uploads. Cannot edit the owner's rows, cannot approve, cannot change the plan |
| `steer` | as `contribute` | the project's decision routes: approvals, gate decisions, plan changes, within the caller's own RBAC role |

Two rules that are load-bearing:

**A grantee writes only rows it owns.** A row created under a grant carries the **grantee's**
`tenant_id`, not the project owner's. Ownership follows the author's company, because that is
what makes the row's own tenant scope honest and what keeps a revoked grant from orphaning data.
Consequence: after revocation, the grantee's rows stay the grantee's, and the owner can no longer
read them through the project arm. Whether that is acceptable is open question 3, and the
alternatives are a handover step at revocation or an owner-side copy.

**Revocation is not retroactive.** It stops the predicate from matching from the moment it is
written. It does not delete, re-stamp or hide anything, and it must not be implemented as a data
rewrite: an unbounded `UPDATE` on revocation is exactly the destructive-migration shape Phase 02
rejected.

## 8. Audit trail

Every create, scope change, suspension and revocation writes an `oe_activity_log` row (which is
already tenant-owned and already backfilled) naming the project, the grantee organization, the
acting user, the previous and new state, and the reason where one was given. The grant table keeps
its own history too: revoked rows are retained, so the sequence of grants on a project is
reconstructible from the table alone even if the activity log is pruned.

A grant is an authorization decision, and the standard for those in this repository is that the
conclusion is traceable to evidence. Both records exist so that "who could see this project on
that date" is answerable after the fact.

## 9. Migration impact on the tenant columns

* **Zero changes to the 30 existing `tenant_id` columns.** The grant adds a table and a second
  GUC; it does not re-stamp a single row. That is deliberate: a migration whose main action is an
  unbounded rewrite of 30 columns across a populated install is the thing Phase 02b spent its
  effort making unnecessary.
* **One new table**, created under the migration guards this repository already enforces:
  single head, DDL guarded for existence, no data rewrite in the migration body. If any backfill
  is ever needed it goes in a `DataRepair` with a ledger, an advisory lock and an idempotent
  re-run, not in the revision.
* **The 11 both-column files** need an index check on `project_id` before their policy changes.
  Stage 1 measures which of them already have one.
* **The 147 project-only files** are untouched until stage 4, and stage 4 is a decision per
  table, not a sweep.
* **Interaction with strict mode.** Enabling `OE_RLS_STRICT` today installs the single-arm
  equality predicate. Turning it on before grants exist would refuse every cross-company project
  read that currently works, including the `project_frameworks` union. So grants are a
  **precondition for strict mode**, not a follow-up to it. That ordering is recorded in the
  Phase 02c list.

## 10. Staged plan

Each stage is separately shippable and separately testable, and no stage weakens an existing test.

1. **Census, no behaviour change.** For the 11 both-column model files, record whether
   `project_id` is indexed and how many rows each table carries on a representative install.
   Deliverable: a table in this ADR replacing the word **unverified** in section 4.2 with numbers.
2. **The table and its service.** `oe_projects_participant_grant`, the model, the migration, a
   service with create/change-scope/suspend/revoke, and the two rules from section 6. No
   predicate change, so nothing that works today stops working. Tests: the unique-live-grant
   constraint, the owner-cannot-grant-itself refusal, revocation retaining the row, and the audit
   row for each transition.
3. **The second GUC.** `bound_tenant` also sets `app.granted_projects`, computed once per request
   and cached. Still no policy change. Tests: the GUC is empty for a caller with no grants, is
   correct for a caller with several, and is empty - not unset - for every background path on the
   Phase 02c list.
4. **The predicate, on the 11 both-column tables first,** behind the existing strict-mode switch,
   measured against the million-row harness with the acceptance bar in section 4.2. Only then the
   project-only tables, one decision at a time.
5. **Retire the accidental union.** `project_frameworks` stops deriving cross-company visibility
   from team membership and reads the grant instead. This is the stage that can refuse access that
   works today, so it needs the owner's answer to open question 1 first, and it needs a rollout
   note in the same shape as the token rollout Phase 02b shipped.

## 11. Open questions for the owner

1. **Does a grant tighten access that works today?** Team membership currently implies
   cross-company read on the `project_frameworks` path and, at route level, on every project
   route. Making company participation a precondition will refuse some callers who succeed today.
   Is that intended, and is there a migration window in which both answers are accepted?
2. **Must the grantee accept?** `status` can carry `offered` before `active`. Two-sided grants are
   the norm in joint ventures; one-sided is simpler and is what the schema assumes by default.
3. **What happens to a grantee's own rows when the grant is revoked?** They stay the grantee's
   (section 7). Is a handover or an owner-side copy required for any document class -
   certificates and signed deliverables are the likely candidates?
4. **Does a grant survive a change of project ownership?** `granted_on_behalf_of_organization_id`
   records who granted it. If the project is sold or transferred, are existing grants honoured,
   suspended pending review, or revoked?
5. **Can a grantee sub-grant?** This ADR assumes no: only the owner grants. Confirm, because a
   consortium structure may need a `steer`-scope grantee to bring in its own subcontractors.
6. **What slowdown is acceptable** for a filtered read of a 1M-row project-scoped table under the
   two-arm predicate, against the single-arm baseline? A factor, or an absolute budget in
   milliseconds, so stage 4 has a pass mark.
7. **Is `scope` a three-value vocabulary or a permission set?** `read` / `contribute` / `steer` is
   the proposal. If grants must map onto existing RBAC permissions instead, the column becomes a
   reference to a permission set and section 7's table is derived rather than written.
8. **Do individuals ever participate without their company?** A freelance engineer seconded to a
   project has no company to grant. Today team membership covers them. If that must keep working,
   the person layer stays authoritative for those callers and the predicate needs a third arm -
   which is a materially more expensive design and should be decided before stage 2.

## 12. Evidence and verification log

Measured on this tree, in this sandbox, CPython 3.12.8 against a real embedded PostgreSQL 16.11.

| Claim | Command or source | Result |
|---|---|---|
| `tenant_id` columns | `grep -rn "tenant_id: Mapped" app/modules --include=models.py \| wc -l` | **30**, across **16** files |
| `project_id` columns | `grep -rn "project_id: Mapped" app/modules --include=models.py \| wc -l` | **419**, across **147** files |
| files carrying both | `comm -12` over the two file lists | **11** |
| `Project.owner_id` read sites | `grep -rn "Project.owner_id" app --include=*.py \| wc -l` | **75** |
| `Project.organization_id` product readers | `grep -rn "Project.organization_id" app` | **1**, `procurement_concept/service.py:2089`, plus the repair docstring |
| RLS surface definition | `app/core/rls_setup.py:202` `tenant_tables()` | tables carrying `tenant_id`, minus `_NEVER_POLICY` |
| route-level project gate | `app/dependencies.py:920` | owner, admin or team member; 404 for missing and denied alike |
| million-row harness exists | `tests/pg/test_a_million_rows_are_rewritten_fast_enough_to_boot_on.py` | 4 x 250,000 rows, floor 20,000 rows/s |
| grant predicate performance | not run | **unverified** - stage 1 and stage 4 |
| index census on `project_id` | not run | **unverified** - stage 1 |

An earlier note in this repository's status records referred to "31 tenant columns". The measured
figure on this tree is **30**, by the command above. The difference is not reconciled here; the
measured number is the one to use, and the command is given so anybody can re-run it.
