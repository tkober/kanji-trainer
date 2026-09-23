"""Answer checking.

The asymmetry is the thing under test: meanings forgive typing, readings do
not. A reading check that drifted loose would drill the wrong word without
ever reporting a mistake, which is the worst failure this codebase can have.
"""

from __future__ import annotations

import pytest

from app.answers import (
    check_meaning,
    check_reading,
    levenshtein,
    normalise_kana,
    romaji_to_hiragana,
    typo_allowance,
)

WATER = [{"meaning": "Water", "primary": True, "accepted_answer": True}]
VEGETABLE = [{"meaning": "Vegetable", "primary": True, "accepted_answer": True}]
BIG = [
    {"meaning": "Big", "primary": True, "accepted_answer": True},
    {"meaning": "Large", "primary": False, "accepted_answer": True},
]


# --- meanings --------------------------------------------------------------


def test_exact_meaning_is_accepted_regardless_of_case_and_spacing():
    assert check_meaning("  WATER ", WATER).correct


def test_a_secondary_meaning_counts_but_names_the_primary_one():
    """`expected` is what the learner has *not* said yet, not what they typed."""
    check = check_meaning("large", BIG)
    assert check.correct
    assert check.secondary
    assert check.expected == "Big"


def test_the_primary_meaning_is_not_flagged_as_secondary():
    check = check_meaning("big", BIG)
    assert check.correct
    assert not check.secondary


def test_a_typo_in_a_long_meaning_is_forgiven():
    assert check_meaning("vegtable", VEGETABLE).correct


def test_a_different_word_is_not_a_typo():
    check = check_meaning("fruit", VEGETABLE)
    assert not check.correct
    assert check.expected == "Vegetable"


def test_short_answers_get_no_slack():
    # "day" and "dam" are one edit apart; at three characters that has to be
    # a miss or half the radicals would accept each other's names.
    assert typo_allowance("day") == 0
    assert not check_meaning("dam", [{"meaning": "Day", "primary": True}]).correct


def test_a_blacklisted_meaning_is_rejected_even_when_close():
    auxiliary = [{"meaning": "Water", "type": "blacklist"}]
    check = check_meaning("water", VEGETABLE, auxiliary)
    assert not check.correct
    assert check.hint


def test_a_whitelisted_meaning_is_accepted():
    auxiliary = [{"meaning": "H2O", "type": "whitelist"}]
    assert check_meaning("h2o", WATER, auxiliary).correct


def test_punctuation_around_a_meaning_is_ignored():
    assert check_meaning("water.", WATER).correct


def test_an_empty_answer_is_wrong_and_still_names_the_expectation():
    check = check_meaning("   ", WATER)
    assert not check.correct
    assert check.expected == "Water"


def test_levenshtein_basics():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("same", "same") == 0


# --- readings --------------------------------------------------------------

KOU = [
    {"reading": "こう", "primary": True, "accepted_answer": True, "type": "onyomi"},
    {"reading": "たか", "primary": False, "accepted_answer": False, "type": "kunyomi"},
]


def test_the_accepted_reading_passes():
    assert check_reading("こう", KOU).correct


def test_romaji_is_converted_before_comparison():
    assert check_reading("kou", KOU).correct


def test_katakana_and_hiragana_are_the_same_answer():
    assert check_reading("コウ", KOU).correct


def test_a_known_but_unaccepted_reading_is_wrong_and_explains_itself():
    check = check_reading("たか", KOU)
    assert not check.correct
    assert check.hint is not None
    assert "kunyomi" in check.hint


def test_a_reading_one_kana_off_is_simply_wrong():
    """No typo tolerance here, on purpose."""
    check = check_reading("こお", KOU)
    assert not check.correct
    assert check.hint is None


def test_an_unknown_reading_is_wrong():
    assert not check_reading("ざつ", KOU).correct


# --- romaji ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("romaji", "kana"),
    [
        ("kan", "かん"),
        ("shinbun", "しんぶん"),
        ("gakkou", "がっこう"),
        ("chotto", "ちょっと"),
        ("kin'you", "きんよう"),
        ("tsukue", "つくえ"),
        ("ryokou", "りょこう"),
        ("nihon", "にほん"),
        ("onna", "おんな"),
    ],
)
def test_romaji_to_hiragana(romaji: str, kana: str):
    assert romaji_to_hiragana(romaji) == kana


def test_the_doubled_n_is_one_kana():
    """Most IMEs need "nn" for ん, so that is what the fingers do."""
    assert romaji_to_hiragana("sann") == "さん"
    assert romaji_to_hiragana("sannnen") == "さんねん"
    # The second n still opens a syllable wherever it can -- see "onna" above.
    assert romaji_to_hiragana("annai") == "あんない"


def test_normalise_kana_folds_katakana_and_strips_spaces():
    assert normalise_kana(" コー ヒー ") == "こーひー"
