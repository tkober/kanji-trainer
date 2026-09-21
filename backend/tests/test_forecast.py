"""The review forecast.

The number that matters is the cumulative one: "how many will be waiting by
Friday" is the question the lesson button actually depends on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import Progress, Subject
from app.srs import ItemState

NOW = datetime.now(timezone.utc)
#: The forecast's first bucket. Offsets are measured from here rather than
#: from `now`, or whether two items share an hour would depend on the minute
#: the suite happens to run at.
HOUR = NOW.replace(minute=0, second=0, microsecond=0)


def at(hours: int, minutes: int = 0) -> datetime:
    """An absolute due time inside a known bucket."""
    return HOUR + timedelta(hours=hours, minutes=minutes)


async def seed(session, *, wanikani_id: int, stage: int, due: datetime | None) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type="kanji",
        level=1,
        slug=f"s{wanikani_id}",
        characters="上",
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
    )
    session.add(subject)
    await session.flush()
    session.add(
        Progress(
            subject_id=subject.id,
            state=ItemState.LEARNING.value if due is not None else ItemState.NEW.value,
            srs_stage=stage,
            next_review_at=due,
        )
    )
    await session.commit()
    return subject.id


async def test_an_empty_collection_still_returns_a_full_window(client):
    body = (await client.get("/api/forecast", params={"hours": 24})).json()

    assert body["due_now"] == 0
    assert body["total"] == 0
    assert len(body["buckets"]) == 24
    assert all(bucket["count"] == 0 for bucket in body["buckets"])


async def test_items_land_in_the_hour_they_are_due(client, session):
    # Two inside the same hour, one clearly in another.
    await seed(session, wanikani_id=1, stage=2, due=at(3, 10))
    await seed(session, wanikani_id=2, stage=2, due=at(3, 50))
    await seed(session, wanikani_id=3, stage=2, due=at(9, 30))

    body = (await client.get("/api/forecast", params={"hours": 24})).json()

    nonempty = [b for b in body["buckets"] if b["count"] > 0]
    assert [b["count"] for b in nonempty] == [2, 1]
    assert body["total"] == 3


async def test_overdue_items_open_the_cumulative_line_instead_of_a_bar(client, session):
    """They are not arriving -- they are already here."""
    await seed(session, wanikani_id=1, stage=2, due=NOW - timedelta(hours=5))
    await seed(session, wanikani_id=2, stage=2, due=at(2, 30))

    body = (await client.get("/api/forecast", params={"hours": 24})).json()

    assert body["due_now"] == 1
    assert body["total"] == 1
    # The very first bucket already carries the overdue item.
    assert body["buckets"][0]["cumulative"] == 1
    assert body["buckets"][-1]["cumulative"] == 2


async def test_the_cumulative_line_only_ever_climbs(client, session):
    for n in range(6):
        await seed(session, wanikani_id=n + 1, stage=2, due=at(n * 3 + 2, 30))

    body = (await client.get("/api/forecast", params={"hours": 24})).json()

    values = [b["cumulative"] for b in body["buckets"]]
    assert values == sorted(values)
    assert values[-1] == 6


async def test_bars_are_split_by_where_the_items_stand(client, session):
    await seed(session, wanikani_id=1, stage=2, due=at(2, 10))  # apprentice
    await seed(session, wanikani_id=2, stage=6, due=at(2, 20))  # guru
    await seed(session, wanikani_id=3, stage=8, due=at(2, 30))  # master band

    body = (await client.get("/api/forecast", params={"hours": 24})).json()
    bucket = next(b for b in body["buckets"] if b["count"] > 0)

    assert bucket["apprentice"] == 1
    assert bucket["guru"] == 1
    assert bucket["master"] == 1
    assert bucket["count"] == 3


async def test_items_outside_the_window_are_left_out(client, session):
    await seed(session, wanikani_id=1, stage=2, due=at(2, 30))
    await seed(session, wanikani_id=2, stage=7, due=at(400))

    body = (await client.get("/api/forecast", params={"hours": 24})).json()

    assert body["total"] == 1


async def test_unlearned_and_retired_items_never_appear(client, session):
    await seed(session, wanikani_id=1, stage=0, due=None)
    burned = await seed(session, wanikani_id=2, stage=9, due=None)
    await client.post("/api/items/known", json={"subject_ids": [burned]})

    body = (await client.get("/api/forecast", params={"hours": 168})).json()

    assert body["total"] == 0
    assert body["due_now"] == 0


async def test_a_hidden_item_drops_out_of_the_forecast(client, session):
    subject_id = await seed(session, wanikani_id=1, stage=3, due=at(2, 30))
    assert (await client.get("/api/forecast", params={"hours": 24})).json()["total"] == 1

    await client.post("/api/items/suspend", json={"subject_ids": [subject_id]})

    assert (await client.get("/api/forecast", params={"hours": 24})).json()["total"] == 0
