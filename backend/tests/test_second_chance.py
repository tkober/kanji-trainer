"""The two ways an answer is not simply counted wrong.

Both come from the same complaint: a slip costs exactly as much as not
knowing the item, and below four characters there is no typo tolerance to
catch it at all. Neither changes what counts as *correct* — they change what
happens on the way there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import Progress, ReviewLog, Subject
from app.srs import ItemState
from sqlalchemy import func, select

NOW = datetime.now(timezone.utc)


async def seed(session, *, stage: int = 3) -> int:
    """A kanji taught by its on'yomi, with the kun'yomi known but not accepted."""
    subject = Subject(
        wanikani_id=600,
        object_type="kanji",
        level=2,
        slug="上",
        characters="上",
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
        readings=[
            {"reading": "じょう", "primary": True, "accepted_answer": True, "type": "onyomi"},
            {"reading": "うえ", "primary": False, "accepted_answer": False, "type": "kunyomi"},
        ],
        meaning_mnemonic="A toe above the ground.",
    )
    session.add(subject)
    await session.flush()
    session.add(
        Progress(
            subject_id=subject.id,
            state=ItemState.LEARNING.value,
            srs_stage=stage,
            next_review_at=NOW - timedelta(minutes=5),
        )
    )
    await session.commit()
    return subject.id


async def answer(client, subject_id, question, text, confirm=False):
    return (
        await client.post(
            "/api/reviews/answer",
            json={
                "subject_id": subject_id,
                "question": question,
                "answer": text,
                "confirm": confirm,
            },
        )
    ).json()


async def log_count(session) -> int:
    return await session.scalar(select(func.count()).select_from(ReviewLog)) or 0


# --- soft answer -----------------------------------------------------------


async def test_a_wrong_answer_is_held_before_it_counts(client, session):
    subject_id = await seed(session)

    body = await answer(client, subject_id, "meaning", "below")

    assert body["held"] is True
    assert body["correct"] is False
    assert body["completed"] is False
    # Still owes everything: nothing was consumed.
    assert body["remaining"] == ["meaning", "reading"]
    assert body["srs_stage_before"] == body["srs_stage_after"] == 3


async def test_a_held_answer_reveals_nothing(client, session):
    """Otherwise the warning would be a reveal button with extra steps."""
    subject_id = await seed(session)

    body = await answer(client, subject_id, "meaning", "below")

    assert body["expected"] == ""
    assert body["subject"] is None
    assert "Above" not in str(body)


async def test_a_held_answer_changes_nothing_in_the_database(client, session):
    subject_id = await seed(session)

    await answer(client, subject_id, "meaning", "below")

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["srs_stage"] == 3
    assert item["progress"]["incorrect_count"] == 0
    assert await log_count(session) == 0


async def test_confirming_submits_the_answer_for_real(client, session):
    subject_id = await seed(session)

    await answer(client, subject_id, "meaning", "below")
    body = await answer(client, subject_id, "meaning", "below", confirm=True)

    assert body["held"] is False
    assert body["correct"] is False
    assert body["expected"] == "Above"
    assert await log_count(session) == 1


async def test_correcting_the_typo_instead_costs_nothing(client, session):
    """The whole point: a slip that is noticed is not a demotion."""
    subject_id = await seed(session)

    await answer(client, subject_id, "meaning", "abvoe")
    body = await answer(client, subject_id, "meaning", "above")

    assert body["correct"] is True
    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["incorrect_count"] == 0


async def test_soft_answer_can_be_switched_off(client, session):
    subject_id = await seed(session)
    await client.put("/api/settings", json={"soft_answer_enabled": False})

    body = await answer(client, subject_id, "meaning", "below")

    assert body["held"] is False
    assert body["expected"] == "Above"
    assert await log_count(session) == 1


async def test_a_correct_answer_is_never_held(client, session):
    subject_id = await seed(session)

    body = await answer(client, subject_id, "meaning", "above")

    assert body["held"] is False
    assert body["correct"] is True


# --- the other reading -----------------------------------------------------


async def test_the_other_reading_is_a_free_retry(client, session):
    """うえ is a real reading of 上; the question just asked for the on'yomi.

    WaniKani re-asks here rather than counting it wrong. Charging for it would
    punish knowing more than the question wanted.
    """
    subject_id = await seed(session)

    body = await answer(client, subject_id, "reading", "うえ")

    assert body["retry"] is True
    assert body["held"] is False
    assert body["correct"] is False
    assert body["remaining"] == ["meaning", "reading"]
    assert body["srs_stage_after"] == 3
    assert body["hint"] and "kunyomi" in body["hint"]


async def test_a_retry_costs_nothing_and_is_not_logged(client, session):
    subject_id = await seed(session)

    await answer(client, subject_id, "reading", "うえ")

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["incorrect_count"] == 0
    assert item["progress"]["srs_stage"] == 3
    assert await log_count(session) == 0


async def test_a_retry_reveals_nothing_either(client, session):
    subject_id = await seed(session)

    body = await answer(client, subject_id, "reading", "うえ")

    assert body["expected"] == ""
    assert body["subject"] is None


async def test_a_retry_is_not_overridable_by_confirming(client, session):
    """It is not a question of insistence: the answer was not wrong."""
    subject_id = await seed(session)

    body = await answer(client, subject_id, "reading", "うえ", confirm=True)

    assert body["retry"] is True
    assert await log_count(session) == 0


async def test_an_invented_reading_is_still_wrong(client, session):
    subject_id = await seed(session)

    held = await answer(client, subject_id, "reading", "ざつ")
    assert held["held"] is True
    assert held["retry"] is False

    body = await answer(client, subject_id, "reading", "ざつ", confirm=True)
    assert body["correct"] is False
    assert body["expected"] == "じょう"


# --- forgiven typos --------------------------------------------------------


async def test_a_forgiven_typo_says_so(client, session):
    """Silently accepting a misspelling teaches the misspelling."""
    subject_id = await seed(session)

    body = await answer(client, subject_id, "meaning", "abov")

    assert body["correct"] is True
    assert body["typo"] is True
    assert body["expected"] == "Above"


async def test_an_exactly_right_answer_is_not_flagged_as_a_typo(client, session):
    subject_id = await seed(session)

    body = await answer(client, subject_id, "meaning", "above")

    assert body["correct"] is True
    assert body["typo"] is False


async def test_a_secondary_reading_names_the_primary_one(client, session):
    """うえ is not accepted here, but a kanji with two accepted on'yomi is.

    Answering with the non-primary one is right, and the useful thing to show
    is the primary — not the reading just typed.
    """
    subject = Subject(
        wanikani_id=601,
        object_type="kanji",
        level=1,
        slug="one",
        characters="一",
        meanings=[{"meaning": "One", "primary": True, "accepted_answer": True}],
        readings=[
            {"reading": "いち", "primary": True, "accepted_answer": True, "type": "onyomi"},
            {"reading": "いつ", "primary": False, "accepted_answer": True, "type": "onyomi"},
        ],
    )
    session.add(subject)
    await session.flush()
    session.add(
        Progress(
            subject_id=subject.id,
            state=ItemState.LEARNING.value,
            srs_stage=2,
            next_review_at=NOW - timedelta(minutes=5),
        )
    )
    await session.commit()

    body = await answer(client, subject.id, "reading", "いつ")

    assert body["correct"] is True
    assert body["secondary"] is True
    assert body["expected"] == "いち"
