# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""What the rollout does to somebody who is already signed in.

Phase 02b made the ``tenant_id`` claim mandatory on an access token. Every credential
issued before that has no claim, and the question this file answers is what such a
credential does next: it is refused with 401 and a ``WWW-Authenticate: Bearer`` header,
it is never reinterpreted as "this user's own rows", and the refresh flow - which the
client is expected to try - hands back an access token that carries the claim and works.

The refusal is deliberately a 401 rather than a 403, because a 401 is the status that
means "get a new credential", and the frontend already acts on it: ``frontend/src/shared/lib/api.ts``
distinguishes a 401 from an auth endpoint, which means the refresh token itself was
refused and ends the session, from any other 401, which triggers a silent refresh and a
retry. So a user who was signed in across the upgrade loses one request and keeps their
session, which is the whole reason the claim was made mandatory at the door instead of
being defaulted from ``sub`` inside each module.

The legacy token here is minted by calling ``create_access_token`` with no extra claims,
which is exactly what the six flows did before this phase, rather than being hand-built:
a hand-built token could differ from a real old one in a way that hides the behaviour.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

pytestmark = pytest.mark.asyncio

_ROUTE = "/api/v1/contacts/"


@pytest_asyncio.fixture(scope="module")
async def app_instance():
    """Boot the app once for the module; lifespan runs ``create_all``."""
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture(scope="module")
async def http_client(app_instance):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://rollout") as ac:
        yield ac


@pytest_asyncio.fixture
async def credential(http_client):
    """A registered, logged-in caller, plus the tokens that login issued."""
    password = f"Rollout{uuid.uuid4().hex[:8]}9!"
    email = f"rollout-{uuid.uuid4().hex[:8]}@legacy-token.io"
    registered = await http_client.post(
        "/api/v1/users/auth/register",
        json={"email": email, "password": password, "full_name": "Rollout Caller"},
    )
    assert registered.status_code in (200, 201), registered.text
    user_id = registered.json()["id"]

    logged_in = await http_client.post("/api/v1/users/auth/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200, logged_in.text
    tokens = logged_in.json()
    return {"user_id": user_id, "access": tokens["access_token"], "refresh": tokens["refresh_token"]}


async def _legacy_access_token(user_id: str) -> str:
    """An access token shaped the way every flow minted them before Phase 02b."""
    from app.config import get_settings
    from app.database import async_session_factory
    from app.modules.users.models import User
    from app.modules.users.service import create_access_token

    async with async_session_factory() as session:
        user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    return create_access_token(user, get_settings())


def _claims(token: str) -> dict:
    return jwt.get_unverified_claims(token)


async def test_a_token_with_no_tenant_claim_is_refused_and_not_reinterpreted(http_client, credential) -> None:
    """The door refuses it, and says how to get a credential it will accept."""
    legacy = await _legacy_access_token(credential["user_id"])
    # The shape under test: a valid signature, a real subject, and no tenant claim.
    assert "tenant_id" not in _claims(legacy), _claims(legacy)
    assert _claims(legacy)["sub"] == credential["user_id"]

    refused = await http_client.get(_ROUTE, headers={"Authorization": f"Bearer {legacy}"})

    assert refused.status_code == 401, (refused.status_code, refused.text)
    assert refused.headers.get("www-authenticate", "").startswith("Bearer"), refused.headers
    assert "tenant" in refused.json()["detail"].lower(), refused.text


async def test_the_credential_this_login_issued_is_accepted(http_client, credential) -> None:
    """So the refusal above is about the missing claim and not about the caller."""
    assert _claims(credential["access"])["tenant_id"]

    accepted = await http_client.get(_ROUTE, headers={"Authorization": f"Bearer {credential['access']}"})

    assert accepted.status_code != 401, (accepted.status_code, accepted.text)


async def test_a_refresh_replaces_the_legacy_token_with_one_that_carries_the_claim(http_client, credential) -> None:
    """The rollout path: refused, refreshed, carried on. Nobody is logged out."""
    refreshed = await http_client.post("/api/v1/users/auth/refresh", json={"refresh_token": credential["refresh"]})
    assert refreshed.status_code == 200, refreshed.text
    new_access = refreshed.json()["access_token"]

    claims = _claims(new_access)
    assert claims["tenant_id"], claims
    assert claims["tenant_id"] == _claims(credential["access"])["tenant_id"], "the refresh changed the tenant"
    assert claims["sub"] == credential["user_id"]

    works = await http_client.get(_ROUTE, headers={"Authorization": f"Bearer {new_access}"})
    assert works.status_code != 401, (works.status_code, works.text)


async def test_the_refresh_token_itself_carries_no_tenant_claim(http_client, credential) -> None:
    """A refresh token is not a credential for data, so it names no company.

    Which organization a caller acts in is decided when an access token is minted,
    from the memberships as they are at that moment. Stamping the refresh token too
    would freeze that choice for the life of the session and make an explicit switch
    impossible without signing in again.
    """
    refreshed = await http_client.post("/api/v1/users/auth/refresh", json={"refresh_token": credential["refresh"]})
    assert refreshed.status_code == 200, refreshed.text

    claims = _claims(refreshed.json()["refresh_token"])
    assert "tenant_id" not in claims, claims
    assert claims.get("type") == "refresh", claims
