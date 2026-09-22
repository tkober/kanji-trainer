"""End-to-end over the HTTP surface, against a real database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import Progress, Subject
from app.srs import ItemState

NOW = datetime.now(timezone.utc)


async def seed_kanji(session, *, stage: int = 2, due_minutes: int = -5) -> int:
    """A kanji due five minutes ago, at Apprentice II."""
    subject = Subject(
        wanikani_id=440,
        object_type="kanji",
        level=1,
        slug="上",
        characters="上",
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
        auxiliary_meanings=[],
        readings=[
            {"reading": "じょう", "primary": True, "accepted_answer": True, "type": "onyomi"}
        ],
        component_subject_ids=[],
        parts_of_speech=[],
        meaning_mnemonic="A toe above the ground.",
    )
    session.add(subject)
    await session.flush()

    session.add(
        Progress(
            subject_id=subject.id,
            state=ItemState.LEARNING.value,
            srs_stage=stage,
            next_review_at=NOW + timedelta(minutes=due_minutes),
        )
    )
    await session.commit()
    return subject.id


async def test_health_reports_the_database(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": True}


async def test_a_full_review_advances_the_item(client, session):
    subject_id = await seed_kanji(session)

    queue = (await client.get("/api/reviews")).json()
    assert queue["total_due"] == 1
    assert queue["items"][0]["questions"] == ["meaning", "reading"]
    # The queue must not ship the answers.
    assert "meanings" not in queue["items"][0]["subject"]

    first = await client.post(
        "/api/reviews/answer",
        json={"subject_id": subject_id, "question": "meaning", "answer": "above"},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["correct"] and not body["completed"]
    assert body["remaining"] == ["reading"]
    # Now that it is answered, the detail comes with it.
    assert body["subject"]["meaning_mnemonic"]

    second = await client.post(
        "/api/reviews/answer",
        json={"subject_id": subject_id, "question": "reading", "answer": "jou"},
    )
    body = second.json()
    assert body["correct"] and body["completed"]
    assert body["srs_stage_before"] == 2
    assert body["srs_stage_after"] == 3
    assert body["next_review_at"] is not None

    assert (await client.get("/api/reviews")).json()["total_due"] == 0


async def test_answering_the_same_question_twice_is_rejected(client, session):
    subject_id = await seed_kanji(session)

    await client.post(
        "/api/reviews/answer",
        json={"subject_id": subject_id, "question": "meaning", "answer": "above"},
    )
    repeat = await client.post(
        "/api/reviews/answer",
        json={"subject_id": subject_id, "question": "meaning", "answer": "above"},
    )
    assert repeat.status_code == 409


async def test_a_wrong_answer_keeps_the_item_due(client, session):
    subject_id = await seed_kanji(session)

    # Soft answer is on by default, so the first attempt is held; confirming
    # is what actually submits it. See tests/test_second_chance.py.
    for confirm in (False, True):
        response = await client.post(
            "/api/reviews/answer",
            json={
                "subject_id": subject_id,
                "question": "meaning",
                "answer": "below",
                "confirm": confirm,
            },
        )
        body = response.json()
        assert not body["correct"]

    assert body["expected"] == "Above"
    assert body["remaining"] == ["meaning", "reading"]

    assert (await client.get("/api/reviews")).json()["total_due"] == 1


async def test_marking_known_takes_an_item_out_of_the_queue(client, session):
    subject_id = await seed_kanji(session)

    response = await client.post("/api/items/known", json={"subject_ids": [subject_id]})
    assert response.json() == {"changed": 1}

    assert (await client.get("/api/reviews")).json()["total_due"] == 0

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["state"] == "known"
    assert item["progress"]["srs_stage"] == 9
    assert item["progress"]["known_at"] is not None


async def test_marking_known_at_a_lower_stage_keeps_one_check_up(client, session):
    subject_id = await seed_kanji(session)

    await client.post(
        "/api/items/known", json={"subject_ids": [subject_id], "known_stage": 8}
    )

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["srs_stage"] == 8
    assert item["progress"]["next_review_at"] is not None


async def test_reset_puts_a_declared_item_back_into_the_rotation(client, session):
    subject_id = await seed_kanji(session)
    await client.post("/api/items/known", json={"subject_ids": [subject_id]})

    await client.post("/api/items/reset", json={"subject_ids": [subject_id]})

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["progress"]["state"] == "learning"
    assert item["progress"]["srs_stage"] == 1
    assert item["progress"]["known_at"] is None


async def test_suspend_and_unsuspend_round_trip(client, session):
    subject_id = await seed_kanji(session)

    await client.post("/api/items/suspend", json={"subject_ids": [subject_id]})
    assert (await client.get("/api/reviews")).json()["total_due"] == 0

    changed = await client.post("/api/items/unsuspend", json={"subject_ids": [subject_id]})
    assert changed.json() == {"changed": 1}
    assert (await client.get("/api/reviews")).json()["total_due"] == 1


async def test_the_level_filter_offers_the_levels_that_were_imported(client, session):
    await seed_kanji(session)
    far_away = Subject(
        wanikani_id=3,
        object_type="kanji",
        level=7,
        slug="下",
        characters="下",
        meanings=[{"meaning": "Below", "primary": True, "accepted_answer": True}],
    )
    session.add(far_away)
    await session.flush()
    session.add(Progress(subject_id=far_away.id, state=ItemState.NEW.value))
    await session.commit()

    # Not a range: a lapsed subscription imports level 1 and 7 and nothing in
    # between, and the filter must not offer the levels that are not there.
    assert (await client.get("/api/items/levels")).json() == [1, 7]


async def test_the_detail_links_to_wanikani_but_the_queue_does_not(client, session):
    subject_id = await seed_kanji(session)

    item = (await client.get(f"/api/items/{subject_id}")).json()
    assert item["subject"]["wanikani_url"] == "https://www.wanikani.com/kanji/%E4%B8%8A"

    # That page names the meaning, so the link is an answer like any other.
    queue_item = (await client.get("/api/reviews")).json()["items"][0]
    assert "wanikani_url" not in queue_item["subject"]


async def test_a_hand_added_item_has_no_wanikani_link(client, session):
    own = Subject(
        wanikani_id=None,
        object_type="kanji",
        level=1,
        slug="猫",
        characters="猫",
        meanings=[{"meaning": "Cat", "primary": True, "accepted_answer": True}],
    )
    session.add(own)
    await session.flush()
    session.add(Progress(subject_id=own.id, state=ItemState.NEW.value))
    await session.commit()

    item = (await client.get(f"/api/items/{own.id}")).json()
    assert item["subject"]["wanikani_url"] is None


async def test_lessons_offer_unlearned_items_and_starting_them_schedules_a_review(
    client, session
):
    subject = Subject(
        wanikani_id=1,
        object_type="radical",
        level=1,
        slug="ground",
        characters="一",
        meanings=[{"meaning": "Ground", "primary": True, "accepted_answer": True}],
    )
    session.add(subject)
    await session.flush()
    session.add(Progress(subject_id=subject.id, state=ItemState.NEW.value))
    await session.commit()

    lessons = (await client.get("/api/lessons")).json()
    assert lessons["total_available"] == 1
    assert lessons["items"][0]["subject"]["characters"] == "一"

    await client.post("/api/lessons/start", json={"subject_ids": [subject.id]})

    item = (await client.get(f"/api/items/{subject.id}")).json()
    assert item["progress"]["state"] == "learning"
    assert item["progress"]["srs_stage"] == 1


async def test_stats_separate_declared_items_from_earned_ones(client, session):
    # One item burned the hard way...
    await seed_kanji(session, stage=9, due_minutes=0)

    # ...and one the learner simply declared.
    declared = Subject(
        wanikani_id=2,
        object_type="radical",
        level=1,
        slug="stick",
        characters="丨",
        meanings=[{"meaning": "Stick", "primary": True, "accepted_answer": True}],
    )
    session.add(declared)
    await session.flush()
    session.add(Progress(subject_id=declared.id, state=ItemState.NEW.value))
    await session.commit()
    await client.post("/api/items/known", json={"subject_ids": [declared.id]})

    stats = (await client.get("/api/stats")).json()
    assert stats["total_subjects"] == 2
    assert stats["known_count"] == 1
    # The declared item sits at stage 9 too, but must not be counted as burned:
    # blurring the two is exactly what would make this dashboard flattering
    # instead of useful.
    assert stats["burned_count"] == 1


async def test_settings_never_return_the_token(client):
    await client.put("/api/settings", json={"wanikani_api_token": "abcd-efgh-1234-wxyz"})

    body = (await client.get("/api/settings")).json()
    assert body["wanikani_token_set"] is True
    assert body["wanikani_token_hint"] == "…wxyz"
    assert "abcd-efgh-1234-wxyz" not in str(body)


async def test_clearing_the_token_falls_back_to_the_environment(client):
    await client.put("/api/settings", json={"wanikani_api_token": "something"})
    await client.put("/api/settings", json={"wanikani_api_token": ""})

    body = (await client.get("/api/settings")).json()
    assert body["wanikani_token_set"] is False


async def test_import_without_a_token_is_refused(client):
    response = await client.post("/api/import", json={})
    assert response.status_code == 400
