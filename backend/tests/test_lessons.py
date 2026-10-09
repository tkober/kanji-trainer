"""The lesson selection view, and the quiz that gates the SRS.

Both exist because of a concrete failure: importing a reset WaniKani account
made every one of ~9.400 items a lesson, so the screen reported "9.321 offen"
and offered no way to act on it — and the batch went straight into the SRS
without the learner ever having produced a single answer.

The selection view goes one step further than a queue: it shows the whole
level, learned items included, tiled by SRS stage, so the learner picks
exactly what to work on rather than taking whatever came off the top.
"""

from __future__ import annotations

from app.db import Progress, Subject
from app.srs import ItemState


async def seed(
    session,
    *,
    level: int,
    slug: str,
    wanikani_id: int,
    state: str = ItemState.NEW.value,
    srs_stage: int = 0,
    object_type: str = "kanji",
) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type=object_type,
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
    session.add(Progress(subject_id=subject.id, state=state, srs_stage=srs_stage))
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
    assert [tile["subject"]["level"] for tile in body["tiles"]] == [3]


async def test_a_level_can_be_asked_for_directly(client, session):
    await seed(session, level=3, slug="下", wanikani_id=2)
    await seed(session, level=40, slug="左", wanikani_id=3)

    body = (await client.get("/api/lessons", params={"level": 40})).json()

    assert body["level"] == 40
    assert [tile["subject"]["level"] for tile in body["tiles"]] == [40]


async def test_tiles_include_the_whole_level_not_just_the_open_items(client, session):
    """A tile carries no answers, whatever state the item is in -- see the
    invariant that a queue item (and this is the same shape) must not leak
    meanings or readings for something already in the review rotation."""
    new_id = await seed(session, level=3, slug="上", wanikani_id=1)
    learning_id = await seed(
        session, level=3, slug="下", wanikani_id=2, state=ItemState.LEARNING.value, srs_stage=3
    )
    known_id = await seed(
        session, level=3, slug="左", wanikani_id=3, state=ItemState.KNOWN.value, srs_stage=9
    )

    body = (await client.get("/api/lessons", params={"level": 3})).json()

    by_id = {tile["subject"]["id"]: tile for tile in body["tiles"]}
    assert set(by_id) == {new_id, learning_id, known_id}
    assert by_id[new_id]["state"] == "new"
    assert by_id[new_id]["srs_stage"] == 0
    assert by_id[learning_id]["state"] == "learning"
    assert by_id[learning_id]["srs_stage"] == 3
    assert by_id[known_id]["state"] == "known"
    assert by_id[known_id]["srs_stage"] == 9
    # Only open in the level, not the whole level, is the number for the
    # picker and the header.
    assert body["total_in_level"] == 1

    for tile in body["tiles"]:
        assert "meanings" not in tile["subject"]
        assert "readings" not in tile["subject"]


async def test_every_level_with_any_subject_is_listed_including_finished_ones(client, session):
    await seed(session, level=3, slug="下", wanikani_id=2)
    await seed(session, level=3, slug="右", wanikani_id=4)
    await seed(
        session, level=5, slug="左", wanikani_id=3, state=ItemState.KNOWN.value, srs_stage=9
    )
    await seed(session, level=40, slug="上", wanikani_id=1)

    body = (await client.get("/api/lessons")).json()

    assert body["levels"] == [
        {
            "level": 3,
            "open_count": 2,
            "total_count": 2,
            "open_radicals": 0,
            "open_kanji": 2,
            "open_vocabulary": 0,
        },
        {
            "level": 5,
            "open_count": 0,
            "total_count": 1,
            "open_radicals": 0,
            "open_kanji": 0,
            "open_vocabulary": 0,
        },
        {
            "level": 40,
            "open_count": 1,
            "total_count": 1,
            "open_radicals": 0,
            "open_kanji": 1,
            "open_vocabulary": 0,
        },
    ]
    # The default level stays the lowest one that still has something open,
    # not the lowest level overall (5 sits between 3 and 40 but is finished).
    assert body["level"] == 3


async def test_level_summaries_split_open_counts_by_type(client, session):
    """`kana_vocabulary` counts as vocabulary, and a learned item of any type
    does not count as open, whatever type it is."""
    await seed(session, level=7, slug="radical1", wanikani_id=1, object_type="radical")
    await seed(session, level=7, slug="上", wanikani_id=2, object_type="kanji")
    await seed(session, level=7, slug="下", wanikani_id=3, object_type="kanji")
    await seed(session, level=7, slug="vocab1", wanikani_id=4, object_type="vocabulary")
    await seed(session, level=7, slug="kanavocab1", wanikani_id=5, object_type="kana_vocabulary")
    await seed(
        session,
        level=7,
        slug="radical2",
        wanikani_id=6,
        object_type="radical",
        state=ItemState.KNOWN.value,
        srs_stage=9,
    )

    body = (await client.get("/api/lessons")).json()
    entry = next(level for level in body["levels"] if level["level"] == 7)

    assert entry["open_radicals"] == 1
    assert entry["open_kanji"] == 2
    # vocabulary + kana_vocabulary together
    assert entry["open_vocabulary"] == 2
    assert entry["open_count"] == 5
    assert entry["total_count"] == 6


async def test_an_empty_collection_reports_no_level_rather_than_failing(client):
    body = (await client.get("/api/lessons")).json()

    assert body["level"] is None
    assert body["tiles"] == []
    assert body["total_available"] == 0


async def test_the_batch_size_is_configurable(client, session):
    for n in range(8):
        await seed(session, level=1, slug=f"x{n}", wanikani_id=100 + n)

    assert (await client.get("/api/lessons")).json()["batch_size"] == 5

    await client.put("/api/settings", json={"lesson_batch_size": 3})
    body = (await client.get("/api/lessons")).json()
    assert body["batch_size"] == 3
    # The batch size no longer caps what the selection view shows -- that is
    # a client-side chunking choice made once items are picked.
    assert len(body["tiles"]) == 8


async def test_the_badge_counts_the_current_level_not_the_collection(client, session):
    await seed(session, level=3, slug="下", wanikani_id=2)
    await seed(session, level=40, slug="左", wanikani_id=3)

    stats = (await client.get("/api/stats")).json()

    assert stats["current_level"] == 3
    assert stats["lessons_available"] == 1
    # The collection-wide count stays available for the breakdown.
    assert stats["new_count"] == 2


# --- remaining_today ---------------------------------------------------


async def test_remaining_today_is_none_without_a_limit(client, session):
    await seed(session, level=1, slug="上", wanikani_id=1)

    body = (await client.get("/api/lessons")).json()

    assert body["remaining_today"] is None


async def test_remaining_today_decreases_after_starting_lessons(client, session):
    first = await seed(session, level=1, slug="上", wanikani_id=1)
    second = await seed(session, level=1, slug="下", wanikani_id=2)

    await client.put("/api/settings", json={"daily_lesson_limit": 5})

    before = (await client.get("/api/lessons")).json()
    assert before["remaining_today"] == 5

    await client.post("/api/lessons/start", json={"subject_ids": [first, second]})

    after = (await client.get("/api/lessons")).json()
    assert after["remaining_today"] == 3


# --- GET /api/lessons/items ----------------------------------------------


async def test_lessons_items_returns_detail_only_for_ids_still_new(client, session):
    new_id = await seed(session, level=1, slug="上", wanikani_id=1)
    learning_id = await seed(
        session, level=1, slug="下", wanikani_id=2, state=ItemState.LEARNING.value, srs_stage=2
    )

    body = (
        await client.get("/api/lessons/items", params={"ids": [new_id, learning_id]})
    ).json()

    assert [item["subject"]["id"] for item in body] == [new_id]
    assert body[0]["subject"]["meanings"]
    assert body[0]["srs_stage"] == 0


async def test_lessons_items_rejects_more_than_200_ids(client):
    response = await client.get("/api/lessons/items", params={"ids": list(range(1, 202))})
    assert response.status_code == 422


async def test_lessons_items_with_no_ids_returns_empty(client):
    assert (await client.get("/api/lessons/items")).json() == []


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


async def test_a_given_up_quiz_answer_is_wrong_and_reveals_the_answer(client, session):
    """Alt+H in the quiz: counts as wrong and names the expected answer, same
    as any other wrong quiz answer -- the quiz already moves nothing."""
    subject_id = await seed(session, level=1, slug="上", wanikani_id=1)

    body = (
        await client.post(
            "/api/lessons/quiz",
            json={
                "subject_id": subject_id,
                "question": "meaning",
                "answer": "above",
                "gave_up": True,
            },
        )
    ).json()

    assert body["correct"] is False
    assert body["retry"] is False
    assert body["expected"] == "Above"

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["state"] == "new"
    assert item["progress"]["incorrect_count"] == 0


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
