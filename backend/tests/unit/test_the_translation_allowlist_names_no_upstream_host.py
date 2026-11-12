# DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""The translation allowlist must not name an upstream host by default.

``POST /translation/lookup-tables/download`` with ``kind="iate"`` and a
``url`` makes this server fetch a TBX dump from somewhere else, and
``_IATE_ALLOWED_PREFIXES`` is what decides where "somewhere else" may be.
The list is also the SSRF guard, so it cannot simply be deleted: the route
stays open to any login, which is the right trade for the official IATE
source and the wrong one for a mirror belonging to the project this
distribution was forked from.

So the default is the official source alone. The upstream mirrors that used
to sit next to it are reachable only through ``OE_IATE_EXTRA_HOSTS``, which
is an operator's decision per deployment rather than the product's decision
for everybody. These tests pin both halves - the default stays free of
upstream hosts, and the opt-in still works - because a guard that only
checked the default would pass just as well if the env var had been broken.
"""

from __future__ import annotations

import pytest

from app.core.translation.downloader import (
    _IATE_ALLOWED_PREFIXES,
    _iate_allowlist,
)

UPSTREAM_HOSTS = (
    "datadrivenconstruction.io",
    "openconstructionerp.com",
    "github.com/datadrivenconstruction",
    "raw.githubusercontent.com/datadrivenconstruction",
)


def test_the_default_allowlist_is_the_official_source_alone() -> None:
    assert _IATE_ALLOWED_PREFIXES == ("https://iate.europa.eu/",)


@pytest.mark.parametrize("prefix", UPSTREAM_HOSTS)
def test_no_upstream_host_is_allowed_by_default(prefix: str) -> None:
    assert not any(prefix in allowed for allowed in _iate_allowlist())


def test_an_upstream_mirror_url_is_not_inside_the_allowlist() -> None:
    url = "https://datadrivenconstruction.io/iate/IATE_export.tbx"
    assert not url.startswith(_iate_allowlist())


def test_an_operator_can_opt_in_to_a_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OE_IATE_EXTRA_HOSTS", "https://mirror.internal.example.org/iate/")
    allowlist = _iate_allowlist()
    assert "https://mirror.internal.example.org/iate/" in allowlist
    url = "https://mirror.internal.example.org/iate/IATE_export.tbx"
    assert url.startswith(allowlist)


def test_the_opt_in_does_not_widen_the_default_for_the_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OE_IATE_EXTRA_HOSTS", "https://mirror.internal.example.org/")
    assert len(_iate_allowlist()) == 2
    monkeypatch.delenv("OE_IATE_EXTRA_HOSTS")
    assert _iate_allowlist() == _IATE_ALLOWED_PREFIXES
