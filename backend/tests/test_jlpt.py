"""JLPT level estimates (issue #62): the matching rules, `refresh_jlpt`'s
diffing UPDATE and `/api/stats/jlpt`'s bucketing.

The matching tests run against the real checked-in CSVs (`app/data/jlpt/`),
not a fixture -- each one is checked against a `grep` of the file first (see
the issue), so a future data refresh that changes one of these rows is
exactly the kind of regression this file exists to catch.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db import Progress, Subject
from app.jlpt import jlpt_level, kanji_characters, kanji_level, refresh_jlpt, word_level
from app.srs import ItemState

NOW = datetime.now(timezone.utc)


# --- matching rules, against the real data ----------------------------------


def test_kanji_matches_by_character():
    assert jlpt_level("kanji", "一", []) == 5


def test_kanji_not_in_the_list_is_none():
    assert jlpt_level("kanji", "瀧", []) is None


def test_radicals_never_get_a_level():
    """Even with a character and a reading that would otherwise match."""
    assert jlpt_level("radical", "一", [{"reading": "いち"}]) is None


def test_vocabulary_without_characters_is_none():
    assert jlpt_level("vocabulary", None, []) is None


def test_a_reading_that_does_not_match_the_list_entry_is_rejected():
    """`一月` is in `vocab.csv` only as ひとつき ("one month"), N5. The word
    this app actually teaches at that spelling, いちがつ ("January"), is a
    different word that happens to share its expression -- an
    expression-only match would wrongly credit it with N5."""
    assert jlpt_level("vocabulary", "一月", [{"reading": "いちがつ"}]) is None


def test_a_suru_verb_matches_its_noun_either_way_the_suru_is_spelled():
    """`妥協` (だきょう) is N1. `妥協する` (だきょうする) is the same word with
    the verb spelled out, and must land on the same level."""
    noun = jlpt_level("vocabulary", "妥協", [{"reading": "だきょう"}])
    verb = jlpt_level("vocabulary", "妥協する", [{"reading": "だきょうする"}])
    assert noun == 1
    assert verb == 1


def test_the_lists_own_suru_parenthetical_is_stripped_before_matching():
    """`勉強` is listed as reading "べんきょう (する)" -- the parenthetical
    must not leak into the comparison key."""
    assert jlpt_level("vocabulary", "勉強", [{"reading": "べんきょう"}]) == 5


def test_semicolon_separated_expression_variants_both_match():
    """`足; 脚` is one N5 row with reading あし; both expressions must
    resolve to the same level through the one reading."""
    assert jlpt_level("vocabulary", "足", [{"reading": "あし"}]) == 5
    assert jlpt_level("vocabulary", "脚", [{"reading": "あし"}]) == 5


def test_kana_vocabulary_with_no_readings_falls_back_to_its_own_characters():
    """`kana_vocabulary` carries no separate reading -- `characters` already
    is the reading, folded to hiragana for the comparison."""
    assert jlpt_level("kana_vocabulary", "テレビ", []) == 5


def test_vocabulary_not_in_the_list_is_none():
    assert jlpt_level("vocabulary", "存在しない言葉", []) is None


def test_vocabulary_okurigana_variant_matches_via_jlpt_level_too():
    """The same okurigana-variant rule `word_level` uses applies to a
    WaniKani subject's own matching (`jlpt_level`/`_vocab_level`) -- a
    subject taught as 落ちる must still find the list's old-spelling 落る."""
    assert jlpt_level("vocabulary", "落ちる", [{"reading": "おちる"}]) == 4


def test_vocabulary_reading_mismatch_on_a_direct_hit_still_wins_over_fallback():
    """`一月` is listed verbatim (ひとつき) -- the fallback rules must never
    be tried once the expression itself is found, even though it has kanji
    and would otherwise have a non-empty skeleton."""
    assert jlpt_level("vocabulary", "一月", [{"reading": "いちがつ"}]) is None


# --- word_level / kanji_level / kanji_characters (issue #75) ----------------
#
# Independent lookups against the same checked-in snapshot, for arbitrary
# text rather than a WaniKani subject -- `get_jlpt_levels`' backing logic.
# Same convention as above: every assertion is checked against a `grep` of
# the real CSVs first.


def test_word_level_matches_by_expression_and_reading():
    # `vocab.csv`: "1,妥協,だきょう" (N1).
    assert word_level("妥協", "だきょう") == 1


def test_word_level_rejects_a_reading_that_does_not_match():
    # "一月" is listed only as ひとつき ("one month"), N5 -- a different
    # reading of the same expression must not borrow its level.
    assert word_level("一月", "いちがつ") is None


def test_word_level_folds_a_katakana_reading_to_hiragana():
    # "5,テレビ,テレビ" -- the list's own reading is katakana too, and both
    # the katakana and the hiragana spelling of the query must match it.
    assert word_level("テレビ", "テレビ") == 5
    assert word_level("テレビ", "てれび") == 5


def test_word_level_strips_a_trailing_suru_either_side():
    # "1,妥協,だきょう" -- the する form of the same word must land on the
    # same level however the suru is spelled on either side.
    assert word_level("妥協する", "だきょうする") == 1
    assert word_level("妥協", "だきょうする") == 1
    assert word_level("妥協する", "だきょう") == 1


def test_word_level_without_a_reading_is_the_easiest_of_every_entry():
    # "そば" is listed twice: "5,そば,そば" and "2,そば,そば" -- without a
    # reading to narrow it down, the easier (numerically larger) level wins.
    assert word_level("そば") == 5


def test_word_level_without_a_reading_still_needs_the_expression_listed():
    assert word_level("存在しない言葉") is None


def test_word_level_single_kanji_not_in_the_vocab_list_falls_back_to_kanji():
    # "入" is in `kanji.csv` (N5) but, unlike "毎" or "水", never appears as
    # its own entry in `vocab.csv` -- only inside longer words.
    assert word_level("入") == 5
    assert word_level("入") == kanji_level("入")


def test_word_level_single_kanji_in_the_vocab_list_does_not_fall_back():
    # "水" is itself an N5 vocabulary entry ("5,水,みず"). A reading that
    # does not match it must not silently fall back to the kanji list either
    # -- the expression was found, just not at this reading.
    assert word_level("水", "みず") == 5
    assert word_level("水", "水") is None


def test_word_level_unlisted_multi_character_word_is_none():
    assert word_level("存在しない") is None


def test_kanji_level_known_and_unknown_characters():
    assert kanji_level("一") == 5
    assert kanji_level("瀧") is None


def test_kanji_characters_lists_kanji_only_in_order_without_duplicates():
    assert kanji_characters("高校生は楽しい高校生") == ["高", "校", "生", "楽"]
    assert kanji_characters("ひらがなだけ") == []


# --- okurigana-variant and adverb-と/に matching rules -----------------------
#
# Found measuring `get_jlpt_levels` against real text (jp-reading-practice's
# fugashi/unidic tokeniser): both rules only ever fire once the expression is
# not listed verbatim at all, and both still require an exact reading match,
# so neither can turn an unrelated word into a false hit (see `_fallback_level`
# and `_adverb_variant_level`'s own doc comments). Every assertion here is
# checked against a `grep` of the real CSVs first, same convention as above.


def test_okurigana_variant_matches_the_lists_old_spelling():
    # `vocab.csv`: "4,落る,おちる" and "4,落す,おとす" -- the list's old
    # okurigana, never updated to the now-standard 落ちる/落とす a real
    # tokeniser hands back.
    assert word_level("落ちる", "おちる") == 4
    assert word_level("落とす", "おとす") == 4


def test_okurigana_variant_needs_the_reading_too_not_kanji_alone():
    # `曲る,まがる` (N5) and `曲げる,まげる` (N2) share the kanji 曲 but are
    # different words at different readings -- the okurigana rule must only
    # ever match the one with the same reading, never let a shared kanji
    # root borrow the other's level.
    assert word_level("曲がる", "まがる") == 5  # okurigana variant of 曲る
    assert word_level("曲げる", "まげる") == 2  # already listed verbatim


def test_okurigana_variant_never_merges_kana_only_homophones():
    """A word with no kanji at all must never go through the okurigana
    fallback, even if its reading happens to coincide with some kanji word's
    own reading elsewhere in the list -- that would be a homophone, not an
    okurigana variant. `_kanji_skeleton` returning "" is what stops it."""
    from app.jlpt import _kanji_skeleton

    assert _kanji_skeleton("ひらがなだけ") == ""
    # Not actually listed at this reading anywhere -- must stay None rather
    # than matching some unrelated kanji entry through an empty skeleton.
    assert word_level("そのことば", "そのことば") is None


def test_adverb_with_trailing_to_in_the_list_matches_the_bare_form():
    # `vocab.csv`: "5,ゆっくりと,ゆっくりと" -- a real tokeniser splits ゆっくり
    # and と into separate tokens (と is its own particle), so the word as it
    # actually reaches `get_jlpt_levels` never carries the と at all.
    assert word_level("ゆっくり", "ゆっくり") == 5


def test_adverb_with_trailing_ni_in_the_list_matches_the_bare_form():
    # `vocab.csv` has only "5,すぐに,すぐに", no bare すぐ entry.
    assert word_level("すぐ", "すぐ") == 5


def test_adverb_with_trailing_ni_also_matches_in_reverse():
    # `本当` is listed twice -- "5,本当,ほんとう" and "3,本当,ほんと" -- but
    # `本当に` ("really", the common adverb) is not listed at all. A caller
    # that kept the に attached must still land on the ほんとう reading's own
    # level, never the unrelated ほんと one.
    assert word_level("本当に", "ほんとうに") == 5
    assert word_level("本当", "ほんとう") == 5  # the direct entry itself, unchanged


def test_trailing_to_is_not_stripped_in_reverse():
    """と is also the plain conditional/quotative particle ("多いと" = "if
    [it's] a lot"), not only an adverb's own ending -- stripping it off a
    query that happens to end with と would wrongly credit an ordinary
    sentence fragment with its stem's level. `多い` itself is listed
    ("5,多い,おおい"), `多いと` is not; the whole point of this test is that
    the second fact must not resolve through the first."""
    assert word_level("多いと", "おおいと") is None


# --- supplement.csv: words missing from vocab.csv entirely -------------------
#
# Added only with a cited source (see the data README's "Supplement"
# section) -- every assertion here is checked against a `grep` of
# `supplement.csv` first, same convention as the rest of this file.


def test_supplement_fills_a_word_vocab_csv_does_not_have_at_all():
    # `supplement.csv`: "4,船,ふね" -- cited from Jisho's own JLPT tag;
    # `vocab.csv` only has 船 as part of the ～船 ("-ship") affix, at an
    # unrelated reading (せん).
    assert word_level("船", "ふね") == 4


def test_supplement_adds_a_second_reading_without_disturbing_the_first():
    # `vocab.csv` already lists 退く at どく (N3); the supplement adds it
    # again at しりぞく (N1) -- two different words, same spelling, same
    # convention `vocab.csv` itself already uses for words like 本当.
    assert word_level("退く", "どく") == 3
    assert word_level("退く", "しりぞく") == 1


def test_words_jp_reading_practice_originally_reported_missing():
    """楽しむ and 室内 are what the brief for this fix started from. 楽しむ
    turned out to be an okurigana variant of 楽む, already in `vocab.csv`
    (fixed by the matching rule, not the supplement). 室内 has no Jisho
    JLPT tag at all -- checked the same way as every supplement candidate
    -- so it is not guessed into the supplement and stays `None`."""
    assert word_level("楽しむ", "たのしむ") == 4
    assert word_level("室内", "しつない") is None


# --- refresh_jlpt -------------------------------------------------------------


async def _seed_subject(
    session,
    *,
    wanikani_id: int,
    object_type: str = "vocabulary",
    characters: str | None,
    readings: list[dict] | None = None,
    jlpt: int | None = None,
) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type=object_type,
        level=1,
        slug=f"s{wanikani_id}",
        characters=characters,
        meanings=[{"meaning": "x", "primary": True, "accepted_answer": True}],
        readings=readings or [],
        jlpt=jlpt,
    )
    session.add(subject)
    await session.flush()
    session.add(Progress(subject_id=subject.id, state=ItemState.NEW.value))
    await session.commit()
    return subject.id


async def test_refresh_jlpt_sets_the_level_and_counts_the_change(session):
    radical_id = await _seed_subject(session, wanikani_id=1, object_type="radical", characters="一")
    kanji_id = await _seed_subject(session, wanikani_id=2, object_type="kanji", characters="一")
    vocab_id = await _seed_subject(
        session,
        wanikani_id=3,
        object_type="vocabulary",
        characters="勉強",
        readings=[{"reading": "べんきょう"}],
    )

    changed = await refresh_jlpt(session)
    await session.commit()

    assert changed == 2  # the radical was already None -> None, no change
    assert (await session.get(Subject, radical_id)).jlpt is None
    assert (await session.get(Subject, kanji_id)).jlpt == 5
    assert (await session.get(Subject, vocab_id)).jlpt == 5


async def test_a_second_refresh_changes_nothing(session):
    await _seed_subject(session, wanikani_id=2, object_type="kanji", characters="一")
    await refresh_jlpt(session)
    await session.commit()

    changed = await refresh_jlpt(session)
    await session.commit()

    assert changed == 0


async def test_refresh_jlpt_clears_a_level_that_no_longer_matches(session):
    """A subject seeded with a stale level (e.g. a re-import changed its
    reading) must be corrected, not just filled in when `None`."""
    subject_id = await _seed_subject(
        session,
        wanikani_id=4,
        object_type="vocabulary",
        characters="存在しない言葉",
        jlpt=3,
    )

    changed = await refresh_jlpt(session)
    await session.commit()

    assert changed == 1
    assert (await session.get(Subject, subject_id)).jlpt is None


# --- the import sets it too ---------------------------------------------------


async def test_import_sets_jlpt_on_flushed_subjects(session):
    """`_flush_subjects` alone does not touch `jlpt` -- it is the
    `refresh_jlpt` call in `_import_everything` that fills it in, exercised
    here the same way `_flush_subjects` is in test_import_mapping.py."""
    from app.importer import _flush_subjects
    from app.wanikani import subject_row

    row = subject_row(
        {
            "id": 99,
            "object": "kanji",
            "data": {
                "level": 1,
                "slug": "ichi",
                "characters": "一",
                "meanings": [{"meaning": "One", "primary": True, "accepted_answer": True}],
                "meaning_mnemonic": "One.",
            },
        }
    )
    await _flush_subjects(session, [row])
    await session.commit()

    from sqlalchemy import select

    subject = (await session.execute(select(Subject).where(Subject.wanikani_id == 99))).scalar_one()
    assert subject.jlpt is None  # not refreshed yet -- that is the next call's job

    await refresh_jlpt(session)
    await session.commit()
    await session.refresh(subject)
    assert subject.jlpt == 5


# --- /api/stats/jlpt -----------------------------------------------------------


async def _seed_bucket_item(
    session,
    *,
    wanikani_id: int,
    object_type: str,
    jlpt: int | None,
    state: str,
    stage: int = 0,
) -> int:
    subject_id = await _seed_subject(
        session,
        wanikani_id=wanikani_id,
        object_type=object_type,
        characters="x",
        jlpt=jlpt,
    )
    await _set_progress(session, subject_id, state, stage)
    return subject_id


async def _set_progress(session, subject_id: int, state: str, stage: int) -> None:
    progress = await session.get(Progress, subject_id)
    progress.state = state
    progress.srs_stage = stage
    await session.commit()


def _bucket(levels: list[dict], jlpt: int, object_type: str) -> dict:
    return next(
        row["counts"] for row in levels if row["jlpt"] == jlpt and row["object_type"] == object_type
    )


async def test_jlpt_endpoint_buckets_known_and_suspended_and_folds_kana_vocabulary(
    session, client
):
    # Known kanji at N5.
    await _seed_bucket_item(
        session, wanikani_id=1, object_type="kanji", jlpt=5, state=ItemState.KNOWN.value
    )
    # Suspended vocabulary at N5.
    await _seed_bucket_item(
        session, wanikani_id=2, object_type="vocabulary", jlpt=5, state=ItemState.SUSPENDED.value
    )
    # kana_vocabulary at N5, learning Apprentice I -- must fold into the same
    # 'vocabulary' bucket as the suspended item above.
    await _seed_bucket_item(
        session,
        wanikani_id=3,
        object_type="kana_vocabulary",
        jlpt=5,
        state=ItemState.LEARNING.value,
        stage=1,
    )
    # A radical -- always jlpt None, must never surface on this card.
    radical_id = await _seed_subject(session, wanikani_id=4, object_type="radical", characters="一")
    await _set_progress(session, radical_id, ItemState.KNOWN.value, 0)
    # A kanji with no JLPT level at all -- must be absent too.
    await _seed_bucket_item(
        session, wanikani_id=5, object_type="kanji", jlpt=None, state=ItemState.KNOWN.value
    )

    body = (await client.get("/api/stats/jlpt")).json()

    kanji_n5 = _bucket(body["levels"], 5, "kanji")
    assert kanji_n5["known"] == 1

    vocab_n5 = _bucket(body["levels"], 5, "vocabulary")
    assert vocab_n5["suspended"] == 1
    assert vocab_n5["apprentice_1"] == 1

    types = {(row["jlpt"], row["object_type"]) for row in body["levels"]}
    assert (5, "kanji") in types
    assert (5, "vocabulary") in types
    # Nothing at all for the radical's (nonexistent) level, and no
    # jlpt=None row of any kind.
    assert all(row["jlpt"] is not None for row in body["levels"])
    assert radical_id  # seeded only to prove it stays out of the response


async def test_jlpt_endpoint_vocabulary_without_level(session, client):
    # One leveled, one not, both vocabulary -- neither suspended.
    await _seed_bucket_item(
        session, wanikani_id=1, object_type="vocabulary", jlpt=5, state=ItemState.KNOWN.value
    )
    await _seed_bucket_item(
        session, wanikani_id=2, object_type="vocabulary", jlpt=None, state=ItemState.NEW.value
    )
    # Suspended, unleveled -- must not count toward the total at all.
    await _seed_bucket_item(
        session,
        wanikani_id=3,
        object_type="kana_vocabulary",
        jlpt=None,
        state=ItemState.SUSPENDED.value,
    )
    # A kanji -- must not leak into the vocabulary totals.
    await _seed_bucket_item(
        session, wanikani_id=4, object_type="kanji", jlpt=5, state=ItemState.KNOWN.value
    )

    body = (await client.get("/api/stats/jlpt")).json()

    assert body["vocabulary_total"] == 2
    assert body["vocabulary_without_level"] == 1


async def test_jlpt_endpoint_with_no_leveled_subjects_returns_an_empty_card(session, client):
    body = (await client.get("/api/stats/jlpt")).json()

    assert body["levels"] == []
    assert body["vocabulary_total"] == 0
    assert body["vocabulary_without_level"] == 0


def test_affixes_and_compound_parts_are_not_supplemented():
    """Split-off affixes and the front half of a split compound would put
    their own level on every word they belong to -- see the README."""
    assert word_level("的", "てき") is None
    assert word_level("日曜", "にちよう") is None
