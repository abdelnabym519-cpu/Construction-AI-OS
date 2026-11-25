# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Every credential this product mints names an organization, or it is refused.

Phase 02b put the tenant into the token (ADR 005). This file is the regression
suite for the promise that came with it: that all six ways in produce the same
credential shape, so there is no door where a caller arrives without a tenant and
falls back to being their own.

The six flows are driven one test each, through their own public entry points.
That is deliberate. They find the user differently (password, demo seed, a
provisioned desktop owner, an OIDC subject, a refresh token, a password change),
they guard differently, and three of them create the account they then issue for.
A shared helper asserting the claim once would have passed while any one of them
resolved the tenant on its own path.

Two structural tests come first, and they are the ones that keep this file honest
as the module grows: they read the source and assert that ``_issue_token_pair`` is
called from exactly these six methods, and that the access token is minted in
exactly one place with the claim in it. A seventh flow added later, or a second
``create_access_token`` call that forgets the claim, fails here before it reaches a
user - which is the only time such an omission is cheap to fix.

The remaining tests pin what the claim *says*: the default organization wins, an
install with no default answers the same way twice, an explicit switch is honoured,
a switch to somebody else's organization is refused and leaves no session behind, a
caller no organization has gets one of their own rather than being folded into a
company they were never added to, and a membership that is not active is not
silently rejoined.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from tests._pg import membership_in, organization_for, transactional_session

DEMO_ADMIN = "demo@openconstructionerp.com"
DEMO_MANAGER = "manager@openconstructionerp.com"
DESKTOP_OWNER = "owner@openestimate.local"
PASSWORD = "CorrectHorse9!"
SERVICE = pathlib.Path(__file__).resolve().parents[2] / "app" / "modules" / "users" / "service.py"

# The six flows, in the order the router registers them. One test each below.
FLOWS = (
    "login",
    "demo_login",
    "desktop_bootstrap",
    "oidc_login",
    "refresh_tokens",
    "change_password",
)


@pytest_asyncio.fixture
async def session(monkeypatch):
    """An empty database holding the two demo rows the demo flow signs in to."""
    import app.core.demo_login
    from app.modules.users.models import User
    from app.modules.users.service import hash_password

    # The two switches in front of the shortcut, set the way a self-installed
    # platform with the demo seed on has them.
    monkeypatch.setenv("SEED_DEMO", "true")
    monkeypatch.setattr(app.core.demo_login, "demo_login_enabled", lambda *_a, **_k: True)

    async with transactional_session() as s:
        for email, role in ((DEMO_ADMIN, "admin"), (DEMO_MANAGER, "manager")):
            s.add(
                User(
                    id=uuid.uuid4(),
                    email=email,
                    hashed_password=hash_password(uuid.uuid4().hex),
                    full_name=email.split("@")[0],
                    role=role,
                    locale="en",
                    is_active=True,
                    metadata_={},
                )
            )
        await s.commit()
        yield s


def _service(session):
    from app.config import get_settings
    from app.modules.users.service import UserService

    return UserService(session, get_settings())


def _claims(token: str) -> dict:
    from app.config import get_settings
    from app.core.jwt_keys import decode_jwt

    return decode_jwt(token, get_settings())


async def _make_user(session, *, role: str = "editor", email: str | None = None, password: str = PASSWORD):
    from app.modules.users.models import User
    from app.modules.users.service import hash_password

    user = User(
        id=uuid.uuid4(),
        email=email or f"flow-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password=hash_password(password),
        full_name="Flow Tester",
        role=role,
        locale="en",
        is_active=True,
        metadata_={},
    )
    session.add(user)
    await session.commit()
    return user


def _form(email: str, password: str = PASSWORD):
    from app.modules.users.schemas import LoginRequest

    return LoginRequest(email=email, password=password)


async def _sessions(session) -> int:
    from app.modules.users.models import UserSession

    return int(await session.scalar(select(func.count()).select_from(UserSession)))


def _user_service_methods() -> list[ast.AST]:
    tree = ast.parse(SERVICE.read_text())
    service = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "UserService")
    return [n for n in service.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))]


def _called_from(fn: ast.AST, callee: str) -> bool:
    """True if ``fn`` calls ``callee``, however the call is spelled.

    Both spellings matter. ``self._issue_token_pair(...)`` is an attribute access,
    but ``create_access_token(...)`` is a bare name imported at module level, and a
    census that only looked at one of the two shapes reported zero callers for the
    other and passed while saying nothing.
    """
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute) and target.attr == callee:
            return True
        if isinstance(target, ast.Name) and target.id == callee:
            return True
    return False


# ── structure: the six are the complete set ─────────────────────────────────


def test_the_six_flows_are_the_complete_set_of_minting_call_sites() -> None:
    """``_issue_token_pair`` has exactly six callers, and they are the six flows.

    This is the assertion that makes "all six behave identically" checkable rather
    than aspirational. It fails when a flow is added that issues tokens some other
    way, which is precisely the case the claim cannot cover by construction.
    """
    callers = sorted(fn.name for fn in _user_service_methods() if _called_from(fn, "_issue_token_pair"))
    assert callers == sorted(FLOWS), f"token-minting call sites are {callers}, expected {sorted(FLOWS)}"


def test_the_access_token_is_minted_once_and_always_with_the_claim() -> None:
    """One ``create_access_token`` call in the service, carrying the tenant claim.

    A second minting site is the realistic way this guarantee decays: somebody adds
    a service-account token or an impersonation path, copies the neighbouring call,
    and omits the third argument because nothing fails when they do. Asserting the
    shape of the call, and not only that the claim reaches a token, is what makes
    that omission a test failure instead of a production incident.
    """
    from app.core.tenant_scope import TENANT_CLAIM

    minting = [fn for fn in _user_service_methods() if _called_from(fn, "create_access_token")]
    assert [fn.name for fn in minting] == ["_issue_token_pair"], (
        f"create_access_token is called from {[fn.name for fn in minting]}; the tenant claim is "
        "attached in _issue_token_pair only, so any other call site mints a credential with no tenant"
    )

    calls = [
        node
        for node in ast.walk(minting[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "create_access_token"
    ]
    assert len(calls) == 1, f"expected one minting call, found {len(calls)}"
    claim_argument = calls[0].args[2]
    assert isinstance(claim_argument, ast.Dict), "the tenant claim must be passed positionally as a dict"
    # The key has to be the constant, not a string that happens to equal it. The
    # module defines TENANT_CLAIM so that the encoder, the resolver and this call
    # cannot drift apart, and a literal here would drift silently the moment the
    # claim was renamed.
    keys = [k.id if isinstance(k, ast.Name) else k.value for k in claim_argument.keys]
    assert keys == ["TENANT_CLAIM"], (
        f"the claim dict is keyed {keys}; it must be keyed by the TENANT_CLAIM constant, whose value is {TENANT_CLAIM!r}"
    )


# ── one test per flow ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_names_the_callers_organization(session) -> None:
    """Password login: the expected organization is known before the call."""
    user = await _make_user(session, role="manager")
    expected = await membership_in(session, user, is_default=True)

    tokens = await _service(session).login(_form(user.email))

    assert _claims(tokens.access_token)["tenant_id"] == expected
    assert expected != str(user.id), "the tenant is the organization, not the caller"


@pytest.mark.asyncio
async def test_demo_login_names_the_callers_organization(session) -> None:
    """The seeded demo admin gets an organization like any other caller.

    Worth its own test: the demo seed predates tenancy and writes users directly,
    so it is exactly the path where an account could exist with no membership and
    quietly issue a claimless token.
    """
    from app.modules.users.models import User

    admin = await session.scalar(select(User).where(User.email == DEMO_ADMIN))
    expected = await organization_for(session, admin)

    tokens = await _service(session).demo_login(DEMO_ADMIN)

    assert _claims(tokens.access_token)["tenant_id"] == expected


@pytest.mark.asyncio
async def test_desktop_bootstrap_names_the_owners_organization(session) -> None:
    """The local desktop owner is provisioned and named in one call, then stable.

    ``desktop_bootstrap`` creates the account it issues for, so there is no
    membership to predict beforehand. What is asserted instead is the property that
    matters to a desktop install: the owner lands in an organization, and a second
    bootstrap of the same owner names the same one rather than a fresh empty
    organization per restart.
    """
    first = await _service(session).desktop_bootstrap()
    second = await _service(session).desktop_bootstrap()

    tenant = _claims(first.access_token)["tenant_id"]
    assert tenant, "the desktop owner's token must name an organization"
    assert _claims(second.access_token)["tenant_id"] == tenant


@pytest.mark.asyncio
async def test_oidc_login_names_the_callers_organization(session) -> None:
    """An identity provider's first sign-in auto-creates a user and a tenant for it.

    The same stability check as the desktop flow, for the same reason: the second
    sign-in finds the account by ``oidc_sub`` and must not invent a second
    organization for somebody who already has one.
    """
    sub = f"oidc-{uuid.uuid4().hex[:10]}"
    email = f"sso-{uuid.uuid4().hex[:8]}@example.com"
    first = await _service(session).oidc_login(
        oidc_sub=sub, oidc_issuer="https://idp.example.com/realms/test", email=email, full_name="SSO Tester"
    )
    second = await _service(session).oidc_login(
        oidc_sub=sub, oidc_issuer="https://idp.example.com/realms/test", email=email, full_name="SSO Tester"
    )

    tenant = _claims(first.access_token)["tenant_id"]
    assert tenant
    assert _claims(second.access_token)["tenant_id"] == tenant


@pytest.mark.asyncio
async def test_a_returning_sso_user_is_not_refused(session) -> None:
    """A second sign-in from the same identity provider succeeds.

    Not a tenancy assertion, and it was not one when written: the stability check
    above signs the same subject in twice and tripped over this. ``oidc_login`` had
    its "no matching account and auto-creation is disabled" refusal as the ``else``
    of the *role sync*, so the condition that actually raised was "your role already
    matches the group map" - true for every returning user, and on an install with
    no ``OIDC_GROUP_ROLE_MAP`` the map is empty and ``role_from_groups`` is None, so
    it was true for all of them. Repeat SSO sign-in was refused with a message
    pointing at a setting nobody had touched.

    The block predates this phase (it is identical at the branch base), so the
    repair is a dedent that puts the refusal back on the branch whose message
    describes it, and the test is here because this is where it was found.
    """
    sub = f"oidc-{uuid.uuid4().hex[:10]}"
    email = f"returning-{uuid.uuid4().hex[:8]}@example.com"
    kwargs = {
        "oidc_sub": sub,
        "oidc_issuer": "https://idp.example.com/realms/test",
        "email": email,
        "full_name": "Returning SSO User",
    }

    first = await _service(session).oidc_login(**kwargs)
    second = await _service(session).oidc_login(**kwargs)
    # And with a group map that resolves to the role the user already has, which is
    # the shape that raised before: the sync finds nothing to change.
    third = await _service(session).oidc_login(**kwargs, role_from_groups="editor")

    assert first.access_token and second.access_token and third.access_token


@pytest.mark.asyncio
async def test_a_returning_sso_user_with_no_account_and_no_auto_creation_is_refused(session) -> None:
    """The refusal that the dedent preserved, on the branch that describes it."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        await _service(session).oidc_login(
            oidc_sub=f"oidc-{uuid.uuid4().hex[:10]}",
            oidc_issuer="https://idp.example.com/realms/test",
            email=f"stranger-{uuid.uuid4().hex[:8]}@example.com",
            full_name="No Account Here",
            auto_create=False,
        )

    assert refused.value.status_code == 403
    assert "auto-creation is disabled" in str(refused.value.detail)


@pytest.mark.asyncio
async def test_refresh_names_the_organization_the_caller_has_now(session) -> None:
    """Refresh re-resolves rather than copying, which is what makes it the recovery path.

    A credential issued before organizations existed is refused at the door, and the
    refusal tells the holder to refresh. That instruction is only true if refreshing
    resolves the tenant against the database as it stands now, so this moves the
    caller into a different organization and checks the new token follows.
    """
    from sqlalchemy import update

    from app.modules.users.models import Membership

    user = await _make_user(session)
    before = await organization_for(session, user)
    tokens = await _service(session).login(_form(user.email))
    assert _claims(tokens.access_token)["tenant_id"] == before

    # Move the caller the way an administrator would: the old membership stops
    # being the default and the new one becomes it. Flagging a second membership
    # without unflagging the first leaves two defaults, and the documented
    # tie-break is then the id, so the token would keep naming the organization it
    # already named and this test would assert nothing about refresh at all.
    await session.execute(
        update(Membership).where(Membership.organization_id == uuid.UUID(before)).values(is_default=False)
    )
    await session.commit()
    after = await membership_in(session, user, is_default=True)
    refreshed = await _service(session).refresh_tokens(tokens.refresh_token)

    assert after != before, "the fixture must actually move the caller"
    assert _claims(refreshed.access_token)["tenant_id"] == after


@pytest.mark.asyncio
async def test_change_password_names_the_callers_organization(session) -> None:
    """The rotation issued after a password change carries the claim too."""
    from app.modules.users.schemas import ChangePasswordRequest

    user = await _make_user(session)
    expected = await organization_for(session, user)

    tokens = await _service(session).change_password(
        user.id, ChangePasswordRequest(current_password=PASSWORD, new_password="AnotherGood123!")
    )

    assert _claims(tokens.access_token)["tenant_id"] == expected


# ── what the claim says ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_refresh_token_itself_carries_no_tenant_claim(session) -> None:
    """The recovery credential is deliberately unscoped.

    Freezing an organization into a 30-day credential would make a switch, or a
    membership an administrator moved, wait for it to expire. The access token is
    the short-lived one, so that is the one that carries the answer.
    """
    user = await _make_user(session)
    await organization_for(session, user)

    tokens = await _service(session).login(_form(user.email))

    assert "tenant_id" not in _claims(tokens.refresh_token)


@pytest.mark.asyncio
async def test_nothing_else_about_the_access_token_changed(session) -> None:
    """The claim is an addition, not a re-shaping of the credential.

    Pinned because the response contract is supposed to be unchanged in this phase:
    if a client reads ``role`` or ``sid`` out of an access token, it still can.
    """
    user = await _make_user(session, role="manager")
    expected = await organization_for(session, user)

    claims = _claims((await _service(session).login(_form(user.email))).access_token)

    for name in ("iss", "sub", "email", "role", "iat", "exp", "type", "jti", "sid", "tenant_id"):
        assert name in claims, f"the access token lost its {name!r} claim"
    assert claims["sub"] == str(user.id)
    assert claims["role"] == "manager"
    assert claims["type"] == "access"
    assert claims["tenant_id"] == expected


@pytest.mark.asyncio
async def test_the_default_organization_wins_over_a_second_membership(session) -> None:
    """A caller in two organizations sees the one flagged as theirs."""
    user = await _make_user(session)
    other = await membership_in(session, user)
    default = await membership_in(session, user, is_default=True)

    tokens = await _service(session).login(_form(user.email))

    assert _claims(tokens.access_token)["tenant_id"] == default
    assert default != other


@pytest.mark.asyncio
async def test_an_install_with_no_default_answers_the_same_way_twice(session) -> None:
    """Determinism where nothing flags a default, which is the shape a backfill leaves.

    The documented order is default first, then oldest, then by id. ``created_at`` on
    this model is a Python-side default rather than a database one, so two memberships
    written one after the other do not tie on it and the older wins outright; the id is
    the tie-break for rows that do tie. An earlier version of this test asserted
    ``min(first, second)``, which is not the rule but a guess about clock resolution,
    and it failed as soon as the two inserts landed a microsecond apart.

    So the expected answer is computed here from the stored rows, in Python, in the
    documented order, independently of the SQL the resolver runs. The property under
    test is that two logins agree with it and with each other: a caller must not change
    tenant between two logins a second apart, or every row they wrote in between would
    belong to a company they cannot see any more.
    """
    from sqlalchemy import select

    from app.core.tenant_scope import MEMBERSHIP_ACTIVE
    from app.modules.users.models import Membership

    user = await _make_user(session)
    first = await membership_in(session, user)
    second = await membership_in(session, user)

    stored = (
        await session.execute(
            select(Membership.organization_id, Membership.created_at, Membership.id).where(
                Membership.user_id == user.id,
                Membership.status == MEMBERSHIP_ACTIVE,
            )
        )
    ).all()
    assert len(stored) == 2, stored
    # default first (none flagged here), then oldest, then smallest id. The id is
    # compared as text because that is how the column stores it, so this agrees with
    # the database's own ordering rather than with Python's idea of a UUID.
    expected = str(min(stored, key=lambda row: (row.created_at, str(row.id))).organization_id)
    assert expected in {first, second}

    one = _claims((await _service(session).login(_form(user.email))).access_token)["tenant_id"]
    two = _claims((await _service(session).login(_form(user.email))).access_token)["tenant_id"]

    assert one == two == expected


@pytest.mark.asyncio
async def test_an_explicit_switch_names_the_requested_organization(session) -> None:
    """The switch path resolves to the organization asked for.

    There is no HTTP endpoint for this yet, and adding one is Phase 02c work: a
    switch changes what every subsequent request can see, so it belongs beside the
    organization administration that will list the choices. What exists now is the
    service-level path, tested here so the endpoint has something correct to call.
    """
    user = await _make_user(session)
    await membership_in(session, user, is_default=True)
    other = await membership_in(session, user)

    tokens = await _service(session)._issue_token_pair(user, organization_id=other)  # noqa: SLF001

    assert _claims(tokens.access_token)["tenant_id"] == other


@pytest.mark.asyncio
async def test_a_switch_to_an_organization_the_caller_is_not_in_is_refused(session) -> None:
    """Refused, and refused before anything is written.

    The alternative is a credential naming a company its holder does not belong to,
    which is the exact forgery this claim exists to make unnecessary. The session
    count is asserted because the ordering is not incidental: the tenant is resolved
    before the session row is opened, so a refusal cannot leave a live session that
    no token names, and therefore no revocation can reach.
    """
    from fastapi import HTTPException

    user = await _make_user(session)
    await organization_for(session, user)
    stranger = await _make_user(session)
    their_organization = await organization_for(session, stranger)
    before = await _sessions(session)

    with pytest.raises(HTTPException) as refused:
        await _service(session)._issue_token_pair(user, organization_id=their_organization)  # noqa: SLF001

    assert refused.value.status_code == 403
    assert await _sessions(session) == before, "a refused switch must not open a session"


@pytest.mark.asyncio
async def test_a_caller_no_organization_has_gets_one_of_their_own(session) -> None:
    """Two newcomers in a multi-organization install do not join an existing company.

    This is the case where the obvious implementation is a data leak. The install has
    two organizations with members; a third person registers; the tempting answers
    are to refuse them, or to put them in the first organization found. Refusing
    breaks every self-registration in a multi-company install, and joining puts their
    contacts and projects inside a tenant nobody added them to, where their
    colleagues' rows are already visible to them. They get an empty organization of
    their own, which an administrator can move them out of later.
    """
    from app.modules.users.models import Membership

    established = [await _make_user(session, role="admin") for _ in range(2)]
    companies = [await membership_in(session, user, is_default=True) for user in established]

    newcomers = [await _make_user(session) for _ in range(2)]
    tenants = [
        _claims((await _service(session).login(_form(user.email))).access_token)["tenant_id"] for user in newcomers
    ]

    for tenant in tenants:
        assert tenant not in companies, "a newcomer was folded into an existing company"
    assert tenants[0] != tenants[1], "two newcomers share an organization"
    for user, tenant in zip(newcomers, tenants, strict=True):
        rows = (
            await session.execute(
                select(Membership.organization_id, Membership.is_default).where(Membership.user_id == user.id)
            )
        ).all()
        assert [(str(organization), bool(flag)) for organization, flag in rows] == [(tenant, True)]


@pytest.mark.asyncio
async def test_a_newcomer_in_a_single_organization_install_joins_it(session) -> None:
    """The single-company shape keeps the answer the boot repair would have given.

    The test above is about an install with several companies. With one, or none,
    there is nothing to choose between and nobody is being placed anywhere they were
    not already: this is the install the ``users_tenant_organization_backfill``
    repair produces, and a caller who arrives before that repair has run must land
    in the same organization it would have named.
    """
    from app.modules.users.models import Membership

    owner = await _make_user(session, role="admin")
    company = await membership_in(session, owner, is_default=True)

    newcomer = await _make_user(session)
    tokens = await _service(session).login(_form(newcomer.email))

    assert _claims(tokens.access_token)["tenant_id"] == company
    membership = (
        await session.execute(
            select(Membership.status, Membership.is_default).where(
                Membership.user_id == newcomer.id, Membership.organization_id == uuid.UUID(company)
            )
        )
    ).one_or_none()
    assert membership is not None, "the token named an organization the caller has no membership of"
    assert membership == ("active", True)


@pytest.mark.asyncio
async def test_a_membership_that_is_not_active_is_not_silently_rejoined(session) -> None:
    """A suspension survives the next login.

    Refusing looks harsh next to the newcomer cases above, and the difference is who
    decided. A caller with no membership at all was never placed anywhere, so placing
    them is answering an open question. A caller whose membership was set inactive
    was placed and then removed, by somebody with the standing to do it, and a login
    that quietly restored it would make that act meaningless. The unique pair means
    the restored row would collide with the suspended one and be swallowed as an
    ordinary duplicate, so nothing would even report it.
    """
    from fastapi import HTTPException

    user = await _make_user(session)
    await membership_in(session, user, is_default=True, status="suspended")

    with pytest.raises(HTTPException) as refused:
        await _service(session).login(_form(user.email))

    assert refused.value.status_code == 403
