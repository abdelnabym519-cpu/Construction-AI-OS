"""The three i18n leak lists describe keys the English bundle still carries.

`scripts/check_i18n_leak_baseline.py` exempts a flagged key by looking it up in
one of three lists, and it loads each list with `_load_json`, which returns an
empty dict when the file is not there. That is the right behaviour for the guard
and it leaves two things nothing checks:

  * An entry whose key has left the bundles exempts nothing today, so the guard
    stays green with it, but it is a standing permission: if the key comes back
    and ships the English string into all 44 other bundles, the lookup finds the
    old entry and nobody decides again. Measured when this was written, 9 of the
    894 entries named keys that no bundle in `frontend/src/app/locales/` carries
    any more - `about.team_name`, `about.intro_body`, `about.thanks_cta`,
    `about.founder_name`, `login.github` and the four `auth.how_found_*` options
    of the referral field the sign-up form no longer has. Each of those surfaces
    was deleted on purpose; the exemptions outlived them.

  * A list that is renamed, moved or emptied reads as `{}`, and every check that
    depends on it passes for the reason that there was nothing to check.

The value side is a ratchet in the same idiom as the guard's own baseline: an
allowlist entry records the English string it is exempting, and that record is
documentation the guard never compares, so it drifts in silence. It is compared
here, with the entries known to have drifted declared rather than deleted, so
the number can only go down.

None of this belongs in the guard: its subject is what the bundles do to each
other. This is what the guard's own bookkeeping claims about the bundles, which
is a different question and one the guard cannot ask without trusting the same
three files twice.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "check_i18n_leak_baseline", Path(__file__).with_name("check_i18n_leak_baseline.py")
)
assert _SPEC is not None and _SPEC.loader is not None
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)

#: The three lists, by the names the guard's own docstring gives them.
LISTS = (
    ("allowlist", guard.ALLOWLIST_PATH),
    ("baseline", guard.BASELINE_PATH),
    ("pending review", guard.PENDING_PATH),
)

#: Declared debt on the value axis, each with the reason it is tolerated rather
#: than corrected. May only shrink.
DRIFTED_EN_VALUES = {
    # The guard's own `_PAIR` regex reads a value up to the closing quote and
    # keeps escape sequences as the two characters they are written with, so
    # `\\n` inside this placeholder arrives as a literal backslash and n while
    # the list records what a reader sees. The mismatch is the parser's shape,
    # not a changed string, and recording the parser's view here would go wrong
    # the day the parser learns about escapes.
    "match_elements.new_text.lines_placeholder",
}

#: Floor on each list's size. Not a prediction of the future: a floor that any
#: empty or missing file clears is the vacuity this file exists to close, and
#: the smallest of the three has held more than two hundred entries since the
#: lists were split.
MINIMUM_ENTRIES = 100


def _read_list(relative: str) -> dict[str, dict[str, object]]:
    path = ROOT / relative
    assert path.is_file(), (
        f"{relative} is not in the repository. The guard reads it with a helper that returns an empty dict "
        "for a missing file, so a rename or a move leaves every check depending on it passing on nothing."
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def english_pairs() -> dict[str, str]:
    """The key/value pairs of the bundle the guard itself treats as English.

    Chosen by the guard's `_locale_stem` over the guard's `LOCALE_GLOB` rather
    than by a path written down here, so this file cannot disagree with the
    guard about which bundle is the reference - and a test that read some other
    bundle would be answering a question about a different repository.
    """
    found = sorted(glob.glob(str(ROOT / guard.LOCALE_GLOB)))
    english = [path for path in found if guard._locale_stem(path) == "en"]
    assert len(english) == 1, f"{guard.LOCALE_GLOB} matched {len(english)} bundles whose stem is 'en': {english}"
    return guard._extract_pairs(Path(english[0]).read_text(encoding="utf-8"))


def test_the_reference_bundle_is_the_english_one(english_pairs: dict[str, str]) -> None:
    """The fixture above read `en.ts`, and read something.

    The subset assertion below fails loudly if the pairs come back empty, so
    what this pins is the other half: that "the English bundle" is the file the
    guard scans and not a sibling that happens to parse.
    """
    found = sorted(glob.glob(str(ROOT / guard.LOCALE_GLOB)))
    english = [path for path in found if guard._locale_stem(path) == "en"]

    assert english == [str(ROOT / "frontend/src/app/locales/en.ts")]
    assert len(english_pairs) > 10_000, f"en.ts parsed to {len(english_pairs)} pairs, which is not a bundle"


@pytest.mark.parametrize(("name", "relative"), LISTS)
def test_a_list_is_present_and_holds_what_it_declares(name: str, relative: str) -> None:
    entries = _read_list(relative)

    assert len(entries) >= MINIMUM_ENTRIES, (
        f"the {name} holds {len(entries)} entries. The guard reads a missing file as an empty one and then has "
        "nothing to exempt, so a list that shrank to nothing passes every check in it while carrying no claim "
        "at all; the three lists have each held more than two hundred entries since they were split."
    )


@pytest.mark.parametrize(("name", "relative"), LISTS)
def test_every_key_a_list_names_is_a_key_the_english_bundle_carries(
    name: str, relative: str, english_pairs: dict[str, str]
) -> None:
    """No entry may exempt a key that does not exist.

    The guard only ever looks a *flagged* key up, and a key is flagged from
    en.ts, so an entry for a departed key changes no verdict today. It is still
    a permission that outlives the decision that granted it.
    """
    entries = _read_list(relative)
    gone = sorted(set(entries) - set(english_pairs))

    assert gone == [], (
        f"{len(gone)} entr{'y' if len(gone) == 1 else 'ies'} in the {name} ({relative}) name keys no bundle in "
        f"{guard.LOCALE_GLOB} carries: {gone}. An exemption for a key that does not exist is a standing "
        "permission for it to come back and ship English everywhere without anybody deciding that again. "
        "Delete the entry, and if it is in the pending review list lower PENDING_REVIEW_CEILING in "
        "scripts/check_i18n_leak_baseline.py in the same diff, which is what that constant's own comment asks "
        "for when an entry leaves."
    )


def test_the_pending_ceiling_carries_no_slack() -> None:
    """The constant and the list move together, including downwards.

    The guard enforces this and fails a run over it; it is asserted here as well
    because the failure it produces is a paragraph about a ceiling, and the edit
    that causes it is a deleted line in a JSON file two directories away.
    """
    entries = _read_list(guard.PENDING_PATH)

    assert len(entries) == guard.PENDING_REVIEW_CEILING, (
        f"{guard.PENDING_PATH} holds {len(entries)} entries and PENDING_REVIEW_CEILING is "
        f"{guard.PENDING_REVIEW_CEILING}. The guard fails on either side of that equality, so that the ceiling "
        "never carries slack a future addition could spend silently."
    )


def test_the_english_values_the_allowlist_records_have_not_drifted(english_pairs: dict[str, str]) -> None:
    """What an exemption says the English string is, is still the English string.

    `en_value` is documentation: the guard compares en.ts against the other
    bundles and never against this field, so a value that changed after the
    entry was written goes unnoticed everywhere else.
    """
    entries = _read_list(guard.ALLOWLIST_PATH)
    drifted = sorted(
        key
        for key, entry in entries.items()
        if key in english_pairs
        and "en_value" in entry
        and entry["en_value"] != english_pairs[key]
        and key not in DRIFTED_EN_VALUES
    )

    assert drifted == [], (
        f"{len(drifted)} allowlist entr{'y' if len(drifted) == 1 else 'ies'} "
        f"record{'s' if len(drifted) == 1 else ''} an English value the bundle "
        f"no longer carries: {drifted}. Either the string changed under a standing exemption, which is worth "
        "reading, or the recorded value was wrong when it was written. Correct the entry, or - if the mismatch "
        "is the parser's rather than the bundle's - name the key in DRIFTED_EN_VALUES with the reason."
    )


def test_the_drift_ratchet_names_only_keys_the_allowlist_still_carries() -> None:
    """A tolerated drift may not outlive the entry it tolerates."""
    entries = _read_list(guard.ALLOWLIST_PATH)
    stale = sorted(set(DRIFTED_EN_VALUES) - set(entries))

    assert stale == [], (
        f"DRIFTED_EN_VALUES tolerates {stale}, which the allowlist no longer carries. A tolerance for an entry "
        "that is gone is the same defect this file exists to catch, one level up."
    )
