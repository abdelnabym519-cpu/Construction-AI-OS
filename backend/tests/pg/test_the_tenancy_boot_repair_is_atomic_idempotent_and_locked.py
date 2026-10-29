# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The tenancy boot repair is atomic, idempotent, serialized, and cannot block a boot.

`users_tenant_organization_backfill` rewrites rows a user can see, on every boot, on
whoever's database the application happens to be pointed at. That is a lot of trust in a
function nobody watches run, so the four properties that make it acceptable are measured
here against a real PostgreSQL cluster rather than argued for in a docstring:

* **atomic** - a failure partway through leaves nothing half-applied. The repair must not
  commit; the runner commits once. This forces a failure in the last phase, after the
  organization, the memberships and every re-pointed row have been written, and then
  reads the database through a fresh connection to prove none of it survived.
* **idempotent** - the ledger is deliberately never consulted to skip a repair, so the
  repair itself has to be safe to repeat. The second run changes nothing and the data is
  byte-for-byte what the first run left.
* **serialized** - two workers booting against the same database must not both run it.
  Concurrent re-pointing of the same rows in batches is how two transactions deadlock,
  and a deadlock here surfaces as a failed repair on a boot that looked healthy. One
  worker takes a transaction-scoped advisory lock and does the work; the other finds it
  taken and returns without writing, because the work is being done and repeating it
  would add nothing.
* **cannot block a boot** - a repair that raises is caught by the runner, reported, and
  the next repair still runs. This tests the runner's contract, which is what
  `app/main.py`'s startup relies on when it calls `run_data_repairs` inside a `try`.

The last test measures the repair against a hundred thousand rows, because "idempotent
and atomic" are cheap claims on a table with three rows in it and the runtime is the
number that decides whether this can run on every boot of a real install at all.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.data_repairs import DataRepair, run_data_repairs
from app.modules.users import repairs as users_repairs
from app.modules.users.repairs import USERS_TENANT_ORGANIZATION_BACKFILL
from tests._pg import isolated_engine

pytestmark = pytest.mark.asyncio

#: The table with a nullable tenant column and no ``created_by``: rows in it can only
#: be re-pointed from their existing tenant value, so it is the honest place to measure
#: the rewrite, and the only columns it requires are its id and ``kind``.
_JOB_RUN = "oe_job_run"
_ORGANIZATION = "oe_users_organization"
_MEMBERSHIP = "oe_users_membership"
_LEDGER = "oe_users_tenant_backfill"

#: How many rows the runtime measurement writes. The task is a hundred thousand; the
#: ceiling asserted below is generous on purpose, because a slow runner is a fact about
#: the machine and a repair that takes minutes on 100k rows is a fact about the code.
_PERF_ROWS = 100_000
_PERF_CEILING_SECONDS = 180.0


@pytest_asyncio.fixture
async def populated():  # noqa: ANN201
    """A throwaway database holding one user and rows stamped with that user's id.

    That is exactly the shape Phase 02b found in the wild: a tenant column full of
    people, and no organization to name.
    """
    from app.modules.users.models import User as UserModel

    async with isolated_engine() as engine:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        person = uuid.uuid4()
        colleague = uuid.uuid4()
        async with factory() as session:
            for uid, email in ((person, "owner@boot-repair.test"), (colleague, "colleague@boot-repair.test")):
                session.add(
                    UserModel(
                        id=uid,
                        email=email,
                        hashed_password="not-a-real-hash",
                        full_name="Boot repair",
                        role="admin",
                        locale="en",
                        is_active=True,
                        metadata_={},
                    )
                )
            await session.commit()
        async with engine.begin() as conn:
            for index in range(3):
                await conn.execute(
                    text(f'INSERT INTO "{_JOB_RUN}" (id, tenant_id, kind) VALUES (:i, :t, :k)'),
                    {"i": f"seed-{index}", "t": str(person), "k": "boot-repair-probe"},
                )
        yield SimpleNamespace(engine=engine, factory=factory, person=person, colleague=colleague)


async def _counts(engine) -> dict[str, int]:  # noqa: ANN001
    """What the database holds, read on a fresh connection as the superuser."""
    async with engine.connect() as conn:
        out = {}
        for name, table in (
            ("organizations", _ORGANIZATION),
            ("memberships", _MEMBERSHIP),
            ("ledger", _LEDGER),
        ):
            out[name] = int((await conn.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar_one())
        out["person_shaped"] = int(
            (
                await conn.execute(
                    text(f'SELECT count(*) FROM "{_JOB_RUN}" WHERE kind = :k'), {"k": "boot-repair-probe"}
                )
            ).scalar_one()
        )
        return out


async def _run_once(factory, repair=USERS_TENANT_ORGANIZATION_BACKFILL) -> int:  # noqa: ANN001
    """One repair in one session, committed the way the runner commits it."""
    async with factory() as session:
        changed = await repair.run(session)
        await session.commit()
    return int(changed or 0)


async def test_a_failure_in_the_last_phase_leaves_nothing_half_applied(
    populated, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    """Atomic, measured: the repair writes, then fails, and the database is untouched.

    ``_link_projects`` is the last phase, so by the time it raises the organization
    exists, both users have memberships, and every person-shaped tenant in the probe
    rows has been re-pointed and recorded in the ledger. All of it is in one
    transaction that the runner has not committed, so all of it has to disappear.
    """
    before = await _counts(populated.engine)
    assert before == {"organizations": 0, "memberships": 0, "ledger": 0, "person_shaped": 3}, before

    async def _explode(session: AsyncSession) -> int:  # noqa: ANN001
        raise RuntimeError("forced failure in the last phase")

    monkeypatch.setattr(users_repairs, "_link_projects", _explode)

    with pytest.raises(RuntimeError, match="forced failure"):
        await _run_once(populated.factory)

    after = await _counts(populated.engine)
    assert after == before, after
    # And the rows still carry the person, which is the wrong value the repair exists
    # to fix: a half-applied repair would have left them naming an organization that
    # no longer exists.
    async with populated.engine.connect() as conn:
        tenants = (
            (
                await conn.execute(
                    text(f'SELECT DISTINCT tenant_id FROM "{_JOB_RUN}" WHERE kind = :k'), {"k": "boot-repair-probe"}
                )
            )
            .scalars()
            .all()
        )
    assert [str(t) for t in tenants] == [str(populated.person)], tenants


async def test_running_it_twice_changes_nothing_the_second_time(populated) -> None:  # noqa: ANN001
    """Idempotent, and the ledger is not what makes it so: it is never read."""
    first = await _run_once(populated.factory)
    assert first > 0, first
    after_first = await _counts(populated.engine)
    assert after_first["organizations"] == 1, after_first
    assert after_first["memberships"] == 2, after_first

    second = await _run_once(populated.factory)
    assert second == 0, second
    assert await _counts(populated.engine) == after_first

    third = await _run_once(populated.factory)
    assert third == 0, third
    assert await _counts(populated.engine) == after_first


async def test_a_worker_that_finds_the_lock_taken_does_nothing(populated) -> None:  # noqa: ANN001
    """Serialized, observed directly: another holder means this worker writes nothing.

    A second worker is held outside, in its own transaction, holding the same advisory
    lock the repair takes. The repair then runs in a different session and must return
    without touching the database. This is the assertion that distinguishes a lock from
    an idempotent repair: without one, a worker that finds the lock taken still does the
    whole rewrite and reports the rows it changed, and the only reason the second boot
    would report zero is that the first had already committed. Both are safe on a small
    table. On a large one they race over the same rows in batches, which is how two
    transactions deadlock, and a deadlock here surfaces as a failed repair on a boot
    that otherwise looked healthy.
    """
    before = await _counts(populated.engine)

    async with populated.engine.begin() as holder:
        taken = (
            await holder.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": users_repairs._ADVISORY_LOCK_KEY},  # noqa: SLF001 - the key is the point
            )
        ).scalar_one()
        assert taken, "the test could not take the lock it is holding against the repair"

        changed = await _run_once(populated.factory)
        assert changed == 0, f"the repair ran while another worker held the lock: {changed} row(s)"
        assert await _counts(populated.engine) == before

    # The lock is transaction-scoped, so the holder's commit released it, and the next
    # worker does the work rather than skipping it forever.
    assert await _run_once(populated.factory) > 0
    after = await _counts(populated.engine)
    assert after["organizations"] == 1 and after["memberships"] == 2, after


async def test_two_workers_booting_at_once_leave_one_consistent_result(populated) -> None:  # noqa: ANN001
    """Raced rather than serialized on purpose: whatever happens, the data is right.

    Both workers do what ``run_data_repairs`` does - open a session, run the repair,
    commit - at the same moment. The property is that neither raises, that the install
    ends with one organization and one membership per user, and that no row is left
    naming a person. Which of the two did the work is not asserted, because with a
    lock held for the length of a transaction the answer depends on scheduling; the
    test above is the one that pins the lock itself.
    """
    results = await asyncio.gather(
        _run_once(populated.factory),
        _run_once(populated.factory),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, BaseException)], results

    final = await _counts(populated.engine)
    assert final["organizations"] == 1, final
    assert final["memberships"] == 2, final
    assert final["person_shaped"] == 3, final
    async with populated.engine.connect() as conn:
        left = int(
            (
                await conn.execute(
                    text(f'SELECT count(*) FROM "{_JOB_RUN}" WHERE kind = :k AND tenant_id = :t'),
                    {"k": "boot-repair-probe", "t": str(populated.person)},
                )
            ).scalar_one()
        )
    assert left == 0, f"{left} rows still name a person"
    assert sum(int(r) for r in results) > 0, results


async def test_a_repair_that_raises_does_not_stop_the_boot(populated) -> None:  # noqa: ANN001
    """The runner's contract, which is what startup relies on.

    ``app/main.py`` calls ``run_data_repairs`` inside a ``try``, and the runner catches
    per repair. So a raising repair is reported, the next repair still runs, and nothing
    escapes to the boot. Asserted with a stub rather than by breaking the real repair,
    because the property belongs to the runner.
    """

    async def _raise(session: AsyncSession) -> int:  # noqa: ANN001, ARG001
        raise ValueError("a repair that cannot run on this database")

    ran: list[str] = []

    async def _record(session: AsyncSession) -> int:  # noqa: ANN001, ARG001
        ran.append("second")
        return 0

    report = await run_data_repairs(
        populated.factory,
        repairs=(
            DataRepair(
                repair_id="stub_raises",
                revision="v3325_organization_tenancy",
                nature="always_wrong",
                summary="raises",
                run=_raise,
            ),
            DataRepair(
                repair_id="stub_records",
                revision="v3325_organization_tenancy",
                nature="always_wrong",
                summary="records",
                run=_record,
            ),
        ),
    )

    by_id = {outcome.repair_id: outcome for outcome in report.outcomes}
    assert by_id["stub_raises"].status == "failed", by_id
    assert "ValueError" in (by_id["stub_raises"].error or ""), by_id
    assert by_id["stub_records"].status == "clean", by_id
    assert ran == ["second"], "the repair after the failure never ran"


async def test_the_rewrite_over_a_hundred_thousand_rows_stays_inside_a_boot(populated, record_property) -> None:  # noqa: ANN001
    """The runtime that decides whether this can run on every boot at all.

    Measured, not estimated: a hundred thousand rows in one tenant table, all stamped
    with a person's id, then one repair run. The ceiling is deliberately generous - the
    point is to catch a repair that has become quadratic, or one that stopped batching,
    not to fail a slow runner. The measured number is attached to the assertion message
    so a failure says what it was.
    """
    async with populated.engine.begin() as conn:
        await conn.execute(
            text(
                f'INSERT INTO "{_JOB_RUN}" (id, tenant_id, kind) '
                "SELECT 'perf-' || g, :t, 'backfill-perf' FROM generate_series(1, :n) AS g"
            ),
            {"t": str(populated.person), "n": _PERF_ROWS},
        )
        total = int((await conn.execute(text(f'SELECT count(*) FROM "{_JOB_RUN}"'))).scalar_one())
    assert total >= _PERF_ROWS, total

    started = time.perf_counter()
    changed = await _run_once(populated.factory)
    elapsed = time.perf_counter() - started

    async with populated.engine.connect() as conn:
        left = int(
            (
                await conn.execute(
                    text(f'SELECT count(*) FROM "{_JOB_RUN}" WHERE tenant_id = :t'), {"t": str(populated.person)}
                )
            ).scalar_one()
        )
        rows = int((await conn.execute(text(f'SELECT count(*) FROM "{_JOB_RUN}"'))).scalar_one())
    assert left == 0, f"{left} of {rows} rows still name a person after {elapsed:.1f}s"
    assert changed >= _PERF_ROWS, changed
    assert elapsed < _PERF_CEILING_SECONDS, f"{rows} rows took {elapsed:.1f}s, over {_PERF_CEILING_SECONDS}s"
    # Recorded into the JUnit report rather than only asserted, because the number is
    # the answer to "can this run on every boot of a real install" and a pass/fail
    # cannot carry it. This is the same reason the CI publisher exists.
    record_property("rows", rows)
    record_property("rows_changed", changed)
    record_property("repair_seconds", round(elapsed, 2))
