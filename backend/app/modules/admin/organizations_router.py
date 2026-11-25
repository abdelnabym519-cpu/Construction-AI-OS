# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Admin - organization administration (installation administrator only).

Three routes, mounted where every other admin route is, at ``/api/v1/admin``:

    POST /api/v1/admin/organizations                             create one
    GET  /api/v1/admin/organizations                             list them all
    POST /api/v1/admin/organizations/{organization_id}/members   add a person

They exist because nothing else could. An organization is the tenant (ADR 005),
and the only two places that ever constructed one were both inside the login
path, each reachable only from the state the other produces: the install's
single organization is created when there are none, and a caller's own
organization is created only when there is already more than one. So an install
booted into one organization and could not reach two, which left project
participant grants (ADR 006) with nobody to invite.

Every route carries ``RequireRole("admin")``, and
:mod:`app.modules.admin.organizations_service` checks the same thing again
against the caller's database role. The service's own docstring says why an
*organization's* administrator is not enough, and why the list route in
particular is one no organization administrator may call: in a multi-tenant
install the names of the other companies are confidential, and a list is an
enumeration.

Both spellings of each path are registered, trailing slash and none, because
FastAPI answers the other one with a 307 and a client that does not follow
redirects on a POST - httpx by default - would report a redirect where the
product meant an answer. The slash-less twin is out of the schema so the
OpenAPI document does not carry each operation twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.dependencies import CurrentTenantId, CurrentUserId, CurrentUserPayload, RequireRole, SessionDep
from app.modules.admin.organizations_service import add_member, create_organization, list_organizations

router = APIRouter()


class OrganizationCreateRequest(BaseModel):
    """The name of the organization to create, and optionally its address.

    ``name`` is sanitized before it is stored, compared and slugged - invisible
    characters out, whitespace collapsed - so no length is enforced here: a name
    padded with zero-width characters could pass a limit checked before
    sanitizing and then not fit the column. The service answers 422 with the
    length it measured.
    """

    name: str = Field(description="The company's name. Sanitized, and unique per installation.")
    slug: str | None = Field(
        default=None,
        description=(
            "The address to give it. Derived from the name when omitted, which is what makes a "
            "retried create collide on the same value and answer 409 rather than leave a second "
            "organization behind."
        ),
    )


class OrganizationRead(BaseModel):
    """One organization, as the installation administrator sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime | None = None


class OrganizationListResponse(BaseModel):
    """Every organization on this installation, oldest first."""

    items: list[OrganizationRead]
    total: int


class MemberAddRequest(BaseModel):
    """Add one person to one organization.

    ``role`` defaults to the person's own, which is what the membership model
    documents: belonging to an organization does not silently change what they
    may do. ``is_default`` decides which organization their next token names
    when they belong to several; it is set anyway when this is their first
    membership, because a token has to name something. Neither flag removes a
    membership the person already has - there is no move here, only an addition.
    """

    user_email: EmailStr = Field(description="The person to add, by the email address they sign in with.")
    role: str | None = Field(default=None, description="Their role in this organization. Their own role when omitted.")
    is_default: bool = Field(
        default=False,
        description="Make this the organization their token names. Implied when they have no membership yet.",
    )


class MembershipRead(BaseModel):
    """One membership, as the installation administrator sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    status: str
    is_default: bool


@router.post(
    "/organizations/",
    response_model=OrganizationRead,
    status_code=201,
    summary="Create an organization",
    description=(
        "Create a tenant. Installation administrator only. 409 when the installation already has an "
        "organization of that name or that address, 422 when the name has nothing visible in it or is "
        "too long for the column. Writes one audit row naming who created it."
    ),
    dependencies=[Depends(RequireRole("admin"))],
)
@router.post(
    "/organizations",
    response_model=OrganizationRead,
    status_code=201,
    include_in_schema=False,
    dependencies=[Depends(RequireRole("admin"))],
)
async def create_organization_endpoint(
    data: OrganizationCreateRequest,
    session: SessionDep,
    user_id: CurrentUserId,
    payload: CurrentUserPayload,
    tenant_id: CurrentTenantId = None,
) -> OrganizationRead:
    """Create one organization."""
    organization = await create_organization(
        session,
        name=data.name,
        slug=data.slug,
        actor_id=user_id,
        actor_role=payload.get("role"),
        actor_tenant_id=tenant_id,
    )
    return OrganizationRead.model_validate(organization)


@router.get(
    "/organizations/",
    response_model=OrganizationListResponse,
    summary="List every organization on this installation",
    description=(
        "Installation administrator only, and deliberately not reachable by an administrator of one "
        "organization: the names of the other companies on a multi-tenant install are confidential, "
        "and a list is an enumeration. Oldest first, which puts the install's own organization first "
        "for the same reason the tenant resolver picks it."
    ),
    dependencies=[Depends(RequireRole("admin"))],
)
@router.get(
    "/organizations",
    response_model=OrganizationListResponse,
    include_in_schema=False,
    dependencies=[Depends(RequireRole("admin"))],
)
async def list_organizations_endpoint(
    session: SessionDep,
    payload: CurrentUserPayload,
) -> OrganizationListResponse:
    """Every organization, oldest first."""
    organizations = await list_organizations(session, actor_role=payload.get("role"))
    return OrganizationListResponse(
        items=[OrganizationRead.model_validate(organization) for organization in organizations],
        total=len(organizations),
    )


@router.post(
    "/organizations/{organization_id}/members/",
    response_model=MembershipRead,
    status_code=201,
    summary="Add a person to an organization",
    description=(
        "Installation administrator only. Adds a membership and removes nothing: a person may belong "
        "to several organizations, and this is not a move. 404 when the installation has neither that "
        "organization nor that user, 409 when the person is already a member, 422 for a role this "
        "installation does not know. Writes one audit row naming the organization, the person and "
        "the role."
    ),
    dependencies=[Depends(RequireRole("admin"))],
)
@router.post(
    "/organizations/{organization_id}/members",
    response_model=MembershipRead,
    status_code=201,
    include_in_schema=False,
    dependencies=[Depends(RequireRole("admin"))],
)
async def add_organization_member_endpoint(
    organization_id: uuid.UUID,
    data: MemberAddRequest,
    session: SessionDep,
    user_id: CurrentUserId,
    payload: CurrentUserPayload,
    tenant_id: CurrentTenantId = None,
) -> MembershipRead:
    """Add one person to one organization."""
    membership = await add_member(
        session,
        organization_id=organization_id,
        user_email=data.user_email,
        role=data.role,
        is_default=data.is_default,
        actor_id=user_id,
        actor_role=payload.get("role"),
        actor_tenant_id=tenant_id,
    )
    return MembershipRead.model_validate(membership)
