"""Who may create a tenant, and what creating one does to the people in it.

An organization is the tenant (ADR 005). Before
:mod:`app.modules.admin.organizations_router` existed nothing in the product
could create a second one: the only two places that constructed an
``Organization`` were both inside the login path, one running only when the
install had none and the other only when it already had more than one, so an
install booted into exactly one organization and stayed there. Project
participant grants (ADR 006) invite *another organization* into a project, and
there was no other organization to invite - which is what made the grant graph
impossible to exercise end to end from a browser.

These tests are the ones the owner asked for before the surface was built, and
each one is a question about an identity rather than about a function:

  * who is refused - a viewer, an administrator *of one organization*, and a
    caller with no credential at all;
  * who is let in - the installation administrator, and only on the three routes
    that exist;
  * what a create does to a name - sanitized, unique, and refused when there is
    nothing visible in it or it will not fit the column;
  * what an add does to a person - a second membership and never a move, so the
    first organization's rows stay theirs;
  * what a person whose only membership is the second organization can see of
    the first one's project - nothing, on both the list and the grant route;
  * what the ledger says afterwards - one row per event, readable over HTTP,
    naming the actor, the organization and the person;
  * and what this surface deliberately does not do: there is no delete and no
    rename, because each is a decision with its own evidence to gather and
    neither is needed to make a second tenant exist.

Marked ``tenant_isolation`` because identity is the variable under test, which
is what the marker is for and what the lane that runs it selects on: a new
boundary test earns the marker rather than hoping a frozen keyword chain happens
to spell its name.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app

pytestmark = pytest.mark.tenant_isolation

ORGANIZATIONS = "/api/v1/admin/organizations"

# What the service writes, read back out of the audit route rather than out of
# the table: a row no HTTP surface can read is a row no administrator can read.
ACTION_CREATED = "created"
ACTION_MEMBER_ADDED = "member_added"
ORGANIZATION_ENTITY_TYPE = "organization"
MEMBERSHIP_ENTITY_TYPE = "organization_membership"


# --- Fixtures and helpers ---------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def orgadm_client():
    """The whole product, over ASGI, for the length of this module."""
    app = create_app()

    @asynccontextmanager
    async def lifespan_ctx():
        async with app.router.lifespan_context(app):
            yield

    async with lifespan_ctx():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _login(client: AsyncClient, email: str, password: str) -> str:
    """One token, retrying only past the rate limit a shared test database sets."""
    token = ""
    for attempt in range(3):
        resp = await client.post("/api/v1/users/auth/login", json={"email": email, "password": password})
        data = resp.json()
        token = data.get("access_token", "")
        if token:
            break
        if "Too many login attempts" in (data.get("detail") or ""):
            await asyncio.sleep(5 * (attempt + 1))
            continue
        break
    return token


async def _registered(client: AsyncClient, *, tag: str, role: str = "viewer", promote: bool = False) -> tuple[str, str]:
    """Register one person, make sure they can sign in, and return their credentials.

    Promotion and activation are direct database writes, as every other
    integration test in this tree does it: the public registration endpoint
    demotes everybody to ``viewer`` on purpose (BUG-327/386) and, in
    ``admin-approve`` mode, leaves them inactive (BUG-RBAC03), so a fixture that
    needs an administrator or a working viewer cannot ask the HTTP surface for
    one.
    """
    from tests.integration._auth_helpers import activate_user, promote_to_admin

    unique = uuid.uuid4().hex[:8]
    email = f"orgadm-{tag}-{unique}@test.io"
    password = f"OrgAdm{unique}9"
    resp = await client.post(
        "/api/v1/users/auth/register",
        json={"email": email, "password": password, "full_name": f"Org Adm {tag}", "role": role},
    )
    assert resp.status_code == 201, f"registration failed: {resp.text}"
    if promote:
        await promote_to_admin(email)
    else:
        await activate_user(email)
    return email, password


@pytest_asyncio.fixture(scope="module")
async def installation_admin(orgadm_client: AsyncClient) -> dict[str, object]:
    """The installation administrator: ``oe_users_user.role`` is ``admin``."""
    email, password = await _registered(orgadm_client, tag="root", promote=True)
    token = await _login(orgadm_client, email, password)
    assert token, "the installation administrator could not sign in"
    me = await orgadm_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    return {"headers": {"Authorization": f"Bearer {token}"}, "email": email, "id": me.json()["id"], "token": token}


async def _created_by_admin(
    client: AsyncClient, admin: dict[str, object], *, tag: str, role: str = "viewer"
) -> tuple[str, str, str]:
    """One person created by the administrator: their email, their password, their id.

    The password is returned rather than derived from the email by the caller,
    because a helper that makes its caller reconstruct a credential from a string
    it also generated is a helper that will be reconstructed wrongly the moment
    the format moves.
    """
    from tests.integration._auth_helpers import activate_user

    unique = uuid.uuid4().hex[:8]
    email = f"orgadm-{tag}-{unique}@test.io"
    password = f"OrgAdm{unique}9"
    resp = await client.post(
        "/api/v1/users/",
        json={"email": email, "password": password, "full_name": f"Org Adm {tag}", "role": role},
        headers=admin["headers"],  # type: ignore[arg-type]
    )
    assert resp.status_code == 201, f"administrator could not create a user: {resp.text}"
    await activate_user(email)
    return email, password, resp.json()["id"]


async def _memberships(email: str) -> list[dict[str, object]]:
    """Every membership a person has, read out of the database.

    Read here rather than over HTTP because no route exposes them: ``/users/me``
    answers with the person's own fields and no organization among them, so a
    test that asked HTTP which company a person belongs to would be asserting
    nothing at all. Which rows exist, and which one is flagged as the default a
    token names, is a fact about the table.
    """
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.modules.users.models import Membership, User

    async with async_session_factory() as session:
        rows = await session.execute(
            select(Membership.organization_id, Membership.role, Membership.status, Membership.is_default)
            .join(User, User.id == Membership.user_id)
            .where(User.email == email.lower())
            .order_by(Membership.created_at, Membership.id)
        )
        return [
            {
                "organization_id": str(organization_id),
                "role": role,
                "status": status_,
                "is_default": bool(is_default),
            }
            for organization_id, role, status_, is_default in rows
        ]


async def _create_organization(
    client: AsyncClient, admin: dict[str, object], name: str, *, slug: str | None = None
) -> dict[str, object]:
    """Create one organization and assert the create answered 201."""
    body: dict[str, object] = {"name": name}
    if slug is not None:
        body["slug"] = slug
    resp = await client.post(f"{ORGANIZATIONS}/", json=body, headers=admin["headers"])  # type: ignore[arg-type]
    assert resp.status_code == 201, f"creating {name!r} answered {resp.status_code}: {resp.text}"
    return resp.json()


# --- Who is refused ---------------------------------------------------------


class TestWhoMayNotAdministerOrganizations:
    """Three identities that are not the installation administrator."""

    async def test_a_viewer_is_refused_on_all_three_routes(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        email, password = await _registered(orgadm_client, tag="viewer")
        token = await _login(orgadm_client, email, password)
        assert token, "the viewer could not sign in"
        headers = {"Authorization": f"Bearer {token}"}

        created = await _create_organization(orgadm_client, installation_admin, "Viewer Refusal Ltd")
        placeholder = str(uuid.uuid4())

        for method, url, body in (
            ("post", f"{ORGANIZATIONS}/", {"name": "A viewer should not be able to make this"}),
            ("get", f"{ORGANIZATIONS}/", None),
            ("post", f"{ORGANIZATIONS}/{created['id']}/members/", {"user_email": email}),
            ("post", f"{ORGANIZATIONS}/{placeholder}/members/", {"user_email": email}),
        ):
            resp = await getattr(orgadm_client, method)(url, json=body, headers=headers)
            assert resp.status_code == 403, (
                f"{method.upper()} {url} answered {resp.status_code} to a viewer: {resp.text}. The route is "
                "installation-administrator-only, and a viewer is the identity furthest from one."
            )

    async def test_an_administrator_of_one_organization_is_refused(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        """A membership role of ``admin`` is not an installation role of ``admin``.

        The membership model says why the two are different columns: "an admin of
        one company is not an admin of another, and a ``role`` column on ``User``
        would say they were". This person holds ``admin`` inside their own
        organization and ``editor`` on the installation, and the routes read the
        second one. Were it the other way round, every company's administrator
        would be able to list every other company on the installation, which in a
        multi-tenant install is a list of the other customers.
        """
        email, password, _user_id = await _created_by_admin(
            orgadm_client, installation_admin, tag="orgadmin", role="editor"
        )
        company = await _create_organization(orgadm_client, installation_admin, "Org Admin Own Company Ltd")

        added = await orgadm_client.post(
            f"{ORGANIZATIONS}/{company['id']}/members/",
            json={"user_email": email, "role": "admin", "is_default": True},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert added.status_code == 201, added.text
        assert added.json()["role"] == "admin", added.text

        token = await _login(orgadm_client, email, f"OrgAdm{password}9")
        assert token, f"the organization administrator could not sign in as {email}"
        headers = {"Authorization": f"Bearer {token}"}

        for method, url, body in (
            ("post", f"{ORGANIZATIONS}/", {"name": "A second company nobody outside should be able to make"}),
            ("get", f"{ORGANIZATIONS}/", None),
        ):
            resp = await getattr(orgadm_client, method)(url, json=body, headers=headers)
            assert resp.status_code == 403, (
                f"{method.upper()} {url} answered {resp.status_code} to an administrator of one organization: "
                f"{resp.text}. Per-organization authority must not reach installation-level routes."
            )

    async def test_a_caller_with_no_credential_is_refused(self, orgadm_client: AsyncClient) -> None:
        created = str(uuid.uuid4())
        for method, url, body in (
            ("post", f"{ORGANIZATIONS}/", {"name": "Anonymous Ltd"}),
            ("get", f"{ORGANIZATIONS}/", None),
            ("post", f"{ORGANIZATIONS}/{created}/members/", {"user_email": "nobody@example.com"}),
        ):
            resp = await getattr(orgadm_client, method)(url, json=body)
            assert resp.status_code in (401, 403), (
                f"{method.upper()} {url} answered {resp.status_code} to a caller with no credential: {resp.text}"
            )


# --- What creating one does -------------------------------------------------


class TestCreatingAnOrganization:
    """The name is sanitized, unique, and refused when it is nothing or too long."""

    async def test_the_installation_administrator_creates_one_and_the_list_carries_it(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        name = f"Kerensky Contractors {uuid.uuid4().hex[:6]}"
        created = await _create_organization(orgadm_client, installation_admin, name)
        assert created["name"] == name
        assert created["slug"] == name.lower().replace(" ", "-"), created
        assert created["status"] == "active", created

        listed = await orgadm_client.get(f"{ORGANIZATIONS}/", headers=installation_admin["headers"])  # type: ignore[arg-type]
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["total"] == len(body["items"]), body
        assert created["id"] in [item["id"] for item in body["items"]], (
            "the organization just created is not in the list the same administrator reads"
        )

    async def test_the_name_is_sanitized_before_it_is_stored(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        unique = uuid.uuid4().hex[:6]
        created = await _create_organization(orgadm_client, installation_admin, f"  Nord {unique}\u200b   Bygg\ufeff  ")
        assert created["name"] == f"Nord {unique} Bygg", created
        assert "\u200b" not in created["name"] and "\ufeff" not in created["name"], created
        assert created["slug"] == f"nord-{unique}-bygg", created

    async def test_a_second_organization_with_the_same_name_is_refused(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        name = f"Duplicate Check {uuid.uuid4().hex[:6]}"
        await _create_organization(orgadm_client, installation_admin, name)
        again = await orgadm_client.post(
            f"{ORGANIZATIONS}/",
            json={"name": name},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert again.status_code == 409, again.text

    async def test_a_name_that_differs_only_by_case_or_an_invisible_character_is_the_same_name(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        """Two names a person cannot tell apart are one name to the uniqueness check.

        Invisible characters are dropped rather than rejected because a duplicate
        nobody can see is still a duplicate, and a check that compared the raw
        strings would answer 201 to both and leave the installation with two
        companies that read identically in every list.
        """
        unique = uuid.uuid4().hex[:6]
        await _create_organization(orgadm_client, installation_admin, f"Invisible {unique} Ltd")

        for variant in (f"invisible {unique} ltd", f"Invisible\u200b {unique} Ltd", f"  INVISIBLE {unique} LTD  "):
            resp = await orgadm_client.post(
                f"{ORGANIZATIONS}/",
                json={"name": variant},
                headers=installation_admin["headers"],  # type: ignore[arg-type]
            )
            assert resp.status_code == 409, f"{variant!r} answered {resp.status_code}: {resp.text}"

    async def test_a_name_with_nothing_visible_in_it_is_refused(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        for empty in ("", "   ", "\u200b\ufeff", "\t\n"):
            resp = await orgadm_client.post(
                f"{ORGANIZATIONS}/",
                json={"name": empty},
                headers=installation_admin["headers"],  # type: ignore[arg-type]
            )
            assert resp.status_code == 422, f"name {empty!r} answered {resp.status_code}: {resp.text}"

    async def test_a_name_too_long_for_the_column_is_refused(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        resp = await orgadm_client.post(
            f"{ORGANIZATIONS}/",
            json={"name": "N" * 256},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert resp.status_code == 422, resp.text
        assert "255" in resp.text, f"the refusal does not say what the limit is: {resp.text}"

    async def test_a_supplied_slug_that_is_already_taken_is_refused(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        slug = f"taken-{uuid.uuid4().hex[:8]}"
        await _create_organization(orgadm_client, installation_admin, f"First Of {slug}", slug=slug)
        resp = await orgadm_client.post(
            f"{ORGANIZATIONS}/",
            json={"name": f"Second Of {slug}", "slug": slug},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert resp.status_code == 409, resp.text


# --- What adding a member does ---------------------------------------------


class TestAddingAMember:
    """An addition, never a move - and one audit row saying who did it."""

    async def test_adding_a_member_writes_one_audit_row_naming_the_organization_and_the_person(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        email, password, _user_id = await _created_by_admin(orgadm_client, installation_admin, tag="audited")
        company = await _create_organization(
            orgadm_client, installation_admin, f"Audited Company {uuid.uuid4().hex[:6]}"
        )
        added = await orgadm_client.post(
            f"{ORGANIZATIONS}/{company['id']}/members/",
            json={"user_email": email, "role": "editor"},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert added.status_code == 201, added.text
        membership_id = added.json()["id"]

        for entity_type, entity_id, action, wanted in (
            (ORGANIZATION_ENTITY_TYPE, company["id"], ACTION_CREATED, {"slug": company["slug"]}),
            (
                MEMBERSHIP_ENTITY_TYPE,
                membership_id,
                ACTION_MEMBER_ADDED,
                {"organization_id": str(company["id"]), "user_email": email, "role": "editor"},
            ),
        ):
            resp = await orgadm_client.get(
                f"/api/v1/audit/{entity_type}/{entity_id}",
                headers=installation_admin["headers"],  # type: ignore[arg-type]
            )
            assert resp.status_code == 200, f"the audit trail for {entity_type} {entity_id} answered {resp.status_code}"
            rows = [row for row in resp.json() if row.get("action") == action]
            assert len(rows) == 1, f"expected one {action} row for {entity_type} {entity_id}, read {resp.json()}"
            row = rows[0]
            assert str(row.get("user_id")) == str(installation_admin["id"]), (
                f"the {action} row does not name the administrator who made it: {row}"
            )
            details = row.get("details") or {}
            for key, value in wanted.items():
                assert details.get(key) == value, (
                    f"the {action} row's details say {details.get(key)!r} for {key}: {row}"
                )

    async def test_a_person_added_to_a_second_organization_keeps_the_first_one(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        """Adding a membership is not a move, and does not change which one a token names.

        The person signs in first, which is what gives them a membership of the
        install's own organization and makes it their default. Being added to a
        second one without asking for the default flag must leave both facts
        alone: a person who suddenly found their projects replaced by another
        company's would have been moved by an administrator who only meant to
        invite them somewhere.
        """
        email, password = await _registered(orgadm_client, tag="twocompanies")
        first_token = await _login(orgadm_client, email, password)
        assert first_token, "the person could not sign in before being added to a second organization"
        before = await _memberships(email)
        assert len(before) == 1, f"signing in did not leave exactly one membership: {before}"
        first_organization = before[0]["organization_id"]
        assert before[0]["is_default"] is True, f"the person's only membership was not the default one: {before}"

        second = await _create_organization(orgadm_client, installation_admin, f"Second Company {uuid.uuid4().hex[:6]}")
        added = await orgadm_client.post(
            f"{ORGANIZATIONS}/{second['id']}/members/",
            json={"user_email": email, "is_default": False},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert added.status_code == 201, added.text
        assert added.json()["is_default"] is False, (
            "adding a person to a second organization made it their default without being asked to"
        )

        after = await _memberships(email)
        assert len(after) == 2, (
            f"being added to a second organization left this person with {len(after)} memberships: {after}. "
            "An addition that removes the membership it was beside is a move, and a move changes which "
            "company's rows their next token names."
        )
        kept = [row for row in after if row["organization_id"] == first_organization]
        assert len(kept) == 1, f"the person's first membership is gone: {after}"
        assert kept[0]["is_default"] is True, (
            f"the first membership stopped being the default when a second one was added: {after}, so the next "
            "token this person is issued names a company nobody asked to move them to"
        )
        assert [row for row in after if row["organization_id"] == str(second["id"])][0]["is_default"] is False, after

        # And a token minted after all of it still works, which is the point of
        # leaving the flag alone: the resolution picks the flagged membership, the
        # flag did not move, and the person is not signed out of their own company
        # by an administrator who only meant to invite them to another one.
        second_token = await _login(orgadm_client, email, password)
        assert second_token, "the person could not sign in after being added to a second organization"
        me = await orgadm_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {second_token}"})
        assert me.status_code == 200, me.text

    async def test_the_first_membership_a_person_has_is_the_one_their_token_names(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        email, password, _user_id = await _created_by_admin(orgadm_client, installation_admin, tag="firstmember")
        company = await _create_organization(
            orgadm_client, installation_admin, f"First Membership {uuid.uuid4().hex[:6]}"
        )
        added = await orgadm_client.post(
            f"{ORGANIZATIONS}/{company['id']}/members/",
            json={"user_email": email},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert added.status_code == 201, added.text
        assert added.json()["is_default"] is True, (
            "a person's only membership was not made the default, so a token minted for them has nothing to name"
        )
        assert added.json()["organization_id"] == company["id"], added.text

    async def test_adding_the_same_person_twice_is_refused(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        email, password, _user_id = await _created_by_admin(orgadm_client, installation_admin, tag="twice")
        company = await _create_organization(orgadm_client, installation_admin, f"Twice Company {uuid.uuid4().hex[:6]}")
        url = f"{ORGANIZATIONS}/{company['id']}/members/"
        first = await orgadm_client.post(
            url,
            json={"user_email": email},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert first.status_code == 201, first.text
        second = await orgadm_client.post(
            url,
            json={"user_email": email},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert second.status_code == 409, second.text

    async def test_an_unknown_organization_or_an_unknown_person_is_not_found(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        email, password, _user_id = await _created_by_admin(orgadm_client, installation_admin, tag="unknowns")
        company = await _create_organization(orgadm_client, installation_admin, f"Unknowns {uuid.uuid4().hex[:6]}")

        missing_organization = await orgadm_client.post(
            f"{ORGANIZATIONS}/{uuid.uuid4()}/members/",
            json={"user_email": email},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert missing_organization.status_code == 404, missing_organization.text

        missing_person = await orgadm_client.post(
            f"{ORGANIZATIONS}/{company['id']}/members/",
            json={"user_email": f"nobody-{uuid.uuid4().hex[:8]}@test.io"},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert missing_person.status_code == 404, missing_person.text

    async def test_a_role_this_installation_does_not_know_is_refused(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        email, password, _user_id = await _created_by_admin(orgadm_client, installation_admin, tag="badrole")
        company = await _create_organization(orgadm_client, installation_admin, f"Bad Role {uuid.uuid4().hex[:6]}")
        resp = await orgadm_client.post(
            f"{ORGANIZATIONS}/{company['id']}/members/",
            json={"user_email": email, "role": "supreme-overlord"},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert resp.status_code == 422, resp.text


# --- What the second tenant can and cannot see ------------------------------


class TestWhatTheSecondOrganizationCanSee:
    """The point of a second tenant: its people do not inherit the first one's rows."""

    async def test_a_person_whose_only_membership_is_the_second_organization_cannot_see_the_first_ones_project(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        project = await orgadm_client.post(
            "/api/v1/projects/",
            json={"name": f"First Company Site {uuid.uuid4().hex[:6]}"},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]

        # Created and added before ever signing in, so the second organization is
        # the only one this person has and the one their token names.
        email, password, _user_id = await _created_by_admin(orgadm_client, installation_admin, tag="outsider")
        second = await _create_organization(
            orgadm_client, installation_admin, f"Outside Company {uuid.uuid4().hex[:6]}"
        )
        added = await orgadm_client.post(
            f"{ORGANIZATIONS}/{second['id']}/members/",
            json={"user_email": email},
            headers=installation_admin["headers"],  # type: ignore[arg-type]
        )
        assert added.status_code == 201, added.text

        token = await _login(orgadm_client, email, f"OrgAdm{password}9")
        assert token, f"the person added to the second organization could not sign in as {email}"
        headers = {"Authorization": f"Bearer {token}"}

        listed = await orgadm_client.get("/api/v1/projects/", headers=headers)
        assert listed.status_code == 200, listed.text
        ids = [item["id"] for item in listed.json()]
        assert project_id not in ids, (
            f"a person whose only organization is the second one was shown the first one's project {project_id}"
        )

        grants = await orgadm_client.get(f"/api/v1/projects/{project_id}/participant-grants", headers=headers)
        assert grants.status_code == 404, (
            f"reading the participants of another organization's project answered {grants.status_code}: "
            f"{grants.text}. ADR 006 answers 404 rather than 403 so that the project's existence is not "
            "leaked to a company that was never invited."
        )


# --- What this surface deliberately does not do ----------------------------


class TestWhatThisSurfaceDoesNotDo:
    """No delete and no rename, because neither is needed to make a tenant exist."""

    async def test_there_is_no_route_that_deletes_or_renames_an_organization(
        self, orgadm_client: AsyncClient, installation_admin: dict[str, object]
    ) -> None:
        company = await _create_organization(orgadm_client, installation_admin, f"Stays Put {uuid.uuid4().hex[:6]}")
        url = f"{ORGANIZATIONS}/{company['id']}"

        for method, body in (
            ("delete", None),
            ("patch", {"name": "Renamed By Somebody"}),
            ("put", {"name": "Replaced"}),
        ):
            resp = await getattr(orgadm_client, method)(url, json=body, headers=installation_admin["headers"])
            assert resp.status_code in (404, 405), (
                f"{method.upper()} {url} answered {resp.status_code}: {resp.text}. Deleting an organization would "
                "orphan every tenant_id that names it and renaming one would break the slug two boot paths rely "
                "on, so neither route exists; if one is added it needs its own evidence and its own tests."
            )

        listed = await orgadm_client.get(f"{ORGANIZATIONS}/", headers=installation_admin["headers"])  # type: ignore[arg-type]
        assert listed.status_code == 200, listed.text
        still = [item for item in listed.json()["items"] if item["id"] == company["id"]]
        assert len(still) == 1, "the organization is gone from the list after a refused delete"
        assert still[0]["name"] == company["name"], f"the organization was renamed by a refused patch: {still[0]}"
