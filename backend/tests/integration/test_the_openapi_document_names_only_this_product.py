# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The API documentation a user can fetch describes this product only.

/openapi.json and /docs are user-visible surfaces: they are served, they are
read by whoever integrates against the API, and they are generated from the
FastAPI metadata and from each route's flags rather than from any template a
static scan would see.

The passwordless demo-login endpoint is asserted absent from the schema here
rather than absent from the application. It stays registered, because the e2e
suite and a seeded demo sign in through it; what it must not do is advertise
itself in the documentation handed to an integrator.

The routes are inspected instead of the rendered document on purpose. Building
the full OpenAPI schema is measured in tens of seconds on this application and
has been seen to block the single-worker event loop while it runs, so a test
that called it would add a minute to a shard to prove something the route flags
already determine: FastAPI omits a route from the schema exactly when
include_in_schema is false.
"""

from __future__ import annotations

from app.main import app

FORBIDDEN = (
    "open source",
    "open-source",
    "opensource",
    "GitHub",
    "Telegram",
    "DataDrivenConstruction",
    "Data Driven Construction",
)


def _offenders(text: str) -> list[str]:
    return [w for w in FORBIDDEN if w.lower() in str(text).lower()]


def test_the_openapi_metadata_carries_no_upstream_or_open_source_wording() -> None:
    assert not _offenders(app.title), f"the OpenAPI title carries {_offenders(app.title)}"
    assert not _offenders(app.description), f"the OpenAPI description carries {_offenders(app.description)}"
    assert app.description.strip(), "the description was emptied rather than rewritten"


def test_the_licence_still_travels_with_the_document() -> None:
    """The licence block is an obligation, not advertising, so it stays.

    AGPL-3.0 requires the licence to travel with the work. This is the other
    half of the cleanup: removing the promotion must not remove the notice.
    """
    # FastAPI has stored this under two attribute names across versions, so both
    # are asked rather than assuming the one this environment happens to have.
    block = getattr(app, "license", None) or getattr(app, "license_info", None) or {}
    licence = block.get("name", "") if isinstance(block, dict) else str(block)
    assert "AGPL" in str(licence), f"the OpenAPI licence block no longer states the licence: {block!r}"


def test_no_demo_login_route_is_published_in_the_schema() -> None:
    demo_routes = [r for r in app.routes if "demo-login" in getattr(r, "path", "")]
    assert demo_routes, "the demo-login endpoint disappeared entirely; the e2e suite signs in through it"
    published = [r.path for r in demo_routes if getattr(r, "include_in_schema", True)]
    assert not published, f"demo-login is still published in the OpenAPI document: {published}"
