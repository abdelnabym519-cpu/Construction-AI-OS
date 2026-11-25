# Phase 02b review guide

Who this is for: somebody about to review the tenancy foundation on this branch, who
wants to know where to look first, what is risky, and what the tests do and do not
cover. It is a reading order, not a summary - the summaries are in
[ROADMAP_STATUS.md](./ROADMAP_STATUS.md#phase-02b-delivered) and
[ADR 005](../adr/005-organization-is-the-tenant.md).

The branch carries 30 commits over `main`. Sixteen belong to earlier phases and were
reviewed there: the documentation media removal and Academy removal (1-7), three CI
repairs (8-10), the Phase 01 audit (11), and Phase 02's agent gate, strict RLS mode and
PostgreSQL evidence (12-16). **The fourteen below are Phase 02b.**

## The commits, grouped by theme

### The decision

| Commit | What it does |
|---|---|
| `5db2212` docs(adr): make the organization the tenant | ADR 005, plus `docs/adr/README.md`. Read this first: every other commit is an consequence of it |

### Schema and existing data

| Commit | What it does |
|---|---|
| `55be819` feat(users): make the organization the tenant in the schema | `Organization`, `Membership`, the `oe_users_tenant_backfill` ledger, `oe_projects_project.organization_id`, alembic `v3325_organization_tenancy`, the boot-time data repair, and the populated-database test |
| `50111cc` fix(tenancy): serialize the boot backfill with an advisory lock | The repair's concurrency, atomicity, idempotence and runtime, measured |

### The token and the door

| Commit | What it does |
|---|---|
| `92bc570` feat(users): make the token name the caller's organization | `tenant_scope.py`, the mint, the resolver with no fall-through to `sub`, the refusal at both payload dependencies, and the six-flow test suite |
| `fdc741c` feat(tenancy): bind the tenant of whoever starts an agent run | `bound_tenant` and the three agent run paths; corrects the unit test that pinned the old single-call-site invariant |

### Write and read sites

| Commit | What it does |
|---|---|
| `a6e2614` fix(modules): scope company-wide records by organization, not by person | Contacts, subcontractors, tendering, finance open-items import, and the contacts scope filter |
| `cf8e4c4` fix(tenancy): stop two routers resolving a tenant from the subject | Deletes the private `_tenant_id_from_payload` in the dashboards (26 sites) and compliance (4 sites) routers |

### Tests

| Commit | What it does |
|---|---|
| `9ced80c` test(tenancy): make the isolation fixtures build two companies | Two fixtures that asserted cross-tenant isolation while building one tenant |
| `5aa994d` test(tenancy): measure two organizations through the restricted role | Two real companies under `oe_app`, and the readiness gate across the repair |
| `b9c3bba` test(tenancy): repair two defects in the new tenancy tests | Defects in the tests above, found by running them |
| `f49ce4d` test(tenancy): name an organization where a test named a person | The contacts CRUD round trip, and the rollout path from a refused legacy token |

### CI and documentation

| Commit | What it does |
|---|---|
| `a4e4709` ci(tenancy): publish test counts so a green job is evidence | `scripts/publish_test_counts.py`, JUnit reports per pytest step, two publisher steps |
| `00b61cf` docs(tenancy): record Phase 02b and correct what it falsified | ADR accepted; the two architecture documents corrected in place; the Phase 02c list |
| `4fb6b60` docs(tenancy): record the executed evidence for Phase 02b | The measured counts, and what could not be measured here |

## The riskiest files, in the order I would read them

1. **`backend/app/modules/users/service.py`** - every credential this product issues
   goes through `_issue_token_pair`, and `_active_organization_id` decides which company
   a caller acts in, including creating one when a newcomer has none and refusing a
   switch to a company the caller does not belong to. It also carries the one
   control-flow repair in this phase that is not about tenancy: **`oidc_login`'s
   auto-creation-disabled refusal was the `else` of the role-sync branch inside the
   existing-user path, so on an install with no `OIDC_GROUP_ROLE_MAP` every returning SSO
   user was refused.** It is now the `else` of `elif auto_create:` inside `if user is
   None:`, which is where the message always claimed it was. The defect predates this
   branch - it is identical at `e373448` - and it is a dedent, so the diff is small and
   the behaviour change is not. Two regression tests cover it.
2. **`backend/app/dependencies.py`** - `reject_token_without_a_tenant` is called from
   both payload dependencies, so it sits in front of every authenticated route in the
   product. Review the refusal's exact conditions: it applies to access tokens only, and
   the refresh token deliberately carries no claim.
3. **`backend/app/modules/users/repairs.py`** and
   **`backend/alembic/versions/v3325_organization_tenancy.py`** - the only code here that
   rewrites rows a user can see, on every boot, on whoever's database the application is
   pointed at. The ledger write happens before the update it describes, the repair must
   not commit, and the downgrade replays the ledger before dropping anything. Note what
   it deliberately does *not* do: it never invents an owner for a row it cannot
   attribute, so such rows stay `NULL` and keep strict mode refused.
4. **`backend/app/core/tenant_scope.py`** - one function decides what a tenant is, and
   one ordering decides which company a member of several acts in. The ordering is
   written down in exactly one place because both the mint and the background paths call
   it; a second copy would drift.
5. **`backend/app/modules/dashboards/router.py`** - the largest single-file behavioural
   change: twenty-six call sites moved from a private resolver that fell back to `sub` to
   the shared one that returns `None`. `None` is fail-closed here, so a snapshot stamped
   with a person's id stops being readable by that person until the backfill re-points it.
6. **`backend/app/core/rls.py`** - `bound_tenant`, and the sentinel semantics underneath
   it: unbound keeps the connecting superuser and bypasses every policy, `None` stamps an
   empty GUC and matches nothing. Those two states are the difference between "not
   policied" and "policied and denied", and they are easy to conflate when reading.
7. **`backend/app/modules/finance/service.py`** - the open-items import filtered contacts
   by `tenant_id == owner OR (tenant_id IS NULL AND created_by == owner)`, which matches
   zero contacts once the backfill has run. Admin callers hid it. Proven by reverting the
   argument and watching the import return `(ready, errors) == (0, 3)`.

## How to check it rather than trust it

* `pytest tests/unit/test_tenant_scope.py tests/unit/test_every_token_flow_names_an_organization.py tests/unit/test_contact_tenancy.py tests/unit/test_which_paths_bind_a_tenant_and_which_still_do_not.py tests/unit/test_no_route_resolves_a_tenant_from_the_token_subject.py`
  - the contract, no cluster needed beyond the unit lane's own.
* `OE_TEST_DB=pg pytest tests/pg/test_tenancy_backfill_on_a_populated_database.py tests/pg/test_two_organizations_are_isolated_under_the_application_role.py tests/pg/test_the_tenancy_boot_repair_is_atomic_idempotent_and_locked.py`
  - migration, backfill, isolation under `oe_app`, and the repair's safety.
* `pytest tests/integration/test_a_legacy_token_is_refused_until_a_refresh_replaces_it.py`
  - what the upgrade does to somebody already signed in.
* `python scripts/publish_test_counts.py --junit <report> --target <file> --fail-on-skip`
  - the counts, from a JUnit report, locally or in CI.

## What these tests do not cover

* **The dashboards routes are not exercised here.** This sandbox's interpreter was built
  without `_ctypes`, `cad2data_bridge` imports pandas which imports `ctypes`, so the
  module loader skips `oe_dashboards` and no dashboards route is mounted. The log says so
  explicitly: `Router for module oe_dashboards was NOT mounted`. The source-level pin in
  `test_no_route_resolves_a_tenant_from_the_token_subject.py` and the route-level evidence
  in CI are what cover it.
* **Phase 02c is not implemented.** Six background paths still bind no tenant and two
  startup seeds still write a person's id into a tenant column. They are listed with call
  sites and risk in
  [ROADMAP_STATUS](./ROADMAP_STATUS.md#phase-02c-the-background-paths-that-still-bind-nothing),
  and a unit test asserts they are still unbound, so closing one has to be deliberate.
* **Strict mode is still off by default**, and nothing in this phase enables it on any
  default path. What changed is that the readiness gate can now open.
