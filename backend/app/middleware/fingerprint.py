# OpenConstructionERP - DataDrivenConstruction (DDC)
# CWICR Cost Database Engine · CAD2DATA Pipeline
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
# AGPL-3.0 License · DDC-CWICR-OE-2026
"""Response identity headers.

This distribution emits only neutral identity headers: an ``X-Powered-By``
whose value comes from the single display-name constant, and an
``X-License`` stating the AGPL-3.0-or-later terms the source is shipped
under. The upstream project's vendor markers (``X-DDC-Engine``,
``X-DDC-Origin``, ``X-DDC-Author``, ``X-DDC-Build``, ``Server-Timing``)
are gone: attribution lives in NOTICE, LICENSE and the /api/source
endpoint, not in every HTTP response.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings


class DDCFingerprintMiddleware(BaseHTTPMiddleware):
    """Adds the product-name and licence headers to all API responses.

    The class keeps its historical name because it is mounted by that name
    in ``app.main``; it is an internal identifier, not a user-visible one.

    ASCII-only header values: HTTP header values are latin-1/ASCII, and
    Starlette's TestClient decodes them as UTF-8, so a non-ASCII byte here
    would crash every TestClient-based test that goes through the full app
    stack. The display name from settings is pure ASCII.
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        super().__init__(app)
        try:
            self._app_name = get_settings().app_name or "Construction AI OS"
        except Exception:
            # Settings may not be available during very early import chains
            # (tests, shell scripts). Fall back to the product name itself.
            self._app_name = "Construction AI OS"

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Powered-By"] = self._app_name
        response.headers["X-License"] = "AGPL-3.0-or-later"
        return response
