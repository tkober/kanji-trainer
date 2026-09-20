"""The lesson queue's level scoping, and the quiz that gates the SRS.

Both exist because of a concrete failure: importing a reset WaniKani account
made every one of ~9.400 items a lesson, so the screen reported "9.321 offen"
and offered no way to act on it — and the batch went straight into the SRS
without the learner ever having produced a single answer.
"""

from __future__ import annotations

from app.db import Progress, Subject
from app.srs import ItemState


async def seed(session, *, level: int, slug: str, wanikani_id: int) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type="kanji",
        level=level,
        slug=slug,
        characters=slug,
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
        readings=[
            {"reading": "じょう", "primary": True, "accepted_answer": True, "type": "onyomi"}
        ],
        meaning_mnemonic="A toe above the ground.",
        sort_order=level * 1000 + wanikani_id,
    )
    session.add(subject)
    await session.flush()
    session.add(Progress(subject_id=subject.id, state=ItemState.NEW.value))
    await session.commit()
    return subject.id


# --- level scoping ---------------------------------------------------------


async def test_the_queue_serves_the_lowest_level_that_still_has_lessons(client, session):
    await seed(session, level=12, slug="上", wanikani_id=1)
    await seed(session, level=3, slug="下", wanikani_id=2)
    await seed(session, level=40, slug="左", wanikani_id=3)

    body = (await client.get("/api/lessons")).json()

    assert body["level"] == 3
    assert body["total_in_level"] == 1
    # The collection-wide figure is still reported, as context rather than as
    # a to-do list.
    assert body["total_available"] == 3
    assert [item["subject"]["level"] for item in body["items"]] == [3]


async def test_a_level_can_be_asked_for_directly(client, session):
    await seed(session, level=3, slug="下", wanikani_id=2)
    await seed(session, level=40, slug="左", wanikani_id=3)

    body = (await client.get("/api/lessons", params={"level": 40})).json()

    assert body["level"] == 40
    assert [item["subject"]["level"] for item in body["items"]] == [40]


async def test_every_open_level_is_listed_for_the_picker(client, session):
    await seed(session, level=3, slug="下", wanikani_id=2)
    await seed(session, level=3, slug="右", wanikani_id=4)
    await seed(session, level=40, slug="左", wanikani_id=3)

    body = (await client.get("/api/lessons")).json()

    assert body["levels"] == [
        {"level": 3, "open_count": 2},
        {"level": 40, "open_count": 1},
    ]


async def test_an_empty_collection_reports_no_level_rather_than_failing(client):
    body = (await client.get("/api/lessons")).json()

    assert body["level"] is None
    assert body["items"] == []
    assert body["total_available"] == 0


async def test_the_batch_size_is_configurable_and_defaults_to_five(client, session):
    for n in range(8):
        await seed(session, level=1, slug=f"x{n}", wanikani_id=100 + n)

    assert len((await client.get("/api/lessons")).json()["items"]) == 5

    await client.put("/api/settings", json={"lesson_batch_size": 3})
    body = (await client.get("/api/lessons")).json()
    assert len(body["items"]) == 3
    assert body["batch_size"] == 3


async def test_the_badge_counts_the_current_level_not_the_collection(client, session):
    await seed(session, level=3, slug="下", wanikani_id=2)
    await seed(session, level=40, slug="左", wanikani_id=3)

    stats = (await client.get("/api/stats")).json()

    assert stats["current_level"] == 3
    assert stats["lessons_available"] == 1
    # The collection-wide count stays available for the breakdown.
    assert stats["new_count"] == 2


# --- the quiz --------------------------------------------------------------


async def test_the_quiz_checks_an_answer_without_moving_the_item(client, session):
    subject_id = await seed(session, level=1, slug="上", wanikani_id=1)

    body = (
        await client.post(
            "/api/lessons/quiz",
            json={"subject_id": subject_id, "question": "meaning", "answer": "above"},
        )
    ).json()
    assert body["correct"] is True
    assert body["expected"] == "Above"
    # No SRS fields at all: the quiz is a gate in front of the SRS, not part
    # of it.
    assert "srs_stage_after" not in body

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["state"] == "new"
    assert item["progress"]["srs_stage"] == 0


async def test_the_quiz_forgives_a_typo_and_converts_romaji(client, session):
    subject_id = await seed(session, level=1, slug="上", wanikani_id=1)

    meaning = await client.post(
        "/api/lessons/quiz",
        json={"subject_id": subject_id, "question": "meaning", "answer": "abov"},
    )
    reading = await client.post(
        "/api/lessons/quiz",
        json={"subject_id": subject_id, "question": "reading", "answer": "jou"},
    )

    assert meaning.json()["correct"] is True
    assert reading.json()["correct"] is True


async def test_a_transposition_in_a_short_meaning_is_not_forgiven(client, session):
    """Pinned because it surprises: "abvoe" for "Above" is rejected.

    Plain Levenshtein charges a swap of two adjacent letters as two edits,
    and a five-letter answer is allowed one. Damerau-Levenshtein would count
    it as one and let the most common typing error through — a deliberate
    choice to revisit, not an oversight, so it is asserted rather than left
    to be discovered in a review session.
    """
    subject_id = await seed(session, level=1, slug="上", wanikani_id=1)

    body = (
        await client.post(
            "/api/lessons/quiz",
            json={"subject_id": subject_id, "question": "meaning", "answer": "abvoe"},
        )
    ).json()

    assert body["correct"] is False


async def test_a_wrong_quiz_answer_costs_nothing(client, session):
    subject_id = await seed(session, level=1, slug="上", wanikani_id=1)

    body = (
        await client.post(
            "/api/lessons/quiz",
            json={"subject_id": subject_id, "question": "meaning", "answer": "below"},
        )
    ).json()

    assert body["correct"] is False
    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["state"] == "new"
    assert item["progress"]["incorrect_count"] == 0


async def test_the_quiz_refuses_items_already_in_the_rotation(client, session):
    """Otherwise it would hand out the answers the review queue withholds."""
    subject_id = await seed(session, level=1, slug="上", wanikani_id=1)
    await client.post("/api/lessons/start", json={"subject_ids": [subject_id]})

    response = await client.post(
        "/api/lessons/quiz",
        json={"subject_id": subject_id, "question": "meaning", "answer": "above"},
    )

    assert response.status_code == 409


async def test_starting_a_batch_reports_the_count_and_schedules_it(client, session):
    first = await seed(session, level=1, slug="上", wanikani_id=1)
    second = await seed(session, level=1, slug="下", wanikani_id=2)

    result = await client.post("/api/lessons/start", json={"subject_ids": [first, second]})
    assert result.json() == {"changed": 2}

    item = (await client.get(f"/api/items/{first}")).json()
    assert item["progress"]["state"] == "learning"
    assert item["progress"]["srs_stage"] == 1
    assert item["progress"]["next_review_at"] is not None

    assert (await client.get("/api/lessons")).json()["total_available"] == 0
