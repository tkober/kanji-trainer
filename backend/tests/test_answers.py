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


# 月 the vocabulary (reading つき) vs 月 the kanji (onyomi げつ/がつ).
TSUKI = [{"reading": "つき", "primary": True, "accepted_answer": True}]
GETSU_GATSU = [
    {"reading": "げつ", "primary": True, "accepted_answer": True, "type": "onyomi"},
    {"reading": "がつ", "primary": False, "accepted_answer": True, "type": "onyomi"},
]


def test_the_kanjis_reading_on_a_vocabulary_is_a_retry_with_a_dedicated_hint():
    check = check_reading("げつ", TSUKI, "vocabulary", GETSU_GATSU)
    assert not check.correct
    assert check.retry
    assert check.expected == ""
    assert check.hint is not None
    assert "vocabulary" in check.hint


def test_the_vocabularys_own_reading_still_wins_even_if_it_equalled_a_kanji_reading():
    """A vocabulary reading that happens to coincide with the kanji's is simply
    correct -- the item's own readings are checked first."""
    check = check_reading("つき", TSUKI, "vocabulary", kanji_readings=TSUKI)
    assert check.correct
    assert not check.retry


def test_an_unrelated_reading_is_still_plain_wrong_with_kanji_readings_present():
    check = check_reading("ざつ", TSUKI, "vocabulary", GETSU_GATSU)
    assert not check.correct
    assert not check.retry
    assert check.hint is None


def test_without_kanji_readings_the_old_behaviour_is_unchanged():
    check = check_reading("げつ", TSUKI, "vocabulary")
    assert not check.correct
    assert not check.retry
    assert check.hint is None


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
        ("onnna", "おんな"),
        ("onna", "おんあ"),
    ],
)
def test_romaji_to_hiragana(romaji: str, kana: str):
    assert romaji_to_hiragana(romaji) == kana


def test_the_doubled_n_is_one_kana():
    """Most IMEs need "nn" for ん, so that is what the fingers do."""
    assert romaji_to_hiragana("sann") == "さん"
    assert romaji_to_hiragana("sannnen") == "さんねん"
    # "nn" is eager now -- see "onna"/"onnna" above -- so な only survives
    # past it with a third n: "annai" would be あんあい, not あんない.
    assert romaji_to_hiragana("annnai") == "あんない"


def test_nn_is_eager_like_an_ime_issue_31():
    """せんえん (1000 yen), not せんねん, when typed the way an IME expects.

    "sennenn" is "nn" (ん) + "e" + "nn" (ん): the fingers write せんえん.
    Getting ねん there would need a third n to spell out the "ne" syllable
    before the closing ん, which is "sennnenn".
    """
    assert romaji_to_hiragana("sennenn") == "せんえん"
    assert romaji_to_hiragana("sennnenn") == "せんねん"


def test_normalise_kana_folds_katakana_and_strips_spaces():
    assert normalise_kana(" コー ヒー ") == "こーひー"
