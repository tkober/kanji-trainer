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
