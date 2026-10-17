"""`POST /api/practice/answer` (issue #95, epic #85).

Practice is the lesson quiz's sibling for items already in the rotation: it
checks an answer exactly like a review, but moves nothing -- no `ReviewLog`,
no `Progress` change, no commit. The one thing worth getting right is the
rule for when that is safe: an item may be practised exactly when it is not
also sitting in (or mid-answer in) `GET /api/reviews` right now, so a review
and a practice attempt never compete for the same question.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import Progress, ReviewLog, Subject
from app.srs import ItemState
from sqlalchemy import func, select

NOW = datetime.now(timezone.utc)


async def seed(
    session,
    *,
    state: str = ItemState.LEARNING.value,
    srs_stage: int = 3,
    next_review_at: datetime | None = None,
    pending_meaning: bool | None = None,
    pending_reading: bool | None = None,
    wanikani_id: int = 700,
    slug: str = "上",
    characters: str = "上",
    object_type: str = "kanji",
    component_subject_ids: list[int] | None = None,
) -> int:
    """A kanji taught by its on'yomi, same shape as `test_second_chance.seed`,
    but with a ``Progress`` row the caller controls directly rather than one
    derived from a due time -- the practice rule cares about several columns
    at once, not just ``next_review_at``.
    """
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type=object_type,
        level=2,
        slug=slug,
        characters=characters,
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
        auxiliary_meanings=[],
        readings=[
            {"reading": "じょう", "primary": True, "accepted_answer": True, "type": "onyomi"},
            {"reading": "うえ", "primary": False, "accepted_answer": False, "type": "kunyomi"},
        ],
        component_subject_ids=component_subject_ids or [],
        parts_of_speech=[],
        meaning_mnemonic="A toe above the ground.",
    )
    session.add(subject)
    await session.flush()
    session.add(
        Progress(
            subject_id=subject.id,
            state=state,
            srs_stage=srs_stage,
            next_review_at=next_review_at,
            pending_meaning=pending_meaning,
            pending_reading=pending_reading,
        )
    )
    await session.commit()
    return subject.id


async def practice(client, subject_id, question, answer, gave_up=False):
    return await client.post(
        "/api/practice/answer",
        json={
            "subject_id": subject_id,
            "question": question,
            "answer": answer,
            "gave_up": gave_up,
        },
    )


async def log_count(session) -> int:
    return await session.scalar(select(func.count()).select_from(ReviewLog)) or 0


async def progress_snapshot(session, subject_id) -> dict:
    """Every column of the `Progress` row, read fresh rather than from the
    identity map -- the whole point of the "nothing written" tests below."""
    session.expire_all()
    progress = await session.get(Progress, subject_id)
    return {
        "state": progress.state,
        "srs_stage": progress.srs_stage,
        "next_review_at": progress.next_review_at,
        "pending_meaning": progress.pending_meaning,
        "pending_reading": progress.pending_reading,
        "session_incorrect": progress.session_incorrect,
        "correct_count": progress.correct_count,
        "incorrect_count": progress.incorrect_count,
        "lapses": progress.lapses,
        "started_at": progress.started_at,
        "passed_at": progress.passed_at,
        "burned_at": progress.burned_at,
        "known_at": progress.known_at,
        "updated_at": progress.updated_at,
    }


# --- the rule: when practice is allowed -------------------------------------


async def test_learning_and_not_due_is_practisable(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))

    response = await practice(client, subject_id, "meaning", "above")

    assert response.status_code == 200


async def test_known_is_practisable(client, session):
    subject_id = await seed(session, state=ItemState.KNOWN.value, next_review_at=None)

    response = await practice(client, subject_id, "meaning", "above")

    assert response.status_code == 200


async def test_due_is_refused(client, session):
    subject_id = await seed(session, next_review_at=NOW - timedelta(minutes=5))

    response = await practice(client, subject_id, "meaning", "above")

    assert response.status_code == 409
    assert "Reviews" in response.json()["detail"]


async def test_due_within_the_grace_window_is_refused(client, session):
    """`review_grace_minutes` defaults to 59 -- an item ten minutes out is
    already due for `GET /api/reviews`, so it must not also be practisable."""
    subject_id = await seed(session, next_review_at=NOW + timedelta(minutes=10))

    response = await practice(client, subject_id, "meaning", "above")

    assert response.status_code == 409


async def test_a_review_in_flight_is_refused(client, session):
    subject_id = await seed(
        session,
        next_review_at=NOW + timedelta(hours=2),
        pending_meaning=True,
        pending_reading=False,
    )

    response = await practice(client, subject_id, "meaning", "above")

    assert response.status_code == 409
    assert "Reviews" in response.json()["detail"]


async def test_a_lesson_not_yet_taken_is_refused(client, session):
    subject_id = await seed(session, state=ItemState.NEW.value, next_review_at=None)

    response = await practice(client, subject_id, "meaning", "above")

    assert response.status_code == 409
    assert "lesson" in response.json()["detail"]


async def test_a_suspended_item_is_refused(client, session):
    subject_id = await seed(session, state=ItemState.SUSPENDED.value, next_review_at=None)

    response = await practice(client, subject_id, "meaning", "above")

    assert response.status_code == 409
    assert "suspended" in response.json()["detail"]


async def test_an_unknown_subject_is_404(client):
    response = await practice(client, 999999, "meaning", "above")

    assert response.status_code == 404


# --- checking, same as a review ---------------------------------------------


async def test_a_synonym_is_accepted(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))
    await client.put(f"/api/items/{subject_id}/synonyms", json={"synonyms": ["Rise"]})

    body = (await practice(client, subject_id, "meaning", "Rise")).json()

    assert body["correct"] is True
    assert body["secondary"] is True


async def test_a_typo_is_forgiven_and_flagged(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))

    body = (await practice(client, subject_id, "meaning", "abov")).json()

    assert body["correct"] is True
    assert body["typo"] is True
    assert body["expected"] == "Above"


async def test_the_kanjis_reading_on_a_single_kanji_vocabulary_is_a_free_retry(client, session):
    """Same confusion as `test_api.test_the_kanjis_reading_on_its_vocabulary_
    is_a_retry_not_a_miss`, but for an item that is not due -- the practice
    endpoint runs the identical check, retry included."""
    kanji = Subject(
        wanikani_id=711,
        object_type="kanji",
        level=1,
        slug="month",
        characters="月",
        meanings=[{"meaning": "Month", "primary": True, "accepted_answer": True}],
        auxiliary_meanings=[],
        readings=[
            {"reading": "げつ", "primary": True, "accepted_answer": True, "type": "onyomi"},
            {"reading": "がつ", "primary": False, "accepted_answer": True, "type": "onyomi"},
            {"reading": "つき", "primary": False, "accepted_answer": False, "type": "kunyomi"},
        ],
        component_subject_ids=[],
        parts_of_speech=[],
        meaning_mnemonic="A moon.",
    )
    session.add(kanji)
    await session.flush()

    vocabulary_id = await seed(
        session,
        slug="month-vocab",
        characters="月",
        wanikani_id=712,
        object_type="vocabulary",
        next_review_at=NOW + timedelta(hours=2),
        component_subject_ids=[kanji.id],
    )
    # The seeded vocabulary's readings above are the generic 上/じょう fixture
    # ones; overwrite them with 月's own, as seed_tsuki_vocabulary does in
    # test_api.py.
    vocabulary = await session.get(Subject, vocabulary_id)
    vocabulary.readings = [{"reading": "つき", "primary": True, "accepted_answer": True}]
    await session.commit()

    body = (await practice(client, vocabulary_id, "reading", "getsu")).json()

    assert body["retry"] is True
    assert body["correct"] is False
    assert body["hint"] is not None
    # Unlike a review's retry, nothing here is held back: the item is not in
    # the rotation's active question, so there is no reveal to protect
    # against -- the subject detail still comes back for "Show item".
    assert body["subject"] is not None
    assert body["subject"]["meaning_mnemonic"]


async def test_giving_up_is_wrong_and_reveals_the_expected_answer(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))

    body = (await practice(client, subject_id, "meaning", "", gave_up=True)).json()

    assert body["correct"] is False
    assert body["expected"] == "Above"


async def test_the_response_carries_the_subject_with_synonyms(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))
    await client.put(f"/api/items/{subject_id}/synonyms", json={"synonyms": ["Rise"]})

    body = (await practice(client, subject_id, "meaning", "above")).json()

    assert body["subject"]["id"] == subject_id
    assert body["subject"]["synonyms"] == ["Rise"]
    assert body["subject"]["meaning_mnemonic"]


# --- nothing written ---------------------------------------------------------


async def test_a_correct_answer_writes_nothing(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))
    before = await progress_snapshot(session, subject_id)
    logs_before = await log_count(session)

    body = (await practice(client, subject_id, "meaning", "above")).json()
    assert body["correct"] is True

    assert await progress_snapshot(session, subject_id) == before
    assert await log_count(session) == logs_before


async def test_a_wrong_answer_writes_nothing(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))
    before = await progress_snapshot(session, subject_id)
    logs_before = await log_count(session)

    body = (await practice(client, subject_id, "meaning", "below")).json()
    assert body["correct"] is False

    assert await progress_snapshot(session, subject_id) == before
    assert await log_count(session) == logs_before


async def test_a_given_up_answer_writes_nothing(client, session):
    subject_id = await seed(session, next_review_at=NOW + timedelta(hours=2))
    before = await progress_snapshot(session, subject_id)
    logs_before = await log_count(session)

    body = (await practice(client, subject_id, "meaning", "", gave_up=True)).json()
    assert body["correct"] is False

    assert await progress_snapshot(session, subject_id) == before
    assert await log_count(session) == logs_before


# --- the question must be one the subject type is asked ---------------------


async def test_a_question_the_subject_type_is_not_asked_is_refused(client, session):
    """A radical has no reading at all -- same shape of refusal the review
    endpoint gives for the equivalent case (`srs.required_questions`)."""
    subject = Subject(
        wanikani_id=720,
        object_type="radical",
        level=1,
        slug="ground",
        characters="一",
        meanings=[{"meaning": "Ground", "primary": True, "accepted_answer": True}],
        auxiliary_meanings=[],
        readings=[],
        component_subject_ids=[],
        parts_of_speech=[],
        meaning_mnemonic="A single stroke along the ground.",
    )
    session.add(subject)
    await session.flush()
    session.add(
        Progress(
            subject_id=subject.id,
            state=ItemState.LEARNING.value,
            srs_stage=1,
            next_review_at=NOW + timedelta(hours=2),
        )
    )
    await session.commit()

    response = await practice(client, subject.id, "reading", "じ")

    assert response.status_code == 409
