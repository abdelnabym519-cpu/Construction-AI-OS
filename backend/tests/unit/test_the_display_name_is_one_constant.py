# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The product display name is one constant, and both sides of the wire read it.

The name a user sees - the browser tab, the login card, the OpenAPI page, the
PDF letterhead, the ``From:`` line of an email - used to be retyped at every
one of those sites, and the copies drifted: the email footer said one thing,
the tab title another. This product renames itself away from its upstream
identity, so the display name now has exactly one backend source, the
``app_name`` field in :mod:`app.config`, and exactly one frontend source,
``APP_NAME`` in ``frontend/src/shared/lib/brandName.ts``.

This test is the agreement between the two. It reads the frontend constant
the way a build would - straight from the file - and holds every backend
surface that renders the name (the settings default, the PDF letterhead's
``DEFAULT_BRAND``, the email ``BRAND_NAME``) to the same string. A rename
that lands on one side of the wire and not the other fails here, at the
commit that caused it, instead of in front of a user.

The per-install override is untouched by this: the CustomBranding feature
(``app.core.app_branding`` + the frontend branding store) still lets an
operator white-label their workspace, and ``DEFAULT_BRAND``/``BRAND_NAME``
are only what a user sees when no override is set.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings
from app.core.email.templates import BRAND_NAME
from app.core.pdf_branding import DEFAULT_BRAND

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_BRAND_FILE = REPO_ROOT / "frontend" / "src" / "shared" / "lib" / "brandName.ts"

#: The display name of this product. One string; every site below must read it.
DISPLAY_NAME = "Construction AI OS"

_APP_NAME_LITERAL = re.compile(r"export const APP_NAME\s*=\s*['\"]([^'\"]+)['\"]")


def _backend_default_app_name() -> str:
    """The ``app_name`` default, read from the field, not from a constructed
    Settings: this asserts what the constant SAYS, independent of whatever
    env the test session happens to carry."""
    default = Settings.model_fields["app_name"].default
    assert isinstance(default, str)
    return default


class TestTheDisplayNameIsOneConstant:
    def test_the_backend_constant_is_the_display_name(self) -> None:
        assert _backend_default_app_name() == DISPLAY_NAME

    def test_the_pdf_letterhead_default_reads_the_constant(self) -> None:
        assert _backend_default_app_name() == DEFAULT_BRAND

    def test_the_email_brand_reads_the_constant(self) -> None:
        assert _backend_default_app_name() == BRAND_NAME

    def test_the_frontend_constant_file_exists(self) -> None:
        assert FRONTEND_BRAND_FILE.is_file(), (
            f"{FRONTEND_BRAND_FILE.relative_to(REPO_ROOT)} is missing: the frontend "
            "has no single source for the display name"
        )

    def test_backend_and_frontend_render_the_same_name(self) -> None:
        source = FRONTEND_BRAND_FILE.read_text(encoding="utf-8")
        match = _APP_NAME_LITERAL.search(source)
        assert match, "APP_NAME is not declared as a plain string literal; keep it parseable"
        assert match.group(1) == _backend_default_app_name(), (
            "the frontend APP_NAME and the backend app_name default disagree: a user would see two product names"
        )
