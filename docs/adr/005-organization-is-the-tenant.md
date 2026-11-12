# 005. The organization is the tenant

Status: Accepted
Date: 2026-10-29

## Context

Phase 02 put PostgreSQL row-level security over every column the schema calls
`tenant_id` - 31 of them, across 169 model modules and 814 tables - and the
readiness report still refuses to let strict mode be enabled. Its blocker is
named `tenant_is_user`, and the name is the finding: there is no tenancy entity
in this schema. `app/core/tenant_scope.py` resolved "which tenant is the caller?"
by preferring a `tenant_id` claim that nothing issued and then falling back to
`sub`, the caller's user id, which is what migration `v231_contact_tenant_id`
documents as the single-tenant doctrine: *"For single-tenant installs ... it
equals the creator's user id."*

That fallback is why the blocker is permanent rather than pending. Data can be
migrated onto organizations perfectly, and the next request carrying a token
without the claim puts a person straight back into a tenant column. No amount of
backfilling closes it while the resolver is willing to invent a tenant out of an
identity.

The evidence this decision is built on, all of it read out of the repository and
the database rather than assumed:

- 29 of the 31 columns are `VARCHAR(36)`; `oe_governance_catalogue` and
  `oe_procurement_concept_framework` are `VARCHAR(64)`. Ten are `NOT NULL`. None
  carries a foreign key. Only eight have a `created_by` column beside them.
- `oe_projects_project` has **no** `tenant_id`. Project ownership is a single
  `owner_id` holding a user id, so projects are not RLS-policied today.
- Nothing anywhere stores an organization, a company or a workspace. The only
  candidate for "whose data is this" is the user row.

## Decision

**1. An organization is the tenant.** `oe_users_organization`: `id` (`GUID`,
`VARCHAR(36)`), `name`, `slug` (unique, indexed), `status`, and the base
timestamps. It carries no `tenant_id` of its own - an organization is not inside
a tenant - and it is verified not to appear in `tenant_tables()`, so no policy is
attached to it.

**2. Membership joins people to organizations.** `oe_users_membership`:
`user_id` and `organization_id` (both foreign keys, both `ON DELETE CASCADE`,
both indexed), `role`, `status` (`active`), `is_default`, and a unique
`(user_id, organization_id)` pair so a duplicate membership is impossible rather
than merely unlikely. The ownership graph is `Organization -> Membership -> User`
and `Organization -> Project`. No other edge is claimed, because no other edge
exists in the data.

**3. Projects gain a nullable `organization_id`**, indexed, backfilled from the
owner's active membership. It stays nullable on purpose: projects are not
RLS-policied, and tightening it to `NOT NULL` is a separate decision that should
be gated on the readiness report rather than bundled into this one.

**4. The active organization is chosen when a token is minted**, in
`UserService._issue_token_pair` - the single place all six token flows reach
(`login`, `demo_login`, `desktop_bootstrap`, `oidc_login`, `refresh_tokens`,
`change_password`), which an AST census in the test suite holds closed. It is
resolved from the database at that moment, for the same reason `role` is
re-hydrated on every request: a value somebody asserts about themselves is not a
value the system knows. Resolution order is active memberships, flagged default
first, then oldest, then by id. Two notes on that order: `created_at` carries
`server_default=now()`, which in PostgreSQL is the *transaction* timestamp, so
memberships written by one request tie on it and the id is the effective
tie-break; and determinism is the property that matters, because a caller must
not change tenant between two requests a second apart.

An explicit switch passes `organization_id=` and is checked against the caller's
own active memberships, refused with 403 otherwise. A caller with no membership
still gets a tenant, because the flows that reach this point are the ones that
create accounts - registration is followed by a login, `desktop_bootstrap`
provisions the owner, OIDC auto-creates on first sign-in - so refusing here does
not ask anybody a question, it locks an account out. Where the install has one
organization or none, the caller is belonged to the install's organization,
created if a fresh database has none. Where it already has several, the caller
gets an organization of their own rather than being placed in a company nobody
said they belonged to.

**5. The tenant value is the organization's id as a canonical 36-character
lowercase UUID string.** That fits all 31 existing columns - 29 are `VARCHAR(36)`
and the two `VARCHAR(64)` ones have room - so this is a data change with no type
migration and no column widening anywhere.

**6. Access tokens gain a `tenant_id` claim, and a token without one is refused.**
The claim is added through the existing `extra_claims` path in
`create_access_token`, so all six flows behave identically by construction rather
than by six edits. `resolve_tenant_id` loses its fall-through to `sub` and returns
the claim or `None`. An authenticated access token that names no organization is
refused at the door - `reject_token_without_a_tenant` in `app/dependencies.py`,
401 with `WWW-Authenticate: Bearer` - and the refusal is recoverable by design:
the refresh flow re-enters the mint point, so a credential issued before
organizations existed exchanges for one that names the organization the caller
has now, with no new sign-in. Refresh tokens deliberately carry **no** tenant
claim: a refresh token is not a scoping credential, and freezing an organization
into a 30-day credential would make a switch or a moved membership wait for it to
expire.

**7. The backfill is a boot repair, not a migration step.** This product does not
run `alembic upgrade head` against an existing database: `postgres_auto_migrate`
creates missing tables and then stamps head, and the repository's own
data-rewrite guard exists precisely because a forward transformation written only
into a revision never executes. So the transformation lives once, in
`app/modules/users/repairs.py`, as a `DataRepair`
(`users_tenant_organization_backfill`, nature `always_wrong`) that runs on every
boot for every install shape. It is PostgreSQL-only, idempotent, batched, and it
records what it changed in `oe_users_tenant_backfill` (`table_name`, `row_id`,
`previous_tenant_id`) so that a downgrade can undo it and an operator can read
what happened. Its steps: adopt the oldest existing organization or create the
default one; create an active default membership for every user; re-point every
`tenant_id` that names a user to that user's organization; and leave genuinely
unattributable rows `NULL` for `null_tenant_rows` to count, rather than guessing
an owner - only 8 of the 31 tables have a `created_by` to guess from.

Idempotence here is not a nicety. PostgreSQL reports rows *matched*, not rows
*changed*, so an `UPDATE ... WHERE tenant_id IS NULL` whose subquery yields `NULL`
re-matches the same row forever and reports progress while making none. Every
statement in the repair excludes the rows it cannot resolve.

**8. Downgrade replays the ledger, then removes the schema.**
`v3325_organization_tenancy.downgrade()` restores each recorded row to its
previous value - including `NULL`, which is a value - then drops the three
tables and the project column. On a populated database that returns ownership
and row counts to what they were. The unrecoverable case is named rather than
hidden: rows written *after* the backfill are not in the ledger, so a downgrade
cannot know what they replaced.

## Trade-offs

Rejected, with the reason each was rejected:

- **Re-hydrate the tenant at the door**, the way `role` and `permissions` are
  re-read on every request. It would put a membership lookup on every request and,
  worse, make a token's tenant whatever the database says now - which defeats both
  the explicit switch and the idea that a credential means something.
- **Refuse inside `resolve_tenant_id`.** It has to stay a total pure function that
  returns `None`, because anonymous and system callers legitimately have no tenant
  and must not raise.
- **Refuse inside the global `rls_request_context`.** That runs for public and
  anonymous requests too, so it would put a 401 on endpoints that are meant to
  answer without a caller.
- **A permissive `tenant_id IS NULL` exception** as a universal bypass: excluded by
  standing instruction, and it would make NULL a privilege.
- **Keeping the `sub` fallback for one release** to soften the rollout. This is the
  one that mattered: it is exactly what makes `tenant_is_user` unresolvable, and a
  softer rollout that cannot reach strict mode buys nothing.

## Consequences

Strict mode stays OFF by default and nothing here enables `OE_RLS_STRICT`; the
point of the change is to make it *possible*, honestly, once readiness agrees.

Four places in the existing code were contradicted by this decision and had to
change with it, rather than being left to fail quietly:

1. `tenant_scope_owner(session, tenant_id)` passed the **tenant** to `_is_admin`,
   which looks its argument up in the users table. While a tenant was a user that
   worked; afterwards it finds nothing, fails closed, and every administrator
   silently loses the unfiltered view of the company-wide resources this helper
   targets. It now takes a required keyword `actor_id`, and a test pins what the
   admin lookup is called with rather than only what it returns.
2. `finance.import_open_items` filtered
   `Contact.tenant_id == owner OR (tenant_id IS NULL AND created_by == owner)`.
   After the backfill that matches zero contacts. It now takes the tenant
   explicitly from the request. The `created_by == owner` arm is dead afterwards -
   it compares a user id with an organization id - but harmless; it is recorded as
   a follow-up rather than left as a defect.
3. Exactly seven write sites store a user id into a `tenant_id` column. Five are
   on the request path and are corrected here (the contacts service and its two
   router callers, the subcontractors router twice, the tendering router twice).
   Two are seeds - `pointcloud/seed.py` and `value/seed.py` - and are deliberately
   left for Phase 02c, with the consequence stated: an install seeded or demoed
   after this change re-raises `tenant_is_user` and therefore cannot reach strict
   mode until those two are bound to an organization.
4. Dropping the fallback changes what `resolve_tenant_id(...) is None` means. It is
   bounded: `tenant_scoped_owner_filter` has no endpoint wired to it, anonymous
   callers already produced `None`, and `CatalogueStore._available` *adds* tenant
   catalogues only when a tenant is present - so a claimless caller treated as
   anonymous sees fewer rows, not more. The one place a `None` tenant could have
   widened a query was checked rather than assumed.

## Verification

- `backend/tests/unit/test_tenant_scope.py` - the resolution policy, the door's
  refusal and its exemption for refresh tokens, and the actor/tenant separation in
  `tenant_scope_owner`.
- `backend/tests/unit/test_every_token_flow_names_an_organization.py` - one test per
  flow, plus the AST census that makes six green tests sufficient.
- `backend/tests/pg/test_tenancy_backfill_on_a_populated_database.py` - upgrade,
  repair, idempotence over three passes, and downgrade on a populated database.

## References

- `backend/alembic/versions/v231_contact_tenant_id.py` - the doctrine this replaces.
- `backend/app/core/tenant_scope.py`, `backend/app/core/rls_setup.py`,
  `backend/app/core/rls_readiness.py`.
- `backend/app/modules/users/repairs.py`,
  `backend/alembic/versions/v3325_organization_tenancy.py`.
- `docs/architecture/ROADMAP_STATUS.md` - Phase 02 status and the blocker list.
