"""Integration tests for server-persisted white-label branding (issue #272).

Branding used to live only in the browser's localStorage, so it never followed
the workspace to another browser or to an invited user's pre-auth view of the
login page. These endpoints persist it on the server:

    GET    /api/v1/branding/   - public (login page reads it before sign-in)
    PUT    /api/v1/branding/   - admin only
    DELETE /api/v1/branding/   - admin only

Covers:
    * Test A - GET is public (no auth) and reports the default when nothing is
      set.
    * Test B - an admin sets a brand, and a later (even unauthenticated) GET
      reads it back; the trio is reconciled (a logo wins over text).
    * Test C - a non-admin cannot write or clear the brand (403).
    * Test D - a bad logo payload is sanitised rather than stored verbatim.
    * Test E - an admin can clear the brand back to the default.

The module-scoped client + auth fixtures mirror
``test_bim_upload_converter_preflight.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app

_PNG_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

# --- Module-scoped fixtures -------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def brand_client():
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
    token = ""
    for attempt in range(3):
        resp = await client.post(
            "/api/v1/users/auth/login",
            json={"email": email, "password": password},
        )
        data = resp.json()
        token = data.get("access_token", "")
        if token:
            break
        if "Too many login attempts" in (data.get("detail") or ""):
            await asyncio.sleep(5 * (attempt + 1))
            continue
        break
    return token


@pytest_asyncio.fixture(scope="module")
async def brand_admin(brand_client: AsyncClient) -> dict[str, str]:
    unique = uuid.uuid4().hex[:8]
    email = f"brandadm-{unique}@test.io"
    password = f"BrandAdm{unique}9"
    reg = await brand_client.post(
        "/api/v1/users/auth/register",
        json={"email": email, "password": password, "full_name": "Brand Admin", "role": "admin"},
    )
    assert reg.status_code == 201, f"Registration failed: {reg.text}"

    from tests.integration._auth_helpers import promote_to_admin

    await promote_to_admin(email)
    token = await _login(brand_client, email, password)
    assert token, "Admin login failed"
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="module")
async def brand_viewer(brand_client: AsyncClient, brand_admin: dict[str, str]) -> dict[str, str]:
    unique = uuid.uuid4().hex[:8]
    email = f"brandview-{unique}@test.io"
    password = f"BrandView{unique}9"
    resp = await brand_client.post(
        "/api/v1/users/",
        json={"email": email, "password": password, "full_name": "Brand Viewer", "role": "viewer"},
        headers=brand_admin,
    )
    assert resp.status_code == 201, f"Viewer create failed: {resp.text}"
    token = await _login(brand_client, email, password)
    assert token, "Viewer login failed"
    return {"Authorization": f"Bearer {token}"}


# --- Tests ------------------------------------------------------------------


class TestAppBranding:
    """issue #272 - workspace branding persists on the server."""

    async def test_get_is_public_and_defaults(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        # Reset to a known baseline, then read with NO auth header at all.
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        resp = await brand_client.get("/api/v1/branding/")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "default"
        assert body["logo_data_url"] is None
        assert body["company_name"] == ""

    async def test_admin_sets_brand_and_anyone_reads_it(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        put = await brand_client.put(
            "/api/v1/branding/",
            json={"mode": "text", "company_name": "Acme Construction"},
            headers=brand_admin,
        )
        assert put.status_code == 200, put.text
        assert put.json()["mode"] == "text"
        assert put.json()["company_name"] == "Acme Construction"

        # A later, unauthenticated read sees the workspace brand.
        got = await brand_client.get("/api/v1/branding/")
        assert got.status_code == 200, got.text
        assert got.json()["company_name"] == "Acme Construction"
        assert got.json()["mode"] == "text"

        # A logo wins over text: setting both reconciles mode to "logo".
        put2 = await brand_client.put(
            "/api/v1/branding/",
            json={"mode": "text", "logo_data_url": _PNG_DATA_URL, "company_name": "Acme"},
            headers=brand_admin,
        )
        assert put2.status_code == 200, put2.text
        assert put2.json()["mode"] == "logo"
        assert put2.json()["logo_data_url"] == _PNG_DATA_URL

    async def test_non_admin_cannot_write_or_clear(
        self,
        brand_client: AsyncClient,
        brand_viewer: dict[str, str],
    ) -> None:
        put = await brand_client.put(
            "/api/v1/branding/",
            json={"mode": "text", "company_name": "Hijack"},
            headers=brand_viewer,
        )
        assert put.status_code == 403, put.text

        delete = await brand_client.delete("/api/v1/branding/", headers=brand_viewer)
        assert delete.status_code == 403, delete.text

    async def test_bad_logo_is_sanitised(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        # A logo that is not an image data URL must not be stored; with no name
        # either, the brand falls back to default.
        put = await brand_client.put(
            "/api/v1/branding/",
            json={"mode": "logo", "logo_data_url": "javascript:alert(1)", "company_name": ""},
            headers=brand_admin,
        )
        assert put.status_code == 200, put.text
        assert put.json()["mode"] == "default"
        assert put.json()["logo_data_url"] is None

    async def test_admin_can_clear_brand(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        await brand_client.put(
            "/api/v1/branding/",
            json={"mode": "text", "company_name": "Temporary"},
            headers=brand_admin,
        )
        cleared = await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["mode"] == "default"

        got = await brand_client.get("/api/v1/branding/")
        assert got.json()["mode"] == "default"
        assert got.json()["company_name"] == ""


class TestTheSupportDestinationAndLegalLinkOverHttp:
    """The two workspace settings Part F added, exercised through the endpoints.

    The unit tests beside ``app_branding.py`` cover the sanitiser. These cover the
    surface a user actually meets, and each one exists because the sanitiser
    passing does not imply the endpoint is right: a field can be validated
    perfectly and still be dropped by the response model, or written by a viewer
    who should not be allowed to write it.

    They are appended to this module rather than given one of their own so they
    reuse its module-scoped client and its admin and viewer fixtures, which stand
    up a whole application and two accounts. Duplicating that for seven assertions
    would cost a shard minutes to prove nothing extra.

    WHICH OF THESE GATE ON PUSH, AND WHY THE OTHERS DO NOT

    ``tests/integration`` is gated on push and pull request by one job and nothing
    else: the sharded Backend CI runs ``pytest tests/unit`` only, and the full
    suite workflow is disabled on a fork. That job selects by the
    ``tenant_isolation`` marker plus a frozen ``-k`` chain it is documented not to
    extend. So a test here is gated if and only if it earns the marker, which
    ``backend/pyproject.toml`` defines as identity being the variable under test -
    who is asking, and what that identity may or may not reach, both polarities,
    with the allow side earning it by pairing with a denial in the same file.

    Five of the eight earn it and carry it: the two denials (a viewer refused on
    each field, asserted as a 403 and as a row that did not change) and the three
    that establish who may read and write the record at all (an anonymous reader
    seeing the defaults, an admin writing and an anonymous reader seeing it, an
    admin hiding the link and showing it again - the allow side of the gate the
    denials exercise).

    Three do not, and are left unmarked rather than marked to order: the refused
    URL scheme, the merge that keeps an omitted field, and clearing a destination.
    In each the identity is incidental setup - an admin is merely the actor - and
    the variable under test is a value. Marking them anyway would spend the
    marker's meaning to buy a green tick, and the definition says plainly that a
    test earns it by what it exercises. What they cost is real and is recorded
    here rather than hidden: they execute in no push lane on this fork. The
    sanitiser they sit on top of is gated in
    ``tests/unit/test_the_support_destination_and_legal_link_are_workspace_settings.py``,
    so the rules themselves are not ungated - only these three routes to them.
    """

    @pytest.mark.tenant_isolation
    async def test_get_is_public_and_reports_no_destination_and_a_visible_link(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        """The defaults, read with no token at all.

        Public because the footers and the login page render before anybody signs
        in. ``support_destination`` null is the state in which the UI hides the
        bug-report channel entirely.
        """
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        resp = await brand_client.get("/api/v1/branding/")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["support_destination"] is None
        assert body["legal_link_visible"] is True

    @pytest.mark.tenant_isolation
    async def test_an_admin_sets_a_mailbox_and_an_anonymous_reader_sees_it(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        put = await brand_client.put(
            "/api/v1/branding/",
            json={"support_destination": {"kind": "email", "value": "support@builder.example"}},
            headers=brand_admin,
        )
        assert put.status_code == 200, put.text
        assert put.json()["support_destination"] == {"kind": "email", "value": "support@builder.example"}
        anon = await brand_client.get("/api/v1/branding/")
        assert anon.json()["support_destination"] == {"kind": "email", "value": "support@builder.example"}

    @pytest.mark.tenant_isolation
    async def test_a_non_admin_cannot_set_the_destination(
        self,
        brand_client: AsyncClient,
        brand_viewer: dict[str, str],
        brand_admin: dict[str, str],
    ) -> None:
        """A support destination is a workspace decision, not a personal one.

        Read is public, write is admin-only, and the refusal has to leave what an
        admin set untouched rather than half-applying.
        """
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        resp = await brand_viewer.put(
            "/api/v1/branding/",
            json={"support_destination": {"kind": "url", "value": "https://elsewhere.example"}},
            headers=brand_viewer,
        )
        assert resp.status_code == 403, resp.text
        after = await brand_client.get("/api/v1/branding/")
        assert after.json()["support_destination"] is None

    @pytest.mark.tenant_isolation
    async def test_a_non_admin_cannot_turn_the_legal_link_off(
        self,
        brand_client: AsyncClient,
        brand_viewer: dict[str, str],
        brand_admin: dict[str, str],
    ) -> None:
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        resp = await brand_viewer.put("/api/v1/branding/", json={"legal_link_visible": False}, headers=brand_viewer)
        assert resp.status_code == 403, resp.text
        assert (await brand_client.get("/api/v1/branding/")).json()["legal_link_visible"] is True

    async def test_a_url_that_executes_instead_of_navigating_is_refused_at_the_edge(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        """Sanitised rather than stored, and the request still succeeds.

        The endpoint does not 4xx on a bad scheme; it drops the value and answers
        with what it kept. That is the existing contract for a bad logo, and a
        destination an attacker could get opened in a user's browser is the same
        class of problem, so it gets the same treatment: refuse the value, never
        serve it.
        """
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        for scheme in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "https://u:p@x.example"):
            put = await brand_client.put(
                "/api/v1/branding/",
                json={"support_destination": {"kind": "url", "value": scheme}},
                headers=brand_admin,
            )
            assert put.status_code == 200, put.text
            assert put.json()["support_destination"] is None, scheme
            assert (await brand_client.get("/api/v1/branding/")).json()["support_destination"] is None, scheme

    @pytest.mark.tenant_isolation
    async def test_an_admin_can_hide_the_legal_link_and_show_it_again(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        off = await brand_client.put("/api/v1/branding/", json={"legal_link_visible": False}, headers=brand_admin)
        assert off.status_code == 200, off.text
        assert off.json()["legal_link_visible"] is False
        assert (await brand_client.get("/api/v1/branding/")).json()["legal_link_visible"] is False
        on = await brand_client.put("/api/v1/branding/", json={"legal_link_visible": True}, headers=brand_admin)
        assert on.json()["legal_link_visible"] is True

    async def test_setting_one_field_leaves_the_other_alone(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        """The router merges by ``model_fields_set``, so an omitted field persists.

        Without this, saving a support mailbox would silently turn the Legal link
        back on, and saving the logo would clear the mailbox. Both are the same
        bug and both are invisible in a UI that only ever sends one thing.
        """
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        await brand_client.put(
            "/api/v1/branding/",
            json={"legal_link_visible": False, "support_destination": {"kind": "email", "value": "a@b.example"}},
            headers=brand_admin,
        )
        await brand_client.put("/api/v1/branding/", json={"company_name": "Meridian"}, headers=brand_admin)
        body = (await brand_client.get("/api/v1/branding/")).json()
        assert body["company_name"] == "Meridian"
        assert body["legal_link_visible"] is False
        assert body["support_destination"] == {"kind": "email", "value": "a@b.example"}

    async def test_clearing_the_destination_hides_the_channel_again(
        self,
        brand_client: AsyncClient,
        brand_admin: dict[str, str],
    ) -> None:
        await brand_client.delete("/api/v1/branding/", headers=brand_admin)
        await brand_client.put(
            "/api/v1/branding/",
            json={"support_destination": {"kind": "url", "value": "https://support.example/new"}},
            headers=brand_admin,
        )
        cleared = await brand_client.put("/api/v1/branding/", json={"support_destination": None}, headers=brand_admin)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["support_destination"] is None
