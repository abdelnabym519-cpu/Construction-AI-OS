"""Open receivables and payables carried over from another system, through the API.

A company switching over brings what customers still owe it and what it still
owes its suppliers. The preview must match each row to a contact by tax number
or name and hand back control totals without writing. One bad row must keep
the whole file out. A clean import must leave the ageing showing exactly the
open amounts and the retention, and a second run of the same file must carry
nothing over twice.

Run:
    cd backend
    python -m pytest tests/integration/test_finance_open_items_import_api.py -v
"""

import asyncio
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app

pytestmark = __import__("pytest").mark.asyncio

URL = "/api/v1/finance/open-items/import/"
CUTOVER = "2026-09-30"


@pytest_asyncio.fixture(scope="module")
async def client():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


async def _login(client: AsyncClient) -> dict[str, str]:
    unique = uuid.uuid4().hex[:8]
    email, password = f"openitems-{unique}@test.io", f"Opening{unique}9"
    reg = await client.post(
        "/api/v1/users/auth/register", json={"email": email, "password": password, "full_name": "Opening"}
    )
    assert reg.status_code == 201, reg.text
    from ._auth_helpers import promote_to_admin

    await promote_to_admin(email)
    data: dict = {}
    for attempt in range(3):
        data = (await client.post("/api/v1/users/auth/login", json={"email": email, "password": password})).json()
        if data.get("access_token"):
            return {"Authorization": f"Bearer {data['access_token']}"}
        await asyncio.sleep(5 * (attempt + 1))
    raise AssertionError(f"login failed: {data}")


async def _ok(resp, code: int = 200):
    assert resp.status_code == code, resp.text
    return resp.json()


@pytest_asyncio.fixture(scope="module")
async def admin(client: AsyncClient) -> dict[str, str]:
    return await _login(client)


@pytest_asyncio.fixture(scope="module")
async def world(client: AsyncClient, admin: dict[str, str]) -> dict[str, str]:
    tag = uuid.uuid4().hex[:6].upper()
    ids: dict[str, str] = {"tag": tag}
    project = await _ok(
        await client.post("/api/v1/projects/", json={"name": f"Cut-over {tag}", "currency": "CAD"}, headers=admin),
        201,
    )
    ids["project"] = project["id"]
    for key, name, extra in (
        ("steel", f"Lakeshore Steel {tag}", {"vat_number": f"98765{tag}RT0001"}),
        ("homes", f"Lakeview Homes {tag}", {}),
        ("twin_a", f"Twin Trades {tag}", {}),
        ("twin_b", f"Twin Trades {tag}", {}),
    ):
        contact = await _ok(
            await client.post(
                "/api/v1/contacts/",
                json={
                    "contact_type": "subcontractor",
                    "company_name": name,
                    "primary_email": f"{key}-{tag.lower()}@test.io",
                    "country_code": "CA",
                    **extra,
                },
                headers=admin,
            ),
            201,
        )
        ids[key] = contact["id"]
    return ids


def _csv(world: dict[str, str], *rows: str) -> bytes:
    header = "direction,invoice_number,counterparty,tax_id,invoice_date,due_date,currency,total,tax,retention_held,open"
    return ("\n".join((header, *rows)) + "\n").encode("utf-8")


def _good_rows(world: dict[str, str]) -> tuple[str, ...]:
    tag = world["tag"]
    return (
        # A customer paid part of a progress claim; 1000 is held back as retention.
        f"AR,INV-{tag}-1,lakeview homes {tag},,2026-08-15,2026-09-14,CAD,11300.00,1300.00,1000.00,6300.00",
        # A supplier matched by tax number though the name is spelled differently.
        f"AP,LS-{tag}-1,Lakeshore Steel Ltd,98765 {tag} RT0001,2026-09-02,2026-10-02,CAD,8475.00,975.00,0,",
        # The same supplier by name, half paid.
        f"payable,LS-{tag}-2,Lakeshore Steel {tag},,2026-07-01,2026-07-31,,2000,0,0,1000",
    )


async def _post(client: AsyncClient, admin, world, body: bytes, *, dry_run: bool, cutover: str = CUTOVER):
    return await client.post(
        URL,
        params={"project_id": world["project"], "cutover_date": cutover, "dry_run": str(dry_run).lower()},
        files={"file": ("open_items.csv", body, "text/csv")},
        headers=admin,
    )


async def _invoices(client: AsyncClient, admin, world) -> list[dict]:
    data = await _ok(await client.get("/api/v1/finance/", params={"project_id": world["project"]}, headers=admin))
    return data["items"]


async def test_the_preview_matches_contacts_and_totals_without_writing(client, admin, world):
    data = await _ok(await _post(client, admin, world, _csv(world, *_good_rows(world)), dry_run=True))
    assert (data["ready"], data["errors"], data["imported"]) == (3, 0, 0)
    by_number = {r["invoice_number"]: r for r in data["rows"]}
    tag = world["tag"]
    assert by_number[f"INV-{tag}-1"]["contact_id"] == world["homes"]
    assert by_number[f"INV-{tag}-1"]["paid"] == "4000.00"
    assert by_number[f"LS-{tag}-1"]["contact_id"] == world["steel"]
    assert by_number[f"LS-{tag}-1"]["open"] == "8475.00"
    assert by_number[f"LS-{tag}-2"]["currency_code"] == "CAD"
    totals = {(t["direction"], t["currency_code"]): t for t in data["control_totals"]}
    assert totals[("payable", "CAD")]["count"] == 2
    assert totals[("payable", "CAD")]["open"] == "9475.00"
    assert totals[("receivable", "CAD")]["retention_held"] == "1000.00"
    assert await _invoices(client, admin, world) == []


async def _colleague(client: AsyncClient, admin: dict[str, str]) -> tuple[dict[str, str], str]:
    """A non-admin member of the admin's own company, and that company's id.

    Registered rather than seeded, then belonged to the admin's organization
    directly: a registration on its own would put the newcomer in a company of
    their own, which is the right answer for an install with several companies and
    no use at all here, where the point is somebody who sees the same directory
    without being allowed to see every one.
    """
    from jose import jwt
    from sqlalchemy import select, update

    from app.database import async_session_factory
    from app.modules.users.models import Membership, User

    tenant = jwt.get_unverified_claims(admin["Authorization"].removeprefix("Bearer "))["tenant_id"]
    unique = uuid.uuid4().hex[:8]
    email, password = f"colleague-{unique}@test.io", f"Colleague{unique}9"
    reg = await client.post(
        "/api/v1/users/auth/register", json={"email": email, "password": password, "full_name": "Colleague"}
    )
    assert reg.status_code == 201, reg.text
    async with async_session_factory() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
        # ``finance.create`` is an editor permission. Whatever registration
        # assigned is not what this test is about and must not decide whether it
        # can run.
        await s.execute(update(User).where(User.id == user.id).values(role="editor"))
        s.add(
            Membership(
                user_id=user.id,
                organization_id=uuid.UUID(tenant),
                role="editor",
                status="active",
                is_default=True,
            )
        )
        await s.commit()
    data = (await client.post("/api/v1/users/auth/login", json={"email": email, "password": password})).json()
    assert data.get("access_token"), data
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    claims = jwt.get_unverified_claims(data["access_token"])
    assert claims["tenant_id"] == tenant, "the colleague signed in to a company that is not the admin's"
    return headers, tenant


async def _stranger(client: AsyncClient) -> tuple[dict[str, str], str]:
    """A caller in a company of their own, which is what makes them a stranger."""
    from jose import jwt
    from sqlalchemy import func, select

    from app.database import async_session_factory
    from app.modules.users.models import Organization

    # Two companies have to exist before this registration. With one, the newcomer
    # joins it, which is correct for a real single-company install and would make
    # this fixture no stranger to anybody.
    async with async_session_factory() as s:
        if int(await s.scalar(select(func.count()).select_from(Organization)) or 0) < 2:
            s.add(Organization(name="Another company", slug=f"another-{uuid.uuid4().hex[:8]}", status="active"))
            await s.commit()
    unique = uuid.uuid4().hex[:8]
    email, password = f"stranger-{unique}@test.io", f"Stranger{unique}9"
    reg = await client.post(
        "/api/v1/users/auth/register", json={"email": email, "password": password, "full_name": "Stranger"}
    )
    assert reg.status_code == 201, reg.text
    data: dict = {}
    for attempt in range(3):
        data = (await client.post("/api/v1/users/auth/login", json={"email": email, "password": password})).json()
        if data.get("access_token"):
            headers = {"Authorization": f"Bearer {data['access_token']}"}
            return headers, jwt.get_unverified_claims(data["access_token"])["tenant_id"]
        await asyncio.sleep(5 * (attempt + 1))
    raise AssertionError(f"login failed: {data}")


async def test_a_colleague_matches_their_own_companys_contacts(client, admin, world):
    """The import matches the contacts of the caller's company, not of the caller.

    ``import_open_items`` was handed the caller's user id as its tenant and filtered
    ``Contact.tenant_id`` with it. That was coherent while a contact's tenant was
    whoever wrote it, and it stopped being coherent when the ADR 005 backfill moved
    every stored tenant onto an organization: the filter then matched no contact at
    all, so a cut-over file would have come back with every row unmatched and an
    operator would have been told to retype a directory the system already held.

    The admin tests above cannot see any of this, because an admin is deliberately
    unfiltered - which is why this one is a colleague, and why it also checks the
    other direction: a contact belonging to a different company is not a candidate
    however closely it is named.
    """
    headers, tenant = await _colleague(client, admin)
    tag = world["tag"]

    # Their own project, not the admin's: ``_require_project_access`` answers 404
    # for a project the caller neither created nor administers, and that check is
    # about people and is not what this test is about. What it is about is that the
    # contact directory is the company's, so a colleague working in a project of
    # their own still matches the contacts the admin's cut-over created.
    own = await _ok(
        await client.post(
            "/api/v1/projects/", json={"name": f"Colleague cut-over {tag}", "currency": "CAD"}, headers=headers
        ),
        201,
    )

    async def _preview(*rows: str) -> dict:
        return await _ok(
            await client.post(
                URL,
                params={"project_id": own["id"], "cutover_date": CUTOVER, "dry_run": "true"},
                files={"file": ("open_items.csv", _csv(world, *rows), "text/csv")},
                headers=headers,
            )
        )

    data = await _preview(*_good_rows(world))
    assert (data["ready"], data["errors"]) == (3, 0), data["rows"]
    by_number = {r["invoice_number"]: r for r in data["rows"]}
    assert by_number[f"INV-{tag}-1"]["contact_id"] == world["homes"]
    assert by_number[f"LS-{tag}-1"]["contact_id"] == world["steel"]

    # The same directory from another company must not be reachable. The foreign
    # row is written directly rather than through the API: what is under test is
    # which rows the import offers as candidates, and a registration's default role
    # has no ``contacts.create`` permission to grant one. The organization it
    # belongs to is a real one, minted by a real registration.
    from app.database import async_session_factory
    from app.modules.contacts.models import Contact

    _, stranger_tenant = await _stranger(client)
    assert stranger_tenant != tenant, "the stranger is in the colleague's company after all"
    foreign_name = f"Foreign Twin {tag}"
    async with async_session_factory() as session:
        foreign = Contact(
            contact_type="client",
            company_name=foreign_name,
            tenant_id=stranger_tenant,
            # A person, and a different one from the caller: ``created_by`` has
            # never held an organization, and the row must not match this caller on
            # the legacy arm of the scope filter either.
            created_by=str(uuid.uuid4()),
        )
        session.add(foreign)
        await session.commit()
        foreign_id = str(foreign.id)
    foreign_row = f"AR,INV-{tag}-9,{foreign_name},,2026-08-15,2026-09-14,CAD,1000.00,0,0,1000.00"
    refused = await _preview(foreign_row)
    assert refused["rows"][0].get("contact_id") != foreign_id, (
        "a contact belonging to another company was offered as a match for this caller's import"
    )


async def test_one_bad_row_keeps_the_whole_file_out(client, admin, world):
    tag = world["tag"]
    rows = (
        *_good_rows(world),
        f"AP,TW-{tag}-1,Twin Trades {tag},,2026-09-01,,CAD,500,0,0,",
        f"AP,NO-{tag}-1,Nobody {tag},,2026-09-01,,CAD,500,0,0,",
        f"AR,LATE-{tag}-1,Lakeview Homes {tag},,2026-10-05,,CAD,500,0,0,",
    )
    data = await _ok(await _post(client, admin, world, _csv(world, *rows), dry_run=False))
    assert (data["imported"], data["errors"]) == (0, 3)
    messages = {r["invoice_number"]: r["message"] for r in data["rows"] if r["status"] == "error"}
    assert "Several contacts" in messages[f"TW-{tag}-1"]
    assert "No contact named" in messages[f"NO-{tag}-1"]
    assert "after the cut-over" in messages[f"LATE-{tag}-1"]
    codes = {r["invoice_number"]: r["code"] for r in data["rows"] if r["status"] == "error"}
    assert codes == {
        f"TW-{tag}-1": "contact_name_ambiguous",
        f"NO-{tag}-1": "contact_name_unknown",
        f"LATE-{tag}-1": "after_cutover",
    }
    assert await _invoices(client, admin, world) == []


async def test_the_import_leaves_the_ageing_with_the_open_amounts(client, admin, world):
    data = await _ok(await _post(client, admin, world, _csv(world, *_good_rows(world)), dry_run=False))
    assert (data["imported"], data["errors"]) == (3, 0)
    invoices = await _invoices(client, admin, world)
    assert {i["status"] for i in invoices if i["invoice_direction"] == "payable"} == {"approved"}
    assert {i["status"] for i in invoices if i["invoice_direction"] == "receivable"} == {"sent"}

    ageing = await _ok(
        await client.get(
            "/api/v1/finance/ageing/",
            params={"project_id": world["project"], "as_of": CUTOVER},
            headers=admin,
        )
    )
    groups = {(g["direction"], g["contact_id"]): g for g in ageing["groups"]}
    homes = groups[("receivable", world["homes"])]
    assert (homes["total_due"], homes["retention_held"]) == ("6300.00", "1000.00")
    steel = groups[("payable", world["steel"])]
    assert (steel["total_due"], steel["invoice_count"]) == ("9475.00", 2)


async def test_a_second_run_carries_nothing_over_twice(client, admin, world):
    data = await _ok(await _post(client, admin, world, _csv(world, *_good_rows(world)), dry_run=False))
    assert (data["imported"], data["exists"], data["errors"]) == (0, 3, 0)
    assert len(await _invoices(client, admin, world)) == 3


async def test_a_number_already_in_use_is_refused(client, admin, world):
    tag = world["tag"]
    await _ok(
        await client.post(
            "/api/v1/finance/",
            json={
                "project_id": world["project"],
                "contact_id": world["steel"],
                "invoice_direction": "payable",
                "invoice_number": f"LIVE-{tag}",
                "invoice_date": "2026-09-10",
                "currency_code": "CAD",
                "amount_subtotal": "100",
            },
            headers=admin,
        ),
        201,
    )
    row = f"AP,live-{tag.lower()},Lakeshore Steel {tag},,2026-09-10,,CAD,100,0,0,"
    data = await _ok(await _post(client, admin, world, _csv(world, row), dry_run=True))
    assert data["rows"][0]["status"] == "error"
    assert "already used" in data["rows"][0]["message"]


async def test_the_file_and_the_cutover_are_checked(client, admin, world):
    data = await _ok(
        await _post(client, admin, world, b"invoice_number,total\nX-1,100\n", dry_run=True),
    )
    assert set(data["missing_columns"]) == {"direction", "counterparty", "invoice_date"}
    resp = await _post(client, admin, world, _csv(world, *_good_rows(world)), dry_run=True, cutover="2999-01-01")
    assert resp.status_code == 422, resp.text
    template = await client.get("/api/v1/finance/open-items/template/", headers=admin)
    assert template.status_code == 200
    assert template.content.decode("utf-8-sig").startswith("direction,invoice_number,counterparty")
