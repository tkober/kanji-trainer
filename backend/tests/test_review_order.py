"""The review queue's order settings -- see study.py's `_review_order`.

A fixed order breaks isolated SRS recall, and after a WaniKani import most
items share a due time, so the leftover `subject_id` tiebreak used to silently
reproduce WaniKani's own radicals-then-kanji-then-vocabulary insertion order.
These tests pin the replacement: two independent settings (item order, type
order), an in-flight item always leading, and ties broken randomly rather than
by id.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.config import Settings
from app.db import Progress, Subject
from app.runtime_config import build_runtime_config
from app.srs import ItemState

NOW = datetime.now(timezone.utc)


async def _seed(
    session,
    *,
    wanikani_id: int,
    object_type: str = "kanji",
    level: int = 1,
    stage: int = 2,
    due_minutes: int = -5,
    pending_meaning: bool | None = None,
    pending_reading: bool | None = None,
) -> int:
    """One due item, with everything the order settings can sort by exposed."""
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type=object_type,
        level=level,
        slug=f"s{wanikani_id}",
        characters="上",
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
        readings=(
            []
            if object_type == "radical"
            else [{"reading": "じょう", "primary": True, "accepted_answer": True, "type": "onyomi"}]
        ),
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
            pending_meaning=pending_meaning,
            pending_reading=pending_reading,
        )
    )
    await session.commit()
    return subject.id


# --- settings plumbing -------------------------------------------------------


async def test_default_settings_report_random_and_mixed(client):
    body = (await client.get("/api/settings")).json()
    assert body["review_item_order"] == "random"
    assert body["review_type_order"] == "mixed"


async def test_an_invalid_review_order_is_rejected(client):
    response = await client.put("/api/settings", json={"review_item_order": "alphabetical"})
    assert response.status_code == 422

    response = await client.put("/api/settings", json={"review_type_order": "chaos"})
    assert response.status_code == 422


async def test_a_valid_review_order_round_trips(client):
    response = await client.put(
        "/api/settings",
        json={"review_item_order": "oldest_first", "review_type_order": "grouped"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["review_item_order"] == "oldest_first"
    assert body["review_type_order"] == "grouped"

    # And it is not just echoed back -- a fresh GET agrees, so it was stored.
    again = (await client.get("/api/settings")).json()
    assert again["review_item_order"] == "oldest_first"
    assert again["review_type_order"] == "grouped"


def test_build_runtime_config_falls_back_on_a_garbage_stored_value():
    """A bad value -- an old option, a typo, anything not in the known set --
    must not be able to break the review screen. Plain object, no database:
    this is `build_runtime_config`'s own contract, same as `_parse_intervals`.
    """
    row = SimpleNamespace(
        wanikani_api_token=None,
        wanikani_known_srs_stage=None,
        known_srs_stage=None,
        srs_interval_hours=None,
        daily_lesson_limit=None,
        lesson_batch_size=None,
        soft_answer_enabled=None,
        review_item_order="alphabetical",
        review_type_order="chaos",
    )
    config = build_runtime_config(row, Settings())
    assert config.review_item_order == "random"
    assert config.review_type_order == "mixed"


# --- ordering, over the API ---------------------------------------------------


async def test_grouped_and_oldest_first_ignores_insertion_order(client, session):
    """Seeded vocab, kanji, radical in that order -- so a subject_id tiebreak
    would come back vocab, kanji, radical. Grouped + oldest_first must not.
    """
    await client.put(
        "/api/settings",
        json={"review_item_order": "oldest_first", "review_type_order": "grouped"},
    )

    await _seed(session, wanikani_id=1, object_type="vocabulary", due_minutes=-5)
    await _seed(session, wanikani_id=2, object_type="kanji", due_minutes=-5)
    await _seed(session, wanikani_id=3, object_type="radical", due_minutes=-5)

    body = (await client.get("/api/reviews")).json()
    types = [item["subject"]["object_type"] for item in body["items"]]
    assert types == ["radical", "kanji", "vocabulary"]


async def test_lowest_stage_first(client, session):
    await client.put("/api/settings", json={"review_item_order": "lowest_stage_first"})

    id_stage_4 = await _seed(session, wanikani_id=1, stage=4)
    id_stage_1 = await _seed(session, wanikani_id=2, stage=1)
    id_stage_3 = await _seed(session, wanikani_id=3, stage=3)

    body = (await client.get("/api/reviews")).json()
    ids = [item["subject"]["id"] for item in body["items"]]
    assert ids == [id_stage_1, id_stage_3, id_stage_4]


async def test_lowest_level_first(client, session):
    await client.put("/api/settings", json={"review_item_order": "lowest_level_first"})

    id_level_5 = await _seed(session, wanikani_id=1, level=5)
    id_level_2 = await _seed(session, wanikani_id=2, level=2)
    id_level_4 = await _seed(session, wanikani_id=3, level=4)

    body = (await client.get("/api/reviews")).json()
    ids = [item["subject"]["id"] for item in body["items"]]
    assert ids == [id_level_2, id_level_4, id_level_5]


async def test_an_item_in_flight_leads_even_under_oldest_first(client, session):
    """A closed tab left a half-answered item behind. It must be picked up
    right away rather than waiting its turn behind items that are also due,
    even under an order that would otherwise put it last.
    """
    await client.put("/api/settings", json={"review_item_order": "oldest_first"})

    due_soon_no_pending = await _seed(session, wanikani_id=1, due_minutes=-100)
    in_flight_due_later = await _seed(
        session, wanikani_id=2, due_minutes=-1, pending_meaning=True
    )

    body = (await client.get("/api/reviews")).json()
    ids = [item["subject"]["id"] for item in body["items"]]
    assert ids[0] == in_flight_due_later
    assert ids[1] == due_soon_no_pending


def _is_grouped_by_type(types: list[str]) -> bool:
    """True if the sequence forms one contiguous run per distinct type."""
    runs = 0
    previous = None
    for kind in types:
        if kind != previous:
            runs += 1
            previous = kind
    return runs == len(set(types))


async def test_mixed_and_random_do_not_reproduce_the_grouped_order(client, session):
    """Default settings: 30 items across 3 types (10 each), seeded in strict
    type-grouped id order -- exactly what a `subject_id` tiebreak would have
    reproduced under the old fixed order. With no type grouping and a random
    item order, a fetch coming back "grouped by type" purely by chance has
    probability 6 * (10!)^3 / 30! (choose which of the 3! block orders, times
    any internal permutation of each block, over every permutation of 30
    items) -- about 1.1e-12. Five independent fetches make a false failure
    astronomically unlikely while still catching the old, deterministic
    behaviour on the very first one.
    """
    for i in range(10):
        await _seed(session, wanikani_id=i, object_type="radical", due_minutes=-5)
    for i in range(10, 20):
        await _seed(session, wanikani_id=i, object_type="kanji", due_minutes=-5)
    for i in range(20, 30):
        await _seed(session, wanikani_id=i, object_type="vocabulary", due_minutes=-5)

    saw_ungrouped = False
    for _ in range(5):
        body = (await client.get("/api/reviews", params={"limit": 500})).json()
        types = [item["subject"]["object_type"] for item in body["items"]]
        assert len(types) == 30
        if not _is_grouped_by_type(types):
            saw_ungrouped = True
            break

    assert saw_ungrouped
