"""Keyset (cursor) pagination of the contacts list.

Run:
    cd backend
    python -m pytest -q -x --no-header tests/modules/contacts/test_cursor_pagination.py
"""

import asyncio
import base64
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import async_session_factory
from app.main import create_app
from app.modules.contacts.models import Contact

pytestmark = pytest.mark.asyncio

URL = "/api/v1/contacts/"


@pytest_asyncio.fixture(scope="module")
async def client():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


async def _ensure_the_install_has_two_companies() -> None:
    """Make the install multi-company, so each caller below gets a company of their own.

    Not a workaround, and the behaviour it depends on is the one ADR 005 specifies:
    a registrant joins the install's organization when there is one, because there
    is nothing to choose between and splitting a real one-company install into
    tenants that cannot see each other's contacts would be wrong. When there are
    several, which company a newcomer belongs to is a fact nobody has recorded, so
    they get an empty one of their own.

    These tests count rows. While a contact's tenant was its author, every test got
    a directory of its own for free; with the tenant being the organization, tests
    that share one share a directory, and ``total == 3`` measures whatever the
    previous test left behind. Giving each caller their own company restores the
    isolation the assertions assume, without weakening any of them.
    """
    from sqlalchemy import func, select

    from app.modules.users.models import Organization

    async with async_session_factory() as session:
        if int(await session.scalar(select(func.count()).select_from(Organization)) or 0) < 2:
            session.add(Organization(name="Second company", slug=f"second-{uuid.uuid4().hex[:8]}", status="active"))
            await session.commit()


async def _login(client: AsyncClient, *, admin: bool = False) -> tuple[dict[str, str], str]:
    """Sign in and return the headers plus the organization the token names.

    The second value used to be the caller's user id, because a contact row's
    ``tenant_id`` held the id of whoever wrote it. ADR 005 makes the tenant the
    organization, so the value rows are tagged with has to be the one the request
    will be scoped by. It is read off the issued token rather than looked up or
    invented, so this helper cannot drift from what the door hands out - and a
    registration that stopped minting the claim would fail here instead of quietly
    seeding rows nobody can see.
    """
    unique = uuid.uuid4().hex[:8]
    email, password = f"cur-{unique}@test.io", f"Cursor{unique}9"
    reg = await client.post(
        "/api/v1/users/auth/register", json={"email": email, "password": password, "full_name": "C"}
    )
    assert reg.status_code == 201, reg.text
    await _ensure_the_install_has_two_companies()
    from tests.integration._auth_helpers import promote_to_admin

    await promote_to_admin(email)
    if not admin:
        from sqlalchemy import update

        from app.modules.users.models import User

        async with async_session_factory() as s:
            await s.execute(update(User).where(User.email == email).values(role="editor"))
            await s.commit()
    data: dict = {}
    for attempt in range(3):
        data = (await client.post("/api/v1/users/auth/login", json={"email": email, "password": password})).json()
        if data.get("access_token"):
            from jose import jwt

            headers = {"Authorization": f"Bearer {data['access_token']}"}
            claims = jwt.get_unverified_claims(data["access_token"])
            assert "tenant_id" in claims, f"login minted a token with no tenant claim: {sorted(claims)}"
            return headers, claims["tenant_id"]
        await asyncio.sleep(5 * (attempt + 1))
    raise AssertionError(f"login failed: {data}")


async def _insert(tenant: str, names: list[str], created_by: str | None = None) -> list[str]:
    ids = []
    async with async_session_factory() as session:
        for n in names:
            c = Contact(
                contact_type="client",
                company_name=n,
                tenant_id=tenant,
                # ``created_by`` names a person and ``tenant_id`` an organization.
                # They were the same value while a tenant was a user, and seeding
                # them equal here would hide which of the two the scope filter is
                # actually matching on.
                created_by=created_by or str(uuid.uuid4()),
            )
            session.add(c)
            await session.flush()
            ids.append(str(c.id))
        await session.commit()
    return ids


async def _walk(client, headers, limit: int, hook=None) -> list[str]:
    seen: list[str] = []
    cursor = ""
    page = 0
    while True:
        r = await client.get(URL, params={"cursor": cursor, "limit": limit}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        page += 1
        if hook:
            await hook(page)
        if cursor is None:
            return seen


async def test_cursor_visits_each_row_once_with_inserts_between_pages(client):
    headers, tenant = await _login(client)
    # Mixed case and repeated names exercise the id tiebreak.
    names = [f"{'Aa'[i % 2]}contact {i % 400:04d}" for i in range(1000)]
    original = set(await _insert(tenant, names))
    late: set[str] = set()

    async def hook(page: int) -> None:
        if page in (2, 5):
            late.update(await _insert(tenant, [f"zz late {page}-{k}" for k in range(3)]))

    seen = await _walk(client, headers, 100, hook)
    assert len(seen) == len(set(seen)), "a row was returned twice"
    assert original <= set(seen), "an existing row was skipped"
    assert set(seen) <= original | late


async def test_offset_callers_unchanged(client):
    headers, tenant = await _login(client)
    await _insert(tenant, ["one", "two", "three"])
    body = (await client.get(URL, params={"limit": 2}, headers=headers)).json()
    assert body["total"] == 3 and len(body["items"]) == 2 and body["next_cursor"] is None


@pytest.mark.parametrize("bad", ["not-base64!!", base64.urlsafe_b64encode(b"[1,2]").decode(), "e30", "W10"])
async def test_invalid_cursor_is_422(client, bad):
    headers, _ = await _login(client)
    r = await client.get(URL, params={"cursor": bad}, headers=headers)
    assert r.status_code == 422, r.text


async def test_cursor_does_not_cross_tenants(client):
    # Two registrations in an install that has more than one company, so each
    # caller is the sole member of their own. ``_login`` arranges the install; the
    # assertion below is what the test is about, and it would catch a mint that put
    # both newcomers in the same company.
    a_headers, a_tenant = await _login(client)
    b_headers, b_tenant = await _login(client)
    assert a_tenant != b_tenant, "two registrations in a two-organization install landed in one organization"
    await _insert(a_tenant, [f"a-{i:03d}" for i in range(30)])
    b_rows = set(await _insert(b_tenant, [f"b-{i:03d}" for i in range(30)]))

    first = await client.get(URL, params={"cursor": "", "limit": 10}, headers=a_headers)
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    assert cursor
    as_b = await client.get(URL, params={"cursor": cursor, "limit": 100}, headers=b_headers)
    assert {i["id"] for i in as_b.json()["items"]} <= b_rows
    assert not (set(await _walk(client, a_headers, 10)) & b_rows)
