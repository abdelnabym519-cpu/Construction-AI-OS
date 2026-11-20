# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The day a gate is assessed on is the day the caller asked for.

The structural ratchet beside this file proves the seam exists. This proves it
carries: that a day handed to the provider is the day the service underneath
receives, on both sides of a decision date, and that handing nothing still
produces today's real date - because the evidence registry calls these providers
without the argument and the shipped product must keep reading the clock.

The service is replaced rather than a database stood up. What is under test is the
hand-off at this layer, not the overdue arithmetic, which belongs to the service's
own tests; a fake that records the day it was given is enough to prove the
hand-off and needs no fixtures. It returns the real return shape - a mapping with
an ``items`` key, which the provider sorts - because a fake that returns the wrong
shape fails for a reason that has nothing to do with the day, and the first run of
this file failed exactly that way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.modules.commitment_release.evidence import overdue_decisions

# Two file-local instants either side of a decision date: the last day the
# decision is still in time, and the first day it is overdue.
LAST_DAY_IN_TIME = date(2026, 3, 31)
FIRST_DAY_OVERDUE = date(2026, 4, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("day", [LAST_DAY_IN_TIME, FIRST_DAY_OVERDUE])
async def test_the_day_given_is_the_day_the_service_receives(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    seen: dict[str, date] = {}

    class _FakeService:
        def __init__(self, session: object) -> None:
            self.session = session

        async def item_views(
            self, project_id: uuid.UUID, *, today: date, item_ids: list[uuid.UUID] | None = None
        ) -> dict[str, list[dict]]:
            seen["today"] = today
            seen["project_id"] = project_id
            return {"items": []}

    monkeypatch.setattr("app.modules.commitment_release.service.CommitmentService", _FakeService)
    item = SimpleNamespace(id=uuid.uuid4())

    await overdue_decisions(None, uuid.uuid4(), "gate_code", [item], today=day)

    assert seen["today"] == day, f"the service was given {seen['today']}, not the {day} the caller asked for"


@pytest.mark.asyncio
async def test_with_no_day_given_the_provider_uses_the_real_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production default stays the wall clock.

    This is the other half of the seam. A parameter that quietly changed the
    shipped behaviour would be worse than no parameter: the registry calls these
    providers without it, so the fallback is the path every real gate takes.
    """
    seen: dict[str, date] = {}

    class _FakeService:
        def __init__(self, session: object) -> None:
            self.session = session

        async def item_views(
            self, project_id: uuid.UUID, *, today: date, item_ids: list[uuid.UUID] | None = None
        ) -> dict[str, list[dict]]:
            seen["today"] = today
            return {"items": []}

    monkeypatch.setattr("app.modules.commitment_release.service.CommitmentService", _FakeService)
    before = datetime.now(UTC).date()
    await overdue_decisions(None, uuid.uuid4(), "gate_code", [SimpleNamespace(id=uuid.uuid4())])
    after = datetime.now(UTC).date()

    assert before <= seen["today"] <= after, f"expected the real date between {before} and {after}, got {seen['today']}"
