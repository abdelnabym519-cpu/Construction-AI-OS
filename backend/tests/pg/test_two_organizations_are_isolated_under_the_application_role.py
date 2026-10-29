# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Two organizations, two members each, measured through the role the app connects as.

The other row-level-security tests in this lane pin the mechanism: what the strict
policy's predicate is, which role a session runs as, when strict mode is refused. This
one pins the thing the mechanism exists for - the ownership model ADR 005 chose. Two
companies exist as real ``Organization`` rows, each with two real ``Membership`` rows,
and the probe rows are owned by those companies' ids, which is exactly the string a
token's ``tenant_id`` claim carries. Nothing here is a stand-in tenant: the value the
policy compares against is the value the mint puts in the token.

Four questions are answered, and each is answered by a statement the restricted role
runs rather than by a query written by the test:

* do two members of one company see the same rows (they do, and they see them because
  they resolve to the same organization, not because they are the same person);
* does a member of the other company see, change, remove or add anything there (no, on
  all four verbs);
* what does a member of two companies see (only the one their token names, and the
  naming follows the documented default-then-oldest-then-id order);
* what happens when there is no organization to name - a suspended membership, or a
  token that predates the claim (nothing is visible; in particular the caller's own id
  is never used as a tenant, which is the fallback Phase 02b removed).

Every statement issued as the restricted role also reports ``current_user`` and
``rolsuper``, because a session that turned out to be a superuser would make every
count below correct for the wrong reason.

This runs on a throwaway database rather than the lane's shared cluster, since it
writes real application rows; the probe tables are still created and dropped here so
row-level security is never left enabled on a table the rest of the lane uses.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import rls as rls_module
from app.core import rls_setup
from app.core.rls import APP_ROLE, GUC_NAME, SYSTEM_ROLE
from app.core.tenant_scope import (
    MEMBERSHIP_ACTIVE,
    active_organization_id,
    active_organization_ids,
    resolve_tenant_id,
)
from tests._pg import isolated_engine
from tests.pg import rls_fixtures as rls

pytestmark = pytest.mark.asyncio

_PROBE = rls.PROBE_TENANT
_COUNT = f'SELECT count(*) FROM "{_PROBE}"'
_IDENTITY = "SELECT current_user, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"


def _restricted(outcome: rls.RoleOutcome) -> rls.RoleOutcome:
    """Assert the statement really ran as the restricted role, and hand it back.

    A superuser bypasses every policy even with ``FORCE ROW LEVEL SECURITY``, so an
    isolation assertion made by a superuser is true no matter what the policy says.
    Recording the identity next to every result is what stops that from being silent.
    """
    assert outcome.current_user == APP_ROLE, outcome.current_user
    assert outcome.rolsuper is False, outcome.rolsuper
    return outcome


@pytest_asyncio.fixture
async def two_orgs(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Two companies with two members each, one member of both, under the strict policy.

    Enforcement is switched on for the duration of the test only; the listener reads
    the flag when a transaction begins. The probe table is created after the
    organizations exist so its rows can be owned by their real ids.
    """
    monkeypatch.setattr(rls_module, "rls_enabled", lambda: True)
    monkeypatch.setattr(rls_setup, "rls_enabled", lambda: True)

    from app.modules.users.models import Membership, Organization
    from app.modules.users.models import User as UserModel

    async with isolated_engine() as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        people: dict[str, uuid.UUID] = {}
        async with factory() as session:
            for key, role in (("a1", "admin"), ("a2", "editor"), ("b1", "admin"), ("b2", "viewer"), ("dual", "editor")):
                uid = uuid.uuid4()
                people[key] = uid
                session.add(
                    UserModel(
                        id=uid,
                        email=f"{key}-{uid.hex[:8]}@two-orgs.test",
                        hashed_password="x",
                        full_name=f"Member {key}",
                        role=role,
                        locale="en",
                        is_active=True,
                        metadata_={},
                    )
                )
            alpha = Organization(slug=f"org-alpha-{uuid.uuid4().hex[:8]}", name="Alpha Construction")
            beta = Organization(slug=f"org-beta-{uuid.uuid4().hex[:8]}", name="Beta Construction")
            session.add_all([alpha, beta])
            await session.flush()
            for key, org in (("a1", alpha), ("a2", alpha), ("b1", beta), ("b2", beta)):
                session.add(
                    Membership(
                        user_id=people[key],
                        organization_id=org.id,
                        role="admin",
                        status=MEMBERSHIP_ACTIVE,
                        is_default=True,
                    )
                )
            # A member of both, so "which organization does this token name" is a
            # question with an answer the data decides rather than the test asserting.
            session.add(
                Membership(
                    user_id=people["dual"],
                    organization_id=alpha.id,
                    role="editor",
                    status=MEMBERSHIP_ACTIVE,
                    is_default=True,
                )
            )
            session.add(
                Membership(
                    user_id=people["dual"],
                    organization_id=beta.id,
                    role="editor",
                    status=MEMBERSHIP_ACTIVE,
                    is_default=False,
                )
            )
            await session.commit()
            alpha_id, beta_id = str(alpha.id), str(beta.id)

        base = rls.probe_base(_PROBE)
        async with engine.begin() as conn:
            await rls.drop_probe_tables(conn, _PROBE)
            await rls.ensure_roles(conn)
            await rls.create_probe_tables(conn, _PROBE)
            stats = await rls_setup.apply_rls(conn, base, strict=True)
            # A fixture that quietly fell back to the permissive policy would make
            # every count below wrong in the same direction, so assert the precondition.
            assert "strict_refused" not in stats
            assert stats["tables"] == 1
            for row_id, tenant, label in (
                ("alpha-1", alpha_id, "alpha one"),
                ("alpha-2", alpha_id, "alpha two"),
                ("beta-1", beta_id, "beta one"),
                ("beta-2", beta_id, "beta two"),
            ):
                await rls.seed(conn, _PROBE, (row_id, tenant, label))

        try:
            yield SimpleNamespace(
                engine=engine, factory=factory, base=base, alpha=alpha_id, beta=beta_id, people=people
            )
        finally:
            async with engine.begin() as conn:
                await rls.drop_probe_tables(conn, _PROBE)


async def _names(two_orgs, key: str) -> str | None:  # noqa: ANN001
    """The organization a member's work belongs to, resolved the way the mint does."""
    async with two_orgs.factory() as session:
        return await active_organization_id(session, two_orgs.people[key])


async def test_two_members_of_one_organization_see_the_same_shared_rows(two_orgs) -> None:  # noqa: ANN001
    """Shared, because they resolve to the same organization and not to each other."""
    for key in ("a1", "a2"):
        assert await _names(two_orgs, key) == two_orgs.alpha, key

    for key in ("a1", "a2"):
        tenant = await _names(two_orgs, key)
        outcome = _restricted(await rls.as_app_role(two_orgs.engine, tenant, _COUNT, fetch=True))
        assert outcome.rows == [(2,)], (key, outcome.rows, outcome.error)


async def test_a_member_of_the_other_organization_sees_none_of_them(two_orgs) -> None:  # noqa: ANN001
    """Beta's member sees Beta's rows and no part of Alpha's."""
    tenant = await _names(two_orgs, "b1")
    assert tenant == two_orgs.beta

    own = _restricted(await rls.as_app_role(two_orgs.engine, tenant, _COUNT, fetch=True))
    assert own.rows == [(2,)], (own.rows, own.error)

    # The statement is bound to Beta, so asking for Alpha's rows returns nothing at all.
    alpha_only = _restricted(
        await rls.as_app_role(
            two_orgs.engine,
            tenant,
            f"SELECT count(*) FROM \"{_PROBE}\" WHERE tenant_id = '{two_orgs.alpha}'",
            fetch=True,
        )
    )
    assert alpha_only.rows == [(0,)], (alpha_only.rows, alpha_only.error)


async def test_an_update_of_another_organizations_row_changes_nothing(two_orgs) -> None:  # noqa: ANN001
    """Committed, so what is asserted afterwards is what the database kept."""
    tenant = await _names(two_orgs, "b2")
    outcome = _restricted(
        await rls.as_app_role(
            two_orgs.engine,
            tenant,
            f"UPDATE \"{_PROBE}\" SET label = 'taken' WHERE id = 'alpha-1'",
            commit=True,
        )
    )
    assert outcome.rowcount == 0, (outcome.rowcount, outcome.error)
    assert outcome.error is None, outcome.error

    kept = await rls.admin_rows(two_orgs.engine, f"SELECT label FROM \"{_PROBE}\" WHERE id = 'alpha-1'")
    assert kept == [("alpha one",)], kept


async def test_a_delete_of_another_organizations_row_removes_nothing(two_orgs) -> None:  # noqa: ANN001
    tenant = await _names(two_orgs, "b1")
    outcome = _restricted(
        await rls.as_app_role(two_orgs.engine, tenant, f"DELETE FROM \"{_PROBE}\" WHERE id = 'alpha-2'", commit=True)
    )
    assert outcome.rowcount == 0, (outcome.rowcount, outcome.error)

    left = await rls.admin_rows(
        two_orgs.engine, f'SELECT count(*) FROM "{_PROBE}" WHERE tenant_id = :t', {"t": two_orgs.alpha}
    )
    assert left == [(2,)], left


async def test_an_insert_naming_another_organization_is_refused(two_orgs) -> None:  # noqa: ANN001
    """A write that names a tenant is checked against the bound one, not against the id."""
    tenant = await _names(two_orgs, "b1")
    outcome = _restricted(
        await rls.as_app_role(
            two_orgs.engine,
            tenant,
            f"INSERT INTO \"{_PROBE}\" (id, tenant_id, label) VALUES ('smuggled', '{two_orgs.alpha}', 'smuggled')",
            commit=True,
        )
    )
    assert outcome.error is not None, "inserting another organization's row must be refused"
    assert "row-level security" in outcome.error.lower(), outcome.error

    kept = await rls.admin_rows(two_orgs.engine, f"SELECT count(*) FROM \"{_PROBE}\" WHERE id = 'smuggled'")
    assert kept == [(0,)], kept


async def test_an_insert_naming_your_own_organization_is_kept(two_orgs) -> None:  # noqa: ANN001
    """So the refusal above is the policy, and not a missing table grant."""
    tenant = await _names(two_orgs, "b2")
    outcome = _restricted(
        await rls.as_app_role(
            two_orgs.engine,
            tenant,
            f"INSERT INTO \"{_PROBE}\" (id, tenant_id, label) VALUES ('beta-new', '{two_orgs.beta}', 'beta new')",
            commit=True,
        )
    )
    assert outcome.error is None, outcome.error
    assert outcome.rowcount == 1, outcome.rowcount

    # And the row is shared with the other member of the same company at once: both
    # resolve to Beta, so both see three rows without either of them being an admin.
    assert await _names(two_orgs, "b1") == tenant
    shared = _restricted(await rls.as_app_role(two_orgs.engine, tenant, _COUNT, fetch=True))
    assert shared.rows == [(3,)], shared.rows


async def test_a_member_of_two_organizations_sees_only_the_one_their_token_names(two_orgs) -> None:  # noqa: ANN001
    """The active organization decides, and it follows the documented order."""
    from app.modules.users.models import Membership

    async with two_orgs.factory() as session:
        ordered = await active_organization_ids(session, two_orgs.people["dual"])
    assert ordered == [two_orgs.alpha, two_orgs.beta], ordered

    as_alpha = _restricted(await rls.as_app_role(two_orgs.engine, ordered[0], _COUNT, fetch=True))
    assert as_alpha.rows == [(2,)], as_alpha.rows
    as_beta = _restricted(await rls.as_app_role(two_orgs.engine, ordered[1], _COUNT, fetch=True))
    assert as_beta.rows == [(2,)], as_beta.rows

    # Move the default and the same caller's next token names the other company. The
    # rows they can see move with it, which is the whole content of "active".
    async def _membership_of(org: str) -> Membership:
        async with two_orgs.factory() as session:
            return await session.scalar_one(
                select(Membership)
                .where(Membership.user_id == two_orgs.people["dual"])
                .where(Membership.organization_id == uuid.UUID(org))
            )

    async with two_orgs.factory() as session:
        (await _membership_of(two_orgs.alpha)).is_default = False
        (await _membership_of(two_orgs.beta)).is_default = True
        await session.commit()

    assert await _names(two_orgs, "dual") == two_orgs.beta
    now = _restricted(await rls.as_app_role(two_orgs.engine, await _names(two_orgs, "dual"), _COUNT, fetch=True))
    assert now.rows == [(2,)], now.rows
    gone = _restricted(
        await rls.as_app_role(
            two_orgs.engine,
            two_orgs.beta,
            f"SELECT count(*) FROM \"{_PROBE}\" WHERE tenant_id = '{two_orgs.alpha}'",
            fetch=True,
        )
    )
    assert gone.rows == [(0,)], gone.rows


async def test_a_suspended_membership_names_no_organization_and_so_sees_nothing(two_orgs) -> None:  # noqa: ANN001
    """A membership that is not active is not a tenant, and that fails closed."""
    from app.modules.users.models import Membership

    async with two_orgs.factory() as session:
        row = await session.scalar_one(
            select(Membership)
            .where(Membership.user_id == two_orgs.people["a1"])
            .where(Membership.organization_id == uuid.UUID(two_orgs.alpha))
        )
        row.status = "suspended"
        await session.commit()

    assert await _names(two_orgs, "a1") is None

    outcome = _restricted(await rls.as_app_role(two_orgs.engine, None, _COUNT, fetch=True))
    assert outcome.rows == [(0,)], (outcome.rows, outcome.error)


async def test_a_token_without_the_claim_never_degrades_to_the_callers_own_scope(two_orgs) -> None:  # noqa: ANN001
    """The removed fallback, measured: a person's id is not a tenant and reaches nothing.

    A row whose tenant column holds a person's id is seeded first, because that is the
    shape the old fallback could read - and the shape a single-tenant install leaves
    behind before the backfill runs. Neither an unbound tenant nor the caller's real
    organization can see it.
    """
    person = two_orgs.people["a1"]
    async with two_orgs.engine.begin() as conn:
        await rls.seed(conn, _PROBE, (f"legacy-{person.hex[:8]}", str(person), "legacy person-shaped row"))

    claimless = resolve_tenant_id({"sub": str(person), "type": "access", "role": "admin"})
    assert claimless is None, claimless

    # Bound to nothing - which is what a refused-then-anonymous caller binds - sees
    # nothing, rather than falling back to rows owned by the caller's own id.
    as_none = _restricted(await rls.as_app_role(two_orgs.engine, None, _COUNT, fetch=True))
    assert as_none.rows == [(0,)], (as_none.rows, as_none.error)

    person_rows = _restricted(
        await rls.as_app_role(
            two_orgs.engine,
            None,
            f"SELECT count(*) FROM \"{_PROBE}\" WHERE tenant_id = '{person}'",
            fetch=True,
        )
    )
    assert person_rows.rows == [(0,)], person_rows.rows

    # And the caller's real organization does not reach it either: the row belongs to
    # nobody, so it stays invisible until something attributes it.
    alpha = await _names(two_orgs, "a2")
    as_alpha = _restricted(await rls.as_app_role(two_orgs.engine, alpha, _COUNT, fetch=True))
    assert as_alpha.rows == [(2,)], (as_alpha.rows, as_alpha.error)


async def test_the_role_the_application_connects_as_cannot_bypass_the_policy(two_orgs) -> None:  # noqa: ANN001
    """The catalog attributes behind every count above, and the documented escape."""
    async with two_orgs.engine.connect() as conn:
        app_role = await rls.role_attributes(conn, APP_ROLE)
        system_role = await rls.role_attributes(conn, SYSTEM_ROLE)
        policy = await rls.installed_policy(conn, _PROBE)

    assert app_role["exists"] and app_role["rolsuper"] is False and app_role["rolbypassrls"] is False, app_role
    assert system_role["exists"] and system_role["rolbypassrls"] is True, system_role
    assert policy["installed"] and policy["rls_enabled"] and policy["forced"], policy
    assert policy["is_strict"] is True, policy["qual"]

    # The system role sees both companies, which is the difference between "policied"
    # and "deliberately not", and the reason trusted background work has a role of its
    # own rather than borrowing the request role.
    bypass = await rls.as_app_role(two_orgs.engine, two_orgs.alpha, _COUNT, fetch=True, role=SYSTEM_ROLE)
    assert bypass.current_user == SYSTEM_ROLE
    assert bypass.rows == [(4,)], (bypass.rows, bypass.error)


async def test_the_bound_tenant_is_what_the_session_carries_into_its_transaction(two_orgs) -> None:  # noqa: ANN001
    """The mechanism, at the level a run actually meets it.

    Three states of the same context variable are three different facts: a company id
    for a bound run, the empty string for one bound to nothing (which a strict policy
    matches no row against), and SQL NULL for a session that was never bound at all.
    """
    alpha = two_orgs.alpha
    async with two_orgs.factory() as session, rls_module.bound_tenant(alpha):
        identity = (await session.execute(text(_IDENTITY))).one()
        bound_to = (await session.execute(text(f"SELECT current_setting('{GUC_NAME}', true)"))).scalar_one()
        visible = (await session.execute(text(_COUNT))).scalar_one()
    assert str(identity[0]) == APP_ROLE, identity
    assert bool(identity[1]) is False, identity
    assert str(bound_to) == alpha, bound_to
    assert int(visible) == 2, visible

    async with two_orgs.factory() as session, rls_module.bound_tenant(None):
        identity = (await session.execute(text(_IDENTITY))).one()
        bound_to = (await session.execute(text(f"SELECT current_setting('{GUC_NAME}', true)"))).scalar_one()
        visible = (await session.execute(text(_COUNT))).scalar_one()
    assert str(identity[0]) == APP_ROLE, identity
    assert bool(identity[1]) is False, identity
    assert str(bound_to) == "", bound_to
    assert int(visible) == 0, visible
