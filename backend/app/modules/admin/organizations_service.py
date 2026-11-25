# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Installation-level organization administration.

An organization is the tenant (ADR 005): every ``tenant_id`` column in the
schema names one, and every access token's ``tenant_id`` claim carries one.
Until this module existed nothing in the product could create a second one. The
only two places that constructed an ``Organization`` were both inside the login
path - :meth:`UserService._create_default_organization`, which runs only when
the install has none, and :meth:`UserService._create_organization_for`, which
runs only when it already has more than one. Neither could be reached from the
other, so an install booted into exactly one organization and stayed there: no
route, no CLI command and no seed ever wrote a second row. Project participant
grants (ADR 006) invite *another organization* into a project, and there was no
other organization to invite.

WHO MAY CALL THIS, AND WHO MAY NOT

The installation administrator only - the role the ``admin`` value of
``oe_users_user.role`` names, which is what :class:`app.dependencies.RequireRole`
reads back out of the database rather than out of the token claim.

An administrator *of one organization* may not. ``oe_users_membership.role`` is
a per-organization role and the model says why: "an admin of one company is not
an admin of another, and a ``role`` column on ``User`` would say they were".
Listing every organization would hand that person the names of every other
company on the installation, which in a multi-tenant install is confidential
information about who else is a customer and not a consequence of administering
one's own. So this module has no route an organization administrator can reach,
and the check is made twice: once by the route's ``RequireRole("admin")``
dependency and once here against the caller's role, so that mounting these
functions behind another door does not quietly widen who is on the other side of
it.

WHAT THIS MODULE DOES NOT DO

There is no delete, no rename and no move. Deleting an organization would orphan
every ``tenant_id`` that names it, renaming one would break the slug two boot
paths rely on to be stable, and moving a person between organizations would
change which company's rows their next token names - each is a decision with its
own evidence to gather, and none is needed to make a second tenant exist. A
person may belong to several organizations at once, which is what the membership
table is for, and one of those memberships may be flagged as the default their
token names. Adding a membership never removes another one, and only sets the
default flag when the caller asks for it or when the person has no membership at
all yet.

AUDIT

Every call writes one row through :func:`app.core.audit.audit_log`, in the
caller's transaction and flushed but not committed by it, so the organization
and the row that explains it land together or not at all. That writer is the one
this module uses rather than :func:`app.core.audit_log.log_activity` directly
because of who can read the result: ``oe_core_audit_log`` carries no
``tenant_id`` column, so ``GET /api/v1/audit/{entity_type}/{entity_id}`` answers
for an installation-level entity that belongs to no one tenant, and the same
call mirrors the row into ``oe_activity_log`` for anything building a timeline
from the newer table. Writing only the newer one would have left these rows with
no HTTP reader at all, since the timeline route is project-scoped and an
organization is not a project.

The row names the actor in ``user_id`` and carries the organization, the person
added, the role and the actor's own organization in ``details``, so "who, what,
which organization, which user, when" is answerable from one row.

``actor_id`` may be ``None``, and the row then names no actor. Only the CLI
reaches that: ``create-organization`` can run on an install nobody has
registered on yet, which is exactly when a second organization may need
bootstrapping, and inventing an actor for it would put a person's id on a
decision they did not make. The authority the CLI acts under is the shell it was
typed into, which is the same authority ``promote-admin`` and ``reset-password``
already exercise over the same tables, and the same reason the HTTP routes check
a role and this entry point does not.
"""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_log
from app.modules.users.models import Membership, Organization, User

logger = logging.getLogger(__name__)

#: What an organization's name may be. 255 is the column, and the limit is
#: checked after sanitising rather than before: a name padded out with
#: zero-width characters could otherwise pass a length check and then not fit.
NAME_MAX_LENGTH = 255

#: The slug column. Derived from the name unless the caller supplies one.
SLUG_MAX_LENGTH = 64

#: What a name is reduced to before it is stored, compared and slugged.
#: Zero-width and bidi-control characters are dropped rather than rejected
#: because they are invisible: two names that differ only by one of them look
#: identical to every person who will ever read them, and a duplicate that a
#: human cannot see is a duplicate that a uniqueness check has to catch.
#: Control characters are dropped for the same reason plus one: they can be
#: logged, exported to CSV and read back into a terminal.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\x00-\x1f\x7f]")

#: Runs of anything that is not a letter or a digit become one hyphen.
_NON_SLUG = re.compile(r"[^0-9a-z]+")

#: The audit vocabulary. One entity type per row this module can write, so a
#: reader can ask the ledger for either and get only what they asked for.
ORGANIZATION_ENTITY_TYPE = "organization"
MEMBERSHIP_ENTITY_TYPE = "organization_membership"
ACTION_CREATED = "created"
ACTION_MEMBER_ADDED = "member_added"

__all__ = [
    "ACTION_CREATED",
    "ACTION_MEMBER_ADDED",
    "MEMBERSHIP_ENTITY_TYPE",
    "NAME_MAX_LENGTH",
    "ORGANIZATION_ENTITY_TYPE",
    "SLUG_MAX_LENGTH",
    "add_member",
    "create_organization",
    "list_organizations",
    "sanitize_organization_name",
    "slug_for_name",
]


def _require_installation_administrator(caller_role: str | None) -> None:
    """Refuse anybody the installation has not made an administrator.

    Resolved through the same hierarchy :class:`app.dependencies.RequireRole`
    uses, so "admin" means here what it means at the door, and an unrecognized
    role string is a refusal rather than a guess. The message does not say which
    role the caller has: a 403 that reports the caller's own privilege level
    back to them is a 403 that tells a stranger what they nearly were.
    """
    from app.core.permissions import ROLE_HIERARCHY, Role, _resolve_role

    resolved = _resolve_role(caller_role or "")
    if resolved is None or ROLE_HIERARCHY.get(resolved, -1) < ROLE_HIERARCHY[Role.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an installation administrator can administer organizations.",
        )


def sanitize_organization_name(raw: str) -> str:
    """One name, reduced to what will be stored, compared and slugged.

    Invisible characters out, whitespace runs collapsed to one space, ends
    trimmed. Raises 422 rather than returning an empty string, because an empty
    name is not a smaller version of a valid one: it is a request that did not
    carry the thing being created.
    """
    cleaned = _INVISIBLE.sub("", raw or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An organization needs a name that carries at least one visible character.",
        )
    if len(cleaned) > NAME_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"An organization name is at most {NAME_MAX_LENGTH} characters; this one is {len(cleaned)}.",
        )
    return cleaned


def slug_for_name(name: str) -> str:
    """The address of an organization, derived from its sanitized name.

    Lowercase, letters and digits only, one hyphen between runs, no hyphen at
    either end, at most 64 characters. Deriving it rather than generating a
    random one is what makes a retried create collide on the same value and
    answer 409 twice instead of leaving two organizations behind, which is the
    property the two login paths already rely on for the same reason.
    """
    slug = _NON_SLUG.sub("-", name.lower()).strip("-")[:SLUG_MAX_LENGTH].strip("-")
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "An organization name needs at least one letter or digit to be addressable; "
                f"{name!r} sanitizes to nothing a slug can be made of."
            ),
        )
    return slug


async def _existing_by_name(session: AsyncSession, name: str) -> uuid.UUID | None:
    """The id of an organization already called this, or ``None``.

    Compared case-insensitively and on the sanitized value, because "Acme Ltd"
    and "acme ltd" are the same company to every person who will read the list
    this module returns. The comparison is in SQL rather than over a fetched
    list so it costs one row on an installation with ten thousand of them.
    """
    return await session.scalar(select(Organization.id).where(func.lower(Organization.name) == name.lower()).limit(1))


async def create_organization(
    session: AsyncSession,
    *,
    name: str,
    actor_id: str | uuid.UUID | None = None,
    actor_role: str | None,
    actor_tenant_id: str | uuid.UUID | None = None,
    slug: str | None = None,
) -> Organization:
    """Create one organization, and write the row that says who created it.

    422 for a name that is empty once sanitized, one that is too long, one that
    cannot be addressed, or a supplied slug that cannot be. 409 for a name the
    installation already has, or a slug it already has - including the race,
    which the unique slug constraint settles and this function reports rather
    than retries, because the answer to "somebody else just created it" is the
    same 409 either way.
    """
    _require_installation_administrator(actor_role)

    clean_name = sanitize_organization_name(name)
    clean_slug = slug_for_name(clean_name) if slug is None else sanitize_slug(slug)

    if await _existing_by_name(session, clean_name) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An organization called {clean_name!r} already exists on this installation.",
        )

    organization = Organization(name=clean_name, slug=clean_slug, status="active")
    try:
        async with session.begin_nested():
            session.add(organization)
            await session.flush()
    except IntegrityError:
        # The unique slug is what two concurrent creates collide on. Neither
        # caller did anything wrong and neither can see the other, so the answer
        # is the same 409 the explicit duplicate check above gives.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An organization addressed {clean_slug!r} already exists on this installation.",
        ) from None

    await audit_log(
        session,
        action=ACTION_CREATED,
        entity_type=ORGANIZATION_ENTITY_TYPE,
        entity_id=str(organization.id),
        user_id=str(actor_id) if actor_id else None,
        details={
            "name": organization.name,
            "slug": organization.slug,
            "status": organization.status,
            "actor_tenant_id": str(actor_tenant_id) if actor_tenant_id else None,
        },
    )
    logger.info("Installation administrator %s created organization %s (%s)", actor_id, organization.id, clean_slug)
    return organization


def sanitize_slug(raw: str) -> str:
    """A slug a caller supplied, reduced the same way a derived one is."""
    slug = _NON_SLUG.sub("-", _INVISIBLE.sub("", raw or "").lower()).strip("-")[:SLUG_MAX_LENGTH].strip("-")
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="An organization slug needs at least one letter or digit.",
        )
    return slug


async def list_organizations(session: AsyncSession, *, actor_role: str | None) -> list[Organization]:
    """Every organization on this installation, oldest first.

    The one read here that no organization administrator may make. Oldest first
    rather than by name because the order a boot repair relies on is
    ``(created_at, id)`` - the install's organization is the oldest - and a list
    an administrator reads should put that one first for the same reason the
    resolver picks it.
    """
    _require_installation_administrator(actor_role)
    rows = await session.execute(select(Organization).order_by(Organization.created_at, Organization.id))
    return list(rows.scalars())


async def _organization_by_id(session: AsyncSession, organization_id: uuid.UUID | str) -> Organization | None:
    """One organization by id, or ``None`` - including for an id that is not one.

    Coerced here rather than handed to ``session.get`` as a string: the primary
    key is a ``GUID`` and a text value would be compared against it in whatever
    way the dialect happens to coerce, which on PostgreSQL is a cast error
    answering 500 for a malformed path parameter that should answer 404.
    """
    try:
        wanted = uuid.UUID(str(organization_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return await session.scalar(select(Organization).where(Organization.id == wanted).limit(1))


async def add_member(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID | str,
    user_email: str,
    actor_id: str | uuid.UUID | None = None,
    actor_role: str | None,
    actor_tenant_id: str | uuid.UUID | None = None,
    role: str | None = None,
    is_default: bool = False,
) -> Membership:
    """Add one person to one organization, leaving every other membership alone.

    404 for an organization or a user this installation does not have - both
    answered the same way, since "that company does not exist here" and "that
    person does not exist here" are equally none of a caller's business once
    they have been refused. 409 for a membership that already exists, which the
    unique pair makes a fact rather than a race. 422 for a role that is not one.

    The membership's role defaults to the person's own, which is what the model
    documents: belonging to an organization does not silently change what they
    may do. The default flag is set when the caller asks for it, or when this is
    the person's first membership and so there is nothing else for a token to
    name; setting it clears the person's other flags in the same transaction,
    because two defaults is the state the resolution rule exists to survive
    rather than one anybody wants.
    """
    from app.core.permissions import _resolve_role

    _require_installation_administrator(actor_role)

    organization = await _organization_by_id(session, organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That organization does not exist on this installation.",
        )

    email = (user_email or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A member is added by email address, and no address was given.",
        )
    user = await session.scalar(select(User).where(func.lower(User.email) == email).limit(1))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That user does not exist on this installation.",
        )

    membership_role = role if role is not None else user.role
    if _resolve_role(membership_role) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{membership_role!r} is not a role this installation knows.",
        )

    existing = await session.scalar(
        select(Membership.id).where(Membership.user_id == user.id, Membership.organization_id == organization.id)
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That user is already a member of that organization.",
        )

    # The person's first membership is the default one, because a token has to
    # name something and this is the only candidate. Any later one is not,
    # unless the caller says so: adding somebody to a second company must not
    # move them out of the first one's rows without being asked.
    has_any_membership = (
        await session.scalar(select(Membership.id).where(Membership.user_id == user.id).limit(1))
    ) is not None
    make_default = bool(is_default) or not has_any_membership

    if make_default:
        await session.execute(
            update(Membership)
            .where(Membership.user_id == user.id, Membership.is_default.is_(True))
            .values(is_default=False)
        )

    membership = Membership(
        user_id=user.id,
        organization_id=organization.id,
        role=membership_role,
        status="active",
        is_default=make_default,
    )
    try:
        async with session.begin_nested():
            session.add(membership)
            await session.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That user is already a member of that organization.",
        ) from None

    await audit_log(
        session,
        action=ACTION_MEMBER_ADDED,
        entity_type=MEMBERSHIP_ENTITY_TYPE,
        entity_id=str(membership.id),
        user_id=str(actor_id) if actor_id else None,
        details={
            "organization_id": str(organization.id),
            "organization_slug": organization.slug,
            "user_id": str(user.id),
            "user_email": user.email,
            "role": membership.role,
            "status": membership.status,
            "is_default": membership.is_default,
            "actor_tenant_id": str(actor_tenant_id) if actor_tenant_id else None,
        },
    )
    logger.info(
        "Installation administrator %s added user %s to organization %s as %s",
        actor_id,
        user.id,
        organization.id,
        membership.role,
    )
    return membership
