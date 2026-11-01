# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""User ORM models.

Tables:
    oe_users_user - registered users
    oe_users_api_key - API keys for programmatic access
    oe_users_session - login sessions, so one can be revoked without the rest
    oe_users_organization - the tenant (ADR 005)
    oe_users_membership - which users belong to which organization
    oe_users_tenant_backfill - the ledger of what the boot repair rewrote, so a
        downgrade can put every value back
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import GUID, Base


class User(Base):
    """Application user."""

    __tablename__ = "oe_users_user"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="editor")
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Tracks when the password was last changed. Tokens issued (`iat`) before
    # this timestamp are considered invalid - see dependencies.get_current_user.
    # Used to invalidate existing sessions on password change.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # GDPR Art. 17 erasure marker. When the account holder self-erases via
    # DELETE /api/v1/users/me the row is anonymised in place (PII nulled,
    # password hash invalidated, is_active flipped False) rather than hard
    # deleted, so foreign-key references (projects, activity, audit) survive.
    # This timestamp records when that happened; NULL means a live account.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # OIDC subject identifier (Keycloak / any OIDC provider). Set on first
    # OIDC login and used to match returning OIDC users to local accounts.
    oidc_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    oidc_issuer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_: Mapped[dict] = mapped_column(  # type: ignore[assignment]
        "metadata",
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Regional preferences
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC", server_default="UTC")
    measurement_system: Mapped[str] = mapped_column(
        String(20), nullable=False, default="metric", server_default="metric"
    )
    # "auto" means the paper size follows the country of the project the
    # document belongs to, resolved by app.core.paper_size. The old default of
    # "A4" was not a neutral start: it is a real choice a European user makes
    # on purpose, so "nobody chose" and "chose A4" were the same row and no
    # generator could have told a US account apart from a German one even once
    # it started reading this column. server_default stays "A4" for the reason
    # spelled out under date_format below - changing it alters the table DDL
    # and needs a migration for no functional gain, because ORM inserts always
    # supply the Python-side default. Accounts created before this still carry
    # "A4" and keep rendering A4, which is what they rendered yesterday.
    paper_size: Mapped[str] = mapped_column(String(10), nullable=False, default="auto", server_default="A4")
    # "auto" means numbers follow the interface language, the same neutral
    # start the date_format below already takes. The old default handed every
    # new account in the world German grouping, and because the column is
    # free-form nothing downstream could tell that seeded "1.234,56" apart from
    # a reader who deliberately picked German. server_default stays "1.234,56"
    # for the reason spelled out under date_format: changing it alters the
    # table DDL and needs a migration for no functional gain, because ORM
    # inserts always supply the Python-side default. Accounts created before
    # this still carry "1.234,56"; the frontend store treats that exact value
    # as "never chose" so their rendering does not change.
    number_format: Mapped[str] = mapped_column(String(20), nullable=False, default="auto", server_default="1.234,56")
    # "auto" means the UI follows the interface language when rendering dates.
    # New accounts start there so the stored value is only ever an order the
    # user actually picked. server_default stays "DD.MM.YYYY" for the same
    # reason as currency_code below - changing it would alter the table DDL and
    # require a migration for no functional gain, because ORM inserts always
    # supply the Python-side default. Accounts created before the automatic
    # option still carry "DD.MM.YYYY"; the frontend store treats that exact
    # value as "never chose" so their rendering does not change.
    date_format: Mapped[str] = mapped_column(String(20), nullable=False, default="auto", server_default="DD.MM.YYYY")
    # No EUR bias for new accounts: empty string means "not chosen yet" and
    # the UI falls back to the project currency / local preference store.
    # server_default stays "EUR" on purpose - changing it would alter the
    # table DDL and require an alembic migration for zero functional gain
    # (ORM inserts always supply the Python-side default).
    currency_code: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="EUR")

    @property
    def digit_system(self) -> str:
        """The digit shapes the reader chose (``auto``, ``latn``, ``arab``, ``arabext``).

        Kept in ``metadata`` rather than a column of its own; see
        ``UserService.update_preferences``.
        """
        value = (self.metadata_ or {}).get("digit_system")
        return value if isinstance(value, str) and value else "auto"

    # Relationships
    # Never loaded implicitly. Every authenticated request re-reads the caller
    # with session.get(User), and "selectin" made that a second SELECT against
    # oe_users_api_key for a collection nothing reads; keys are listed through
    # APIKeyRepository. passive_deletes leaves removal of a deleted user's keys
    # to the FK's ON DELETE CASCADE, which the table has carried since it was
    # first created, instead of loading them to delete one by one.
    api_keys: Mapped[list["APIKey"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"


class APIKey(Base):
    """API key for programmatic access."""

    __tablename__ = "oe_users_api_key"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("oe_users_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)  # First 8 chars for identification
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    permissions: Mapped[list] = mapped_column(  # type: ignore[assignment]
        JSON, nullable=False, default=list, server_default="[]"
    )

    # Relationships
    user: Mapped[User] = relationship(back_populates="api_keys")

    def __repr__(self) -> str:
        return f"<APIKey {self.key_prefix}... ({self.name})>"


class UserSession(Base):
    """One login session, named by the ``sid`` claim its tokens carry.

    This row is what makes revoking a single session expressible. Before it,
    every token carried a ``jti`` that nothing read, so an issued credential
    had a name but no record, and the only way to end one session was to
    rotate the signing secret, which ends everybody's. Revocation now has
    something to point at: the access token and the refresh token minted
    together carry the same ``sid``, so refusing this row refuses the pair and
    every pair rotated out of it.

    Revocation sets ``revoked_at``; it never deletes the row. That is the
    whole reason a "revoked" column exists rather than just a missing row: a
    token whose session cannot be found is honoured (see
    ``reject_revoked_session`` in ``app.dependencies`` for why), so deleting
    the row would *undo* the revocation instead of enforcing it. Pruning is
    therefore only ever allowed to touch rows already past ``expires_at``,
    which no live credential can be pointing at.
    """

    __tablename__ = "oe_users_session"

    # The token claim, ``uuid4().hex`` (32 chars); sized with headroom so a
    # future change of generator does not need a migration. Unique because it
    # names one session, indexed because every authenticated request looks a
    # token up by it.
    sid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("oe_users_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The refresh horizon, not the access horizon: the session stays alive as
    # long as its refresh token can still mint, which is what a person means
    # by "I am still logged in on that laptop". Also the pruning boundary.
    # Indexed here as well as in the migration so the two descriptions of this
    # table stay identical; declared in only one of them, the next
    # autogenerated revision would propose dropping the index.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Written on refresh rather than on every request: it exists so a person
    # can recognise which session to end, and paying a write on each
    # authenticated call to sharpen that timestamp is not worth it.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        state = "revoked" if self.revoked_at else "active"
        return f"<UserSession {self.sid[:8]}... ({state})>"


class Organization(Base):
    """The tenant.

    ADR 005. This is the entity every ``tenant_id`` column in the schema names,
    and the value the ``tenant_id`` claim in an access token carries. Before it
    existed there was nothing for those columns to point at, which is why the
    resolver answered with a user id and why the readiness blocker is called
    ``tenant_is_user``.

    It has no ``tenant_id`` column of its own: an organization is not inside a
    tenant, it is one. That is not an oversight to be completed later - a tenant
    column here would put this table in ``tenant_tables()`` and give it a policy
    comparing its own identity with the caller's.
    """

    __tablename__ = "oe_users_organization"

    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Unique, and the reason the boot repair and the token mint can both create
    # an organization without coordinating: two boots racing to establish the
    # install's organization collide here on purpose, and the loser reads the
    # winner instead of failing somebody's login over a race they cannot see.
    # Also how a caller with no membership in a multi-organization install gets
    # an organization of their own exactly once, however often they retry.
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # ``active`` / ``suspended``. Suspended is a fact about the organization, and
    # is kept separate from a suspended *membership*, which is a fact about one
    # person's place in it.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    def __repr__(self) -> str:
        return f"<Organization {self.slug} ({self.status})>"


class Membership(Base):
    """A user's place in an organization.

    The middle edge of the ownership graph ``Organization -> Membership -> User``.
    It is a table rather than a column on ``User`` because the graph has to
    support a person belonging to more than one organization, and because the
    role a person holds can differ per organization: an admin of one company is
    not an admin of another, and a ``role`` column on ``User`` would say they
    were.

    The membership, not the user row, is what a token's ``tenant_id`` claim is
    resolved from, and only an ``active`` one counts.
    """

    __tablename__ = "oe_users_membership"
    __table_args__ = (
        # One membership per (user, organization). Makes a concurrent duplicate
        # impossible rather than merely unlikely, so the mint path can treat a
        # collision as "the row that matters exists" and carry on.
        UniqueConstraint("user_id", "organization_id", name="uq_oe_users_membership_user_org"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("oe_users_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("oe_users_organization.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Copied from the user's own role when the membership is created, so
    # belonging to an organization does not silently change what they may do.
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="editor")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # Which organization a token names when a person belongs to several and has
    # not chosen one. Exactly one per user is the intent; nothing enforces it,
    # because the resolution is deterministic without it (flagged first, then
    # oldest, then by id) and refusing a login over two flags would be worse than
    # picking one the same way every time.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<Membership user={self.user_id} org={self.organization_id} ({self.status})>"


class TenantBackfillEntry(Base):
    """One row of the ledger of what the tenant backfill rewrote.

    Written by :mod:`app.modules.users.repairs` for every row whose ``tenant_id``
    it changes, holding the value that was there before - including ``NULL``,
    which is a value and not the absence of one. It exists so that
    ``v3325_organization_tenancy.downgrade()`` can put a populated database back
    the way it was instead of dropping the tables and taking the evidence with
    them, and so an operator can answer "what did that boot do to my data?" from
    the database rather than from a log file that has already rotated.

    The unique ``(table_name, row_id)`` pair is what makes the repair safe to run
    on every boot: a row already in the ledger has already been accounted for.
    """

    __tablename__ = "oe_users_tenant_backfill"
    __table_args__ = (UniqueConstraint("table_name", "row_id", name="uq_oe_users_tenant_backfill_row"),)

    table_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # ``VARCHAR(36)``: every primary key in this schema is a ``GUID``.
    row_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # ``VARCHAR(64)`` because that is the widest ``tenant_id`` in the schema
    # (``oe_governance_catalogue`` and ``oe_procurement_concept_framework``), and
    # a ledger that truncated the value it was keeping would restore the wrong
    # one. Nullable because ``NULL`` is a previous value worth recording.
    previous_tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<TenantBackfillEntry {self.table_name}:{self.row_id} was {self.previous_tenant_id!r}>"
