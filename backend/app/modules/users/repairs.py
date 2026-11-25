# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Boot-path data repairs owned by the users module.

Imported by :func:`app.core.data_repairs.discover_data_repairs`, which is what
makes the registration below take effect. Nothing else imports this file.

The one repair here moves an install from "the tenant is the calling user" to
"the tenant is an organization" (ADR 005). It is a boot repair rather than a step
inside ``v3325_organization_tenancy`` because this product does not run
``alembic upgrade head`` against an existing database: ``postgres_auto_migrate``
creates missing tables and stamps head, so a forward transformation written only
into a revision would never execute anywhere. The revision carries the schema and
the downgrade; the transformation lives here, where it runs on every boot and
therefore on every install shape - upgraded, freshly created, restored from a
backup taken before organizations existed.

Three properties are load-bearing and each has a test in
``tests/pg/test_tenancy_backfill_on_a_populated_database.py``:

* **Idempotent.** PostgreSQL reports rows *matched*, not rows *changed*, so an
  ``UPDATE ... WHERE tenant_id IS NULL`` whose subquery yields ``NULL`` re-matches
  the same row on every boot and reports progress forever while making none. Every
  statement below excludes the rows it cannot resolve, and the loops stop on a
  round that changed nothing.
* **Bounded.** Each pass touches at most :data:`_BATCH` rows of one table, so a
  repair that has to move a large install moves it in pieces rather than holding
  one unbounded update open across it.
* **Reversible.** Every value it overwrites is recorded in
  ``oe_users_tenant_backfill`` first, including ``NULL``, which is a previous value
  and not the absence of one. ``v3325_organization_tenancy.downgrade()`` replays
  that ledger before dropping the schema.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from app.core.data_repairs import DataRepair, register_data_repair
from app.core.tenant_scope import (
    DEFAULT_ORGANIZATION_NAME,
    DEFAULT_ORGANIZATION_SLUG,
    MEMBERSHIP_ACTIVE,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: Rows touched per pass, per table. Bounds a single update rather than the whole
#: repair, so the cost of a large install is spread over passes and a failure
#: part-way through leaves a database that is partly repaired and fully recorded
#: rather than one held open by a transaction nobody can reason about.
#: Rows one statement of the rewrite is allowed to touch. Bounded on purpose - see
#: the module docstring - but not small: each round re-scans the table to find the
#: rows it has not fixed yet, so the cost of a large install is the table scanned
#: once per round, and a batch of five thousand made a million-row table two hundred
#: rounds of that. Fifty thousand is twenty rounds, still one bounded statement at a
#: time, and measured fast enough that a boot can afford it. A hundred thousand rather
#: than fifty because each round re-scans for the rows it has not fixed yet, and the
#: slowest table measured - a wide one carrying several indexes - was the one that fell
#: short of the floor at the smaller bound.
_BATCH = 100_000

#: Stable application-defined key for ``pg_try_advisory_xact_lock``, so two workers
#: booting against the same database run this repair once between them rather than
#: racing over the same rows. Arbitrary, and must stay constant across releases.
_ADVISORY_LOCK_KEY = 573910446

_ORGANIZATION_TABLE = "oe_users_organization"
_MEMBERSHIP_TABLE = "oe_users_membership"
_LEDGER_TABLE = "oe_users_tenant_backfill"
_USER_TABLE = "oe_users_user"
_PROJECT_TABLE = "oe_projects_project"

#: The organization a membership resolves to when a person belongs to several and
#: none is flagged. Identical to the order
#: :meth:`app.modules.users.service.UserService._active_organization_id` uses at
#: token-mint time, and that is the point: the value the repair writes into a row
#: and the value a token later names have to be the same organization, or the two
#: halves of the tenancy work disagree about whose data this is.
_PREFERRED_ORGANIZATION = """
        (SELECT m.organization_id
           FROM {membership} m
          WHERE m.user_id = {owner}
            AND m.status = :active
          ORDER BY m.is_default DESC, m.created_at, m.id
          LIMIT 1)
""".format(membership=_MEMBERSHIP_TABLE, owner="{owner}")


async def _is_postgres(session: AsyncSession) -> bool:
    """Whether this session is talking to PostgreSQL.

    The repair is written in PostgreSQL: ``information_schema`` predicates,
    ``gen_random_uuid()``, ``ON CONFLICT``, and a row-level security model that
    only exists there. Every other dialect gets a no-op rather than an
    approximation, because a repair that half-worked on another database would be
    worse than one that says it did nothing.
    """
    bind = getattr(session, "bind", None)
    return bool(bind is not None and bind.dialect.name == "postgresql")


async def _has_table(session: AsyncSession, table: str) -> bool:
    """Whether the catalog knows this table.

    A repair runs on every boot, and a boot can happen against a database that
    ``create_all`` has not finished building. Asking the catalog is the only
    answer that is true at the moment it is needed: the ORM's own metadata knows
    what the code declares, not what this database has.
    """
    found = await session.scalar(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = :name LIMIT 1"),
        {"name": table},
    )
    return found is not None


async def _has_column(session: AsyncSession, table: str, column: str) -> bool:
    """Whether this table has this column, right now.

    Used for the two columns whose presence is not guaranteed by the time a boot
    repair runs: ``created_by``, which only 8 of the 31 tenant-carrying tables
    have, and ``oe_projects_project.organization_id``, which exists only once the
    schema for this change has been applied. Deriving the table list from the ORM
    and then asking the catalog per column keeps one source of truth for *which*
    tables and one for *what is there*.
    """
    found = await session.scalar(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = :table AND column_name = :column LIMIT 1"),
        {"table": table, "column": column},
    )
    return found is not None


async def _tenant_tables(session: AsyncSession) -> list[str]:
    """The tenant-carrying tables this database actually has.

    Taken from :func:`app.core.rls_setup.tenant_tables` - the same list the
    row-level policies are built from - so the repair and the policies can never
    disagree about which columns hold a tenant. Intersected with the catalog
    because that list is derived from the ORM, and a database mid-migration does
    not have all of it yet.
    """
    from app.core.rls_setup import tenant_tables
    from app.database import Base

    declared = tenant_tables(Base)
    present: list[str] = []
    for table in declared:
        if await _has_column(session, table, "tenant_id"):
            present.append(table)
    return present


async def _ensure_default_organization(session: AsyncSession) -> str | None:
    """The install's organization: adopt the oldest one, or create the default.

    Adopting rather than always creating is what makes this safe on a database
    that already has organizations - one created by an earlier boot, by an
    administrator, or by a token mint that belonged somebody to a new company. A
    second ``default`` row would split the install in two and the readiness report
    would not be the thing that told you.

    The insert collides on the unique slug on purpose when two boots race, and
    ``ON CONFLICT DO NOTHING`` followed by a re-read means the loser adopts the
    winner's row instead of failing a boot over a race nobody can see.
    """
    existing = await session.scalar(text(f"SELECT id FROM {_ORGANIZATION_TABLE} ORDER BY created_at, id LIMIT 1"))
    if existing is not None:
        return str(existing)

    await session.execute(
        text(
            f"INSERT INTO {_ORGANIZATION_TABLE} (id, name, slug, status) "
            f"VALUES (gen_random_uuid()::text, :name, :slug, 'active') "
            f"ON CONFLICT (slug) DO NOTHING"
        ),
        {"name": DEFAULT_ORGANIZATION_NAME, "slug": DEFAULT_ORGANIZATION_SLUG},
    )
    created = await session.scalar(
        text(f"SELECT id FROM {_ORGANIZATION_TABLE} WHERE slug = :slug"),
        {"slug": DEFAULT_ORGANIZATION_SLUG},
    )
    return str(created) if created is not None else None


async def _ensure_memberships(session: AsyncSession, organization_id: str) -> int:
    """One active membership for every user who has none at all.

    "None at all", not "none active": a user whose only membership is suspended
    was suspended by somebody, and creating an active one beside it would undo
    that decision from a boot path with no audit trail. Such a user is left alone,
    which is also what the token mint does - it refuses to name an organization
    for a membership that is not active.

    The role is copied from the user row, so belonging to an organization does not
    change what a person may do; and ``is_default`` is set because this is the
    only organization the install had when the membership was created.
    """
    result = await session.execute(
        text(
            f"INSERT INTO {_MEMBERSHIP_TABLE} "
            f"       (id, user_id, organization_id, role, status, is_default) "
            f"SELECT gen_random_uuid()::text, u.id, :organization_id, u.role, :active, true "
            f"  FROM {_USER_TABLE} u "
            f" WHERE NOT EXISTS (SELECT 1 FROM {_MEMBERSHIP_TABLE} m WHERE m.user_id = u.id) "
            f"ON CONFLICT (user_id, organization_id) DO NOTHING"
        ),
        {"organization_id": organization_id, "active": MEMBERSHIP_ACTIVE},
    )
    return int(result.rowcount or 0)


async def _record_in_ledger(session: AsyncSession, table: str, rows: list[tuple[str, str | None]]) -> None:
    """Write down what is about to be overwritten, before it is overwritten.

    The ledger is written first so that a boot interrupted between recording and
    updating leaves a database that can still be put back, rather than one whose
    previous values exist only in the memory of a process that died. The unique
    ``(table_name, row_id)`` pair makes re-recording a no-op, which is what lets
    the repair run on every boot without growing the ledger.
    """
    if not rows:
        return
    await session.execute(
        text(
            f"INSERT INTO {_LEDGER_TABLE} (id, table_name, row_id, previous_tenant_id) "
            f"VALUES (gen_random_uuid()::text, :table_name, :row_id, :previous) "
            f"ON CONFLICT (table_name, row_id) DO NOTHING"
        ),
        [{"table_name": table, "row_id": row_id, "previous": previous} for row_id, previous in rows],
    )


async def _repoint_table(session: AsyncSession, table: str) -> int:
    """Move one table's ``tenant_id`` from naming a user to naming an organization.

    Two shapes are repaired, and only two:

    * a ``tenant_id`` that *is* a user id - the single-tenant doctrine
      ``v231_contact_tenant_id`` established - becomes that user's organization;
    * a ``tenant_id`` that is ``NULL``, on the 8 tables that also carry
      ``created_by``, becomes the creator's organization.

    Nothing else is touched. In particular a ``NULL`` on a table with no
    ``created_by`` is left ``NULL``: ownership is not derivable, and inventing it
    would put somebody's rows in a company nobody said they were in. Those rows
    are what the readiness report counts as ``null_tenant_rows``, and they stay
    visible there until somebody who knows answers the question.

    Rows naming a user with no *active* membership are excluded in the WHERE
    clause rather than filtered afterwards. That exclusion is the idempotence
    guarantee: without it the subquery yields ``NULL`` for them, the update
    "matches" them on every boot, and PostgreSQL reports rows matched rather than
    rows changed - so a pass that repaired nothing would report that it had.
    """
    if not await _has_column(session, table, "tenant_id"):
        return 0

    # Two passes, and deliberately not one join with an OR in it: an OR between
    # two different equi-conditions cannot be hash-joined, so PostgreSQL would
    # plan it as a nested loop over every user for every row, which on the tables
    # this repair exists for is the difference between a boot that takes a second
    # and one that takes an hour. Each pass below is a single equality on one
    # column and can be hash-joined.
    changed = await _repoint_pass(session, table, owner_column="tenant_id", only_null=False)

    if await _has_column(session, table, "created_by"):
        changed += await _repoint_pass(session, table, owner_column="created_by", only_null=True)

    return changed


async def _repoint_pass(session: AsyncSession, table: str, *, owner_column: str, only_null: bool) -> int:
    """One pass over one table: rows owned by ``owner_column`` get an organization.

    ``only_null`` selects the second shape - rows whose ``tenant_id`` is still
    ``NULL``, attributed through ``created_by`` - as opposed to the first, rows
    whose ``tenant_id`` currently holds a user id.

    Each round is one statement, and that is a measured requirement rather than a
    style preference. This pass used to select a batch of candidates and then issue
    one ``UPDATE`` per row, which is a round trip per row: measured on a real
    cluster it rewrote between 2,645 and 2,994 rows per second, so a million-row
    install spent six minutes inside a boot that serves nothing until it finishes.
    The batch is still bounded - that property is load-bearing, and the module
    docstring claims it - but the ledger write and the rewrite now happen in the
    same statement as the selection that chose them, so a round costs one trip
    instead of five thousand and two.

    Ordering inside that statement is what makes the ledger honest. A data-modifying
    statement in ``WITH`` runs to completion whether or not the primary query reads
    its output, and every ``WITH`` sub-statement sees the same snapshot as the
    primary query, so the rows recorded are exactly the rows rewritten: there is no
    window in which one statement's idea of the candidates and the other's can
    differ, which two separate statements would have.

    The ``IS DISTINCT FROM`` predicate is the idempotence guarantee, and it now does
    the work the caller used to do in Python. A row already naming its organization
    is not a candidate, so a second round over the same table selects nothing and
    the loop stops - which also means a round can never return the rows the previous
    round already fixed, the failure mode that made the old Python-side filter
    necessary and that would have ended a pass early on a large table.
    """
    null_filter = "t.tenant_id IS NULL" if only_null else "t.tenant_id IS NOT NULL"
    preferred = f"({_PREFERRED_ORGANIZATION.format(owner='u.id')})::text"
    sql = text(
        f"""
        WITH batch AS (
            SELECT t.id AS row_id,
                   t.tenant_id AS previous,
                   {preferred} AS organization_id
              FROM {table} t
              JOIN {_USER_TABLE} u ON u.id::text = t.{owner_column}::text
             WHERE {null_filter}
               AND EXISTS (SELECT 1 FROM {_MEMBERSHIP_TABLE} m
                            WHERE m.user_id = u.id AND m.status = :active)
               AND {preferred} IS NOT NULL
               AND {preferred} IS DISTINCT FROM t.tenant_id::text
             LIMIT :batch
        ), recorded AS (
            INSERT INTO {_LEDGER_TABLE} (id, table_name, row_id, previous_tenant_id)
            SELECT gen_random_uuid()::text, :table_name, row_id::text, previous
              FROM batch
            ON CONFLICT (table_name, row_id) DO NOTHING
        )
        UPDATE {table} t
           SET tenant_id = batch.organization_id
          FROM batch
         WHERE t.id = batch.row_id
           AND t.tenant_id IS NOT DISTINCT FROM batch.previous
        """
    )

    changed = 0
    while True:
        # Terminates on a round that changed nothing rather than on a short round:
        # the update can legitimately touch fewer rows than the batch selected, and
        # stopping there would leave the rest of the table to the next boot.
        result = await session.execute(sql, {"active": MEMBERSHIP_ACTIVE, "table_name": table, "batch": _BATCH})
        rounds = int(result.rowcount or 0)
        if not rounds:
            return changed
        changed += rounds


async def _link_projects(session: AsyncSession) -> int:
    """Give each project the organization its owner belongs to.

    ``oe_projects_project`` has no ``tenant_id`` and therefore no row-level
    policy; this fills in the ``Organization -> Project`` edge of the ownership
    graph so that "whose project is this" is a question the project can answer
    instead of a two-hop question through the owner's membership, which changes
    meaning the moment an owner leaves the company.

    A project whose owner has no active membership is left ``NULL``. That is not
    an oversight and not a batch that failed: an orphan project belongs to nobody,
    and the readiness report is where that stays visible.
    """
    if not await _has_column(session, _PROJECT_TABLE, "organization_id"):
        return 0
    if not await _has_column(session, _PROJECT_TABLE, "owner_id"):
        return 0

    changed = 0
    while True:
        result = await session.execute(
            text(
                f"""
                UPDATE {_PROJECT_TABLE} AS p
                   SET organization_id = sub.organization_id
                  FROM (SELECT pr.id AS project_id,
                               {_PREFERRED_ORGANIZATION.format(owner="pr.owner_id")} AS organization_id
                          FROM {_PROJECT_TABLE} pr
                         WHERE pr.organization_id IS NULL
                           AND EXISTS (SELECT 1 FROM {_MEMBERSHIP_TABLE} m
                                        WHERE m.user_id = pr.owner_id AND m.status = :active)
                         LIMIT :batch) AS sub
                 WHERE p.id = sub.project_id
                   AND sub.organization_id IS NOT NULL
                """
            ),
            {"active": MEMBERSHIP_ACTIVE, "batch": _BATCH},
        )
        rows = int(result.rowcount or 0)
        changed += rows
        # ``organization_id IS NULL`` in the inner query would otherwise match the
        # same unresolvable project on every pass forever, and report it as
        # changed every time. The EXISTS above is what makes this terminate; this
        # is the belt to its braces.
        if rows < _BATCH:
            return changed


async def _run_tenant_organization_backfill(session: AsyncSession) -> int:
    """Move an install onto organizations. See the module docstring for the contract."""
    if not await _is_postgres(session):
        logger.debug("Tenant backfill skipped: not PostgreSQL")
        return 0

    for required in (_ORGANIZATION_TABLE, _MEMBERSHIP_TABLE, _LEDGER_TABLE, _USER_TABLE):
        if not await _has_table(session, required):
            # A database built before this change, or one mid-create_all. There is
            # nothing to repair into yet, and saying so is better than guessing.
            logger.debug("Tenant backfill skipped: %s does not exist yet", required)
            return 0

    # Two workers can boot against the same database at once, and this repair
    # rewrites the same rows in batches. Concurrent re-pointing is how two
    # transactions deadlock, and a deadlock here surfaces as a failed repair on a
    # boot that otherwise looked healthy - the runner catches it, logs it, and the
    # data stays wrong until the next start. The key is arbitrary but must stay
    # constant across releases, and it is not the auto-migration heal key: that lock
    # guards DDL and is waited on, this one guards a row rewrite and is not.
    #
    # The poll is the non-blocking form on purpose. A second worker never waits for
    # the first, because the work is idempotent and is already being done, so
    # returning without writing is the whole of what it owes - and a boot that could
    # be delayed by another worker's backfill is a boot that can be delayed at all.
    # The lock is transaction-scoped, so the runner's commit releases it and a crash
    # releases it with the connection.
    acquired = (
        await session.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})
    ).scalar_one()
    if not acquired:
        logger.info(
            "Data repair %r skipped: another worker holds the advisory lock and is running it now.",
            USERS_TENANT_ORGANIZATION_BACKFILL.repair_id,
        )
        return 0

    organization_id = await _ensure_default_organization(session)
    if organization_id is None:
        logger.warning("Tenant backfill could not establish an organization; nothing was rewritten")
        return 0

    changed = await _ensure_memberships(session, organization_id)

    for table in await _tenant_tables(session):
        changed += await _repoint_table(session, table)

    changed += await _link_projects(session)
    return changed


#: Nature ``always_wrong``: a ``tenant_id`` holding a user id never named a tenant
#: that anything could resolve ownership from, so replacing it with the
#: organization that user belongs to corrects a value that was never right rather
#: than migrating one that was. ``v3325_organization_tenancy`` is the revision
#: that creates the tables this repair writes into and whose ``downgrade()``
#: replays the ledger.
USERS_TENANT_ORGANIZATION_BACKFILL = register_data_repair(
    DataRepair(
        repair_id="users_tenant_organization_backfill",
        revision="v3325_organization_tenancy",
        summary="Move tenant_id columns from naming a user to naming that user's organization",
        run=_run_tenant_organization_backfill,
        nature="always_wrong",
    )
)
