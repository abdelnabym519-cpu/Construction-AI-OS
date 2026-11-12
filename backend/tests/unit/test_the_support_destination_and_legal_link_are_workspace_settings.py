# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two workspace settings, stored on the server, that used to be somebody else's.

The bug-report channel used to open this repository's GitHub issues in every
deployment, which told every user where the software came from, and the Legal
link's visibility used to live in one browser's localStorage, which is not a
workspace setting at all - it followed nobody and an admin could not set it for
their team.

Both are fields on the workspace branding record now, because that record is
already the server-side, per-workspace settings store: a small JSON file in the
data dir, read by a PUBLIC endpoint (the footers and the login page have no token
yet) and written by an admin-only one. Using it means no schema change, no
migration and no second cache to keep coherent.

What these tests hold down:

  * the defaults, which are the whole point - no destination until an admin sets
    one, so the channel is hidden rather than pointing somewhere unowned, and the
    Legal link visible until an admin who has cleared the licence position turns
    it off;
  * that a destination survives a write and a read, so it really is workspace
    data and not a per-browser preference;
  * that an omitted field keeps its stored value, since the router merges by
    ``model_fields_set`` and saving a logo must not silently drop a support
    mailbox;
  * and that the URL scheme is an allowlist. The value is admin-set but served
    publicly and opened by a browser, so ``javascript:`` and ``data:`` are
    refused outright rather than filtered by a denylist nobody can finish.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.app_branding import (
    DEFAULT_BRANDING,
    read_branding,
    sanitise,
    sanitise_support_destination,
    write_branding,
)


def test_a_fresh_workspace_has_no_support_destination_and_shows_the_legal_link(tmp_path: Path) -> None:
    stored = read_branding(data_dir=tmp_path)
    assert stored["support_destination"] is None
    assert stored["legal_link_visible"] is True
    assert DEFAULT_BRANDING["support_destination"] is None
    assert DEFAULT_BRANDING["legal_link_visible"] is True


def test_a_saved_destination_survives_a_read(tmp_path: Path) -> None:
    write_branding({"support_destination": {"kind": "email", "value": "support@example.com"}}, data_dir=tmp_path)
    stored = read_branding(data_dir=tmp_path)
    assert stored["support_destination"] == {"kind": "email", "value": "support@example.com"}
    # untouched fields keep their defaults through the same write
    assert stored["legal_link_visible"] is True
    assert stored["company_name"] == ""


def test_turning_the_legal_link_off_is_stored_and_turning_it_back_on_is_too(tmp_path: Path) -> None:
    write_branding({"legal_link_visible": False}, data_dir=tmp_path)
    assert read_branding(data_dir=tmp_path)["legal_link_visible"] is False
    write_branding({"legal_link_visible": True}, data_dir=tmp_path)
    assert read_branding(data_dir=tmp_path)["legal_link_visible"] is True


def test_an_ambiguous_visibility_value_falls_back_to_visible() -> None:
    """Only an exact False hides the link.

    The link is the product's one required pointer at the licence notice and the
    source offer, so a corrupt file, a hand-edited "yes" and a missing key all
    land on the side of showing it.
    """
    for value in (None, "no", "false", 0, [], {}):
        assert sanitise({"legal_link_visible": value})["legal_link_visible"] is True, value
    assert sanitise({"legal_link_visible": False})["legal_link_visible"] is False


def test_a_https_support_page_and_a_mailbox_are_both_accepted() -> None:
    assert sanitise_support_destination({"kind": "url", "value": "https://support.example.com/new"}) == {
        "kind": "url",
        "value": "https://support.example.com/new",
    }
    assert sanitise_support_destination({"kind": "email", "value": "help@builder.example"}) == {
        "kind": "email",
        "value": "help@builder.example",
    }


def test_a_destination_that_executes_instead_of_navigating_is_refused() -> None:
    """The scheme is an allowlist, not a denylist.

    This value is admin-set but served from a public endpoint and opened in a
    user's browser, so anything that runs code rather than navigating is refused
    outright - including the schemes nobody thought of yet.
    """
    for value in (
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "//support.example.com",
        "support.example.com",
    ):
        assert sanitise_support_destination({"kind": "url", "value": value}) is None, value


def test_a_credential_in_the_support_url_is_refused() -> None:
    """A userinfo component would be handed to every user's browser."""
    assert sanitise_support_destination({"kind": "url", "value": "https://user:pass@support.example.com"}) is None


def test_a_malformed_mailbox_is_refused_rather_than_sent() -> None:
    for value in ("", "   ", "not-an-email", "two@@example.com", "no-tld@example", "a@b@c.com", "mailto:a@b.com"):
        assert sanitise_support_destination({"kind": "email", "value": value}) is None, value


def test_an_unknown_kind_or_shape_is_refused() -> None:
    for payload in (
        None,
        "",
        [],
        {"kind": "github", "value": "https://github.com/someone/somewhere"},
        {"kind": "url"},
        {"value": "https://support.example.com"},
        {"kind": "url", "value": 42},
    ):
        assert sanitise_support_destination(payload) is None, payload


def test_the_request_model_itself_is_accepted_not_just_a_dict() -> None:
    """The write path merges the validated model straight onto the stored payload.

    Treating only a dict as valid would silently discard every destination an
    admin saved, because the router never converts it.
    """

    class Submitted:
        kind = "url"
        value = "https://support.example.com/tickets"

    assert sanitise_support_destination(Submitted()) == {
        "kind": "url",
        "value": "https://support.example.com/tickets",
    }


def test_a_destination_is_trimmed_and_bounded() -> None:
    assert sanitise_support_destination({"kind": "email", "value": "  support@example.com  "}) == {
        "kind": "email",
        "value": "support@example.com",
    }
    long_url = "https://support.example.com/" + "a" * 900
    result: Any = sanitise_support_destination({"kind": "url", "value": long_url})
    assert result is not None and len(result["value"]) <= 300


def test_sanitise_never_drops_the_two_settings_whatever_else_arrives() -> None:
    """A corrupt or hand-edited file degrades to the defaults, not to a KeyError."""
    for payload in (None, "branding", [], {"mode": "nonsense"}):
        out = sanitise(payload)
        assert "support_destination" in out and "legal_link_visible" in out, payload
        assert out["legal_link_visible"] is True, payload
