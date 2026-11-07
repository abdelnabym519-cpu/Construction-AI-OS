# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The API documentation a user can fetch describes this product only.

/openapi.json and /docs are user-visible surfaces: they are served, they are read
by whoever integrates against the API, and they are generated from the FastAPI
metadata and from each route's flags rather than from any template a static scan
would see.

The passwordless demo-login endpoint is asserted absent from the schema here
rather than absent from the application. It stays registered, because the e2e
suite and a seeded demo sign in through it; what it must not do is advertise
itself in the documentation handed to an integrator.

Two shapes of app are used, and the split is deliberate. ``create_app()`` builds
the FastAPI instance and sets its title, description and licence block, but the
module routers are discovered on startup, so a pre-startup app has no routes on it
at all - asking one about demo-login would prove nothing. The route flags are read
from the users router itself, which is where they are declared. Neither test runs
the lifespan, so neither needs a database.

The rendered OpenAPI document is not built. That is measured in tens of seconds on
this application and has been seen to block the single-worker event loop while it
runs, and FastAPI omits a route from the schema exactly when include_in_schema is
false, so the flags already determine the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

FORBIDDEN = (
    "open source",
    "open-source",
    "opensource",
    "GitHub",
    "Telegram",
    "DataDrivenConstruction",
    "Data Driven Construction",
)


def _fresh_app() -> FastAPI:
    """The application as it exists before any startup has run."""
    from app.main import create_app

    return create_app()


def _offenders(text: str) -> list[str]:
    return [w for w in FORBIDDEN if w.lower() in str(text).lower()]


def test_the_openapi_metadata_carries_no_upstream_or_open_source_wording() -> None:
    app = _fresh_app()
    assert not _offenders(app.title), f"the OpenAPI title carries {_offenders(app.title)}"
    assert not _offenders(app.description), f"the OpenAPI description carries {_offenders(app.description)}"
    assert str(app.description).strip(), "the description was emptied rather than rewritten"


def test_the_licence_still_travels_with_the_document() -> None:
    """The licence block is an obligation, not advertising, so it stays.

    AGPL-3.0 requires the licence to travel with the work. This is the other half
    of the cleanup: removing the promotion must not remove the notice. FastAPI has
    stored this under two attribute names across versions, so both are asked.
    """
    app = _fresh_app()
    block = getattr(app, "license", None) or getattr(app, "license_info", None) or {}
    licence = block.get("name", "") if isinstance(block, dict) else str(block)
    assert "AGPL" in str(licence), f"the OpenAPI licence block no longer states the licence: {block!r}"


def test_no_demo_login_route_is_published_in_the_schema() -> None:
    from app.modules.users.router import router

    demo_routes = [r for r in router.routes if "demo-login" in getattr(r, "path", "")]
    assert demo_routes, "the demo-login endpoint disappeared entirely; the e2e suite signs in through it"
    published = [r.path for r in demo_routes if getattr(r, "include_in_schema", True)]
    assert not published, f"demo-login is still published in the OpenAPI document: {published}"
    # Both spellings are registered, and both have to be hidden: hiding only one
    # left the trailing-slash variant in /openapi.json, which is where a
    # passwordless login route is least welcome.
    assert len(demo_routes) >= 2, f"expected both spellings registered, saw {[r.path for r in demo_routes]}"
