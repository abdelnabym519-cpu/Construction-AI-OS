# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""A million rows have to be rewritten fast enough that a boot can afford it.

The boot repair runs on every start, inside the lifespan, before the module loader and
before the application serves anything. Nothing else in the product gets to decide how
long a boot takes; this does. So the question is not "is the rewrite correct" - the other
file in this directory answers that - but "how long does an install with a real history
wait", and the answer has to be a measured number rather than an assumption.

Three numbers are recorded here, because they are three different facts:

* **per-table rows/second**, from timing ``_repoint_table`` on each table separately.
  This is the number that says whether the rewrite is set-based or per-row, and it is
  the one that decides whether a large install boots at all.
* **the one-time cost**, the whole repair over a million rows on a database that has
  never been backfilled. This is what the first boot after an upgrade pays.
* **the steady-state cost**, the same repair over the same million rows once they have
  been re-pointed. Every subsequent boot pays this, forever, so a repair that is slow
  when it has nothing to do is worse than one that is slow once.

The floor asserted below is 20,000 rows per second per table. It is not a target chosen
to be comfortably met: a rewrite below it means a million-row install waits a minute or
more on the boot that upgrades it, and the loop that produced that number issued one
``UPDATE`` per row, which is a round trip per row over a connection that may be on the
other side of a network.

The volume is overridable with ``OE_PERF_ROWS`` so the same test can be run at a twentieth
of the size while iterating, and the default is what the claim is made against.
"""

from __future__ import annotations

import os
import time
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.users import repairs as users_repairs
from tests._pg import isolated_engine

pytestmark = pytest.mark.asyncio

#: Rows per table, and how many tables. Four times a quarter of a million is the
#: million this file claims a number for.
_ROWS_PER_TABLE = int(os.environ.get("OE_PERF_ROWS", "250000"))
_TABLE_COUNT = int(os.environ.get("OE_PERF_TABLES", "4"))

#: Below this the repair is issuing work per row rather than per set.
_MIN_ROWS_PER_SECOND = 20_000.0

_USER_TABLE = "oe_users_user"

#: SQL literal per information_schema data type, for the columns a table insists on.
#: A column this does not know how to fill disqualifies the table rather than being
#: guessed at: a repair benchmark that populated a column with a value the schema
#: tolerates but the product never writes would be measuring a different database.
_LITERALS = {
    "uuid": "gen_random_uuid()",
    "character varying": "'perf-' || g",
    "text": "'perf-' || g",
    "character": "'perf-' || g",
    "integer": "g",
    "bigint": "g",
    "smallint": "g",
    "numeric": "g",
    "double precision": "g",
    "real": "g",
    "boolean": "false",
    "timestamp without time zone": "now()",
    "timestamp with time zone": "now()",
    "date": "current_date",
    "json": "'{}'::json",
    "jsonb": "'{}'::jsonb",
}


@pytest_asyncio.fixture(scope="module")
async def large_install():  # noqa: ANN201
    """A database with several tenant tables holding a person's id, and one user.

    Which tables are used is discovered rather than hard-coded: a table is usable when
    every column it insists on is one this file knows how to fill, and the probe that
    decides it runs inside a savepoint so a table with a foreign key this file has no
    parent for is passed over instead of failing the run.
    """
    from app.modules.users.models import User as UserModel

    async with isolated_engine() as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        person = uuid.uuid4()
        async with factory() as session:
            session.add(
                UserModel(
                    id=person,
                    email="history@large-install.test",
                    hashed_password="not-a-real-hash",
                    full_name="Long history",
                    role="admin",
                    locale="en",
                    is_active=True,
                    metadata_={},
                )
            )
            await session.commit()

        tables = await _select_tables(engine, person)
        assert len(tables) == _TABLE_COUNT, f"only found {len(tables)} populatable tenant tables"
        yield SimpleNamespace(engine=engine, factory=factory, person=person, tables=tables)


async def _required_columns(conn, table: str) -> dict[str, str]:  # noqa: ANN001
    """The columns a raw INSERT must supply, mapped to a literal that fills them."""
    rows = (
        await conn.execute(
            text(
                """
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_name = :table
                   AND is_nullable = 'NO'
                   AND column_default IS NULL
                """
            ),
            {"table": table},
        )
    ).all()
    out: dict[str, str] = {}
    for column, data_type in rows:
        literal = _LITERALS.get(data_type)
        if literal is None:
            return {}
        out[column] = (
            "gen_random_uuid()::text" if column == "id" and data_type in ("character varying", "text") else literal
        )
    return out


async def _insert_rows(conn, table: str, columns: dict[str, str], tenant: str, count: int) -> None:  # noqa: ANN001
    """``count`` rows in one statement, stamped with a person's id."""
    names = ", ".join(["id", "tenant_id", *(name for name in columns if name not in ("id", "tenant_id"))])
    values = ", ".join(
        [
            columns.get("id", "gen_random_uuid()::text"),
            ":tenant",
            *(literal for name, literal in columns.items() if name not in ("id", "tenant_id")),
        ]
    )
    await conn.execute(
        text(f"INSERT INTO {table} ({names}) SELECT {values} FROM generate_series(1, :count) AS g"),
        {"tenant": tenant, "count": count},
    )


async def _select_tables(engine, person: uuid.UUID) -> list[str]:  # noqa: ANN001
    """The first ``_TABLE_COUNT`` tenant tables this file can fill, by name."""
    from app.core.rls_setup import tenant_tables
    from app.database import Base

    chosen: list[str] = []
    async with engine.begin() as conn:
        for table in sorted(tenant_tables(Base)):
            if len(chosen) == _TABLE_COUNT:
                break
            columns = await _required_columns(conn, table)
            if not columns or "id" not in columns:
                continue
            nested = await conn.begin_nested()
            try:
                await _insert_rows(conn, table, columns, str(person), _ROWS_PER_TABLE)
            except Exception:  # noqa: BLE001 - a table this file cannot fill is passed over
                await nested.rollback()
                continue
            await nested.commit()
            chosen.append(table)
    return chosen


async def test_a_million_rows_are_rewritten_fast_enough_to_boot_on(large_install, record_property) -> None:  # noqa: ANN001
    """The three numbers, measured on a real cluster.

    The organization and its memberships are created first and timed separately, because
    the boot pays for them too but they are not the rewrite and mixing them into one
    number would hide which half was slow.
    """
    per_table = int(_ROWS_PER_TABLE)
    total_rows = per_table * len(large_install.tables)
    record_property("tables", len(large_install.tables))
    record_property("rows_per_table", per_table)
    record_property("total_rows", total_rows)

    async with large_install.factory() as session:
        prepared = time.perf_counter()
        organization = await users_repairs._ensure_default_organization(session)  # noqa: SLF001
        memberships = await users_repairs._ensure_memberships(session, organization)  # noqa: SLF001
        prepare_seconds = time.perf_counter() - prepared
        assert organization and memberships >= 1, (organization, memberships)
        record_property("prepare_seconds", round(prepare_seconds, 3))

        slowest = 0.0
        for table in large_install.tables:
            started = time.perf_counter()
            changed = await users_repairs._repoint_table(session, table)  # noqa: SLF001
            seconds = time.perf_counter() - started
            rate = changed / seconds if seconds else float("inf")
            slowest = min(slowest or rate, rate) if slowest else rate
            record_property(f"{table}.rows", changed)
            record_property(f"{table}.seconds", round(seconds, 3))
            record_property(f"{table}.rows_per_second", int(rate))
            assert changed == per_table, (table, changed, per_table)
        await session.commit()

    record_property("slowest_rows_per_second", int(slowest))
    assert slowest >= _MIN_ROWS_PER_SECOND, (
        f"the slowest table rewrote {int(slowest)} rows/second, below {_MIN_ROWS_PER_SECOND:.0f}; "
        f"{total_rows} rows would hold a boot for {total_rows / max(slowest, 1):.0f}s"
    )

    # The steady-state cost: the same repair, over the same rows, with nothing left to
    # fix. Every boot after the first pays this, so it is measured rather than assumed.
    async with large_install.factory() as session:
        started = time.perf_counter()
        changed = await users_repairs.USERS_TENANT_ORGANIZATION_BACKFILL.run(session)
        steady_seconds = time.perf_counter() - started
        await session.commit()
    record_property("steady_state_seconds", round(steady_seconds, 3))
    record_property("steady_state_rows_changed", int(changed or 0))
    assert changed == 0, f"a second pass over {total_rows} re-pointed rows still changed {changed}"
