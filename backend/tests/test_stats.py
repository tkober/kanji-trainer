"""The dashboard numbers, the level breakdown and the activity calendar.

Declared-known items must stay out of `burned`/Guru counts everywhere here --
same invariant `/stats`' own `burned_count` protects, now extended to
`/stats/levels` and the Guru-crossing counters. Guru crossings are counted by
*subject*, not by review_log row, since a lapse and a re-pass inside the same
window must not count twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import Progress, ReviewLog, Subject
from app.srs import STAGE_BURNED, STAGE_GURU_1, ItemState

NOW = datetime.now(timezone.utc)
#: Anchor for "next review" tests, same reasoning as test_forecast.py's HOUR:
#: offsets measured from `now` would put two items in the same hour or not
#: depending on the minute the suite happens to run at.
HOUR = NOW.replace(minute=0, second=0, microsecond=0)


async def seed_item(
    session,
    *,
    wanikani_id: int,
    object_type: str = "kanji",
    level: int = 1,
    state: str,
    stage: int = 0,
    next_review_at: datetime | None = None,
    started_at: datetime | None = None,
    lapses: int = 0,
    correct_count: int = 0,
    incorrect_count: int = 0,
) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type=object_type,
        level=level,
        slug=f"s{wanikani_id}",
        characters="上",
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
    )
    session.add(subject)
    await session.flush()
    session.add(
        Progress(
            subject_id=subject.id,
            state=state,
            srs_stage=stage,
            next_review_at=next_review_at,
            started_at=started_at,
            lapses=lapses,
            correct_count=correct_count,
            incorrect_count=incorrect_count,
        )
    )
    await session.commit()
    return subject.id


async def log_answer(
    session,
    *,
    subject_id: int,
    answered_at: datetime,
    stage_before: int,
    stage_after: int,
    correct: bool = True,
    question_type: str = "meaning",
) -> None:
    session.add(
        ReviewLog(
            subject_id=subject_id,
            question_type=question_type,
            correct=correct,
            srs_stage_before=stage_before,
            srs_stage_after=stage_after,
            answered_at=answered_at,
        )
    )
    await session.commit()


# --- /api/stats: next review ------------------------------------------------


async def test_next_review_is_none_when_nothing_is_scheduled(client):
    body = (await client.get("/api/stats")).json()

    assert body["next_review_at"] is None
    assert body["next_review_count"] == 0


async def test_an_item_inside_the_grace_window_is_due_not_next(client, session):
    """Default grace is 59 minutes -- inside it an item is due, so it must
    not surface as "next" either."""
    await seed_item(
        session,
        wanikani_id=1,
        state=ItemState.LEARNING.value,
        stage=2,
        next_review_at=NOW + timedelta(minutes=20),
    )
    await seed_item(
        session,
        wanikani_id=2,
        state=ItemState.LEARNING.value,
        stage=2,
        next_review_at=HOUR + timedelta(hours=3),
    )

    body = (await client.get("/api/stats")).json()

    assert body["next_review_at"] is not None
    assert datetime.fromisoformat(body["next_review_at"]) == HOUR + timedelta(hours=3)
    assert body["next_review_count"] == 1


async def test_next_review_count_is_scoped_to_the_same_clock_hour(client, session):
    await seed_item(
        session,
        wanikani_id=1,
        state=ItemState.LEARNING.value,
        stage=2,
        next_review_at=HOUR + timedelta(hours=3, minutes=10),
    )
    await seed_item(
        session,
        wanikani_id=2,
        state=ItemState.LEARNING.value,
        stage=2,
        next_review_at=HOUR + timedelta(hours=3, minutes=50),
    )
    await seed_item(
        session,
        wanikani_id=3,
        state=ItemState.LEARNING.value,
        stage=2,
        next_review_at=HOUR + timedelta(hours=4, minutes=5),
    )

    body = (await client.get("/api/stats")).json()

    assert datetime.fromisoformat(body["next_review_at"]) == HOUR + timedelta(hours=3, minutes=10)
    assert body["next_review_count"] == 2


# --- /api/stats: Guru crossings ---------------------------------------------


async def test_non_crossing_answers_do_not_count_as_guru(client, session):
    subject_id = await seed_item(session, wanikani_id=1, state=ItemState.LEARNING.value, stage=3)
    await log_answer(
        session, subject_id=subject_id, answered_at=NOW - timedelta(hours=1), stage_before=2, stage_after=3
    )

    body = (await client.get("/api/stats")).json()

    assert body["guru_last_7_days"] == 0


async def test_a_lapse_and_repass_inside_the_window_counts_the_subject_once(client, session):
    subject_id = await seed_item(session, wanikani_id=1, state=ItemState.LEARNING.value, stage=5)
    await log_answer(
        session, subject_id=subject_id, answered_at=NOW - timedelta(days=2), stage_before=4, stage_after=5
    )
    await log_answer(
        session,
        subject_id=subject_id,
        answered_at=NOW - timedelta(days=1, hours=12),
        stage_before=5,
        stage_after=4,
        correct=False,
    )
    await log_answer(
        session, subject_id=subject_id, answered_at=NOW - timedelta(hours=6), stage_before=4, stage_after=5
    )

    body = (await client.get("/api/stats")).json()

    assert body["guru_last_7_days"] == 1


async def test_guru_previous_week_window(client, session):
    recent = await seed_item(session, wanikani_id=1, state=ItemState.LEARNING.value, stage=5)
    older = await seed_item(session, wanikani_id=2, state=ItemState.LEARNING.value, stage=5)
    await log_answer(
        session, subject_id=recent, answered_at=NOW - timedelta(days=3), stage_before=4, stage_after=5
    )
    await log_answer(
        session, subject_id=older, answered_at=NOW - timedelta(days=10), stage_before=4, stage_after=5
    )

    body = (await client.get("/api/stats")).json()

    assert body["guru_last_7_days"] == 1
    assert body["guru_previous_7_days"] == 1


# --- /api/stats: tz ----------------------------------------------------------


async def test_stats_today_window_respects_tz(client, session):
    """An answer just before UTC midnight is already "today" in a zone ahead
    of UTC -- Berlin is always at least one hour ahead, DST or not."""
    subject_id = await seed_item(session, wanikani_id=1, state=ItemState.LEARNING.value, stage=2)
    today_utc_start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    await log_answer(
        session,
        subject_id=subject_id,
        answered_at=today_utc_start - timedelta(minutes=30),
        stage_before=2,
        stage_after=2,
    )

    utc_body = (await client.get("/api/stats")).json()
    berlin_body = (await client.get("/api/stats", params={"tz": "Europe/Berlin"})).json()

    assert utc_body["reviews_today"] == 0
    assert berlin_body["reviews_today"] == 1


async def test_an_invalid_tz_is_rejected(client):
    response = await client.get("/api/stats", params={"tz": "Not/AZone"})

    assert response.status_code == 422
    assert "Not/AZone" in response.json()["detail"]


# --- /api/stats/levels -------------------------------------------------------


async def test_a_known_item_is_counted_as_known_never_burned(client, session):
    await seed_item(
        session,
        wanikani_id=1,
        object_type="kanji",
        level=3,
        state=ItemState.KNOWN.value,
        stage=STAGE_BURNED,
    )

    body = (await client.get("/api/stats/levels")).json()

    entry = next(e for e in body["levels"] if e["level"] == 3 and e["object_type"] == "kanji")
    assert entry["counts"]["known"] == 1
    assert entry["counts"]["burned"] == 0


async def test_level_bucketing_covers_every_stage_and_suspended(client, session):
    for stage in range(1, 10):
        await seed_item(
            session,
            wanikani_id=stage,
            object_type="kanji",
            level=5,
            state=ItemState.LEARNING.value,
            stage=stage,
        )
    await seed_item(
        session, wanikani_id=100, object_type="kanji", level=5, state=ItemState.SUSPENDED.value, stage=2
    )
    await seed_item(
        session, wanikani_id=101, object_type="kanji", level=5, state=ItemState.NEW.value, stage=0
    )

    body = (await client.get("/api/stats/levels")).json()

    entry = next(e for e in body["levels"] if e["level"] == 5 and e["object_type"] == "kanji")
    assert entry["counts"] == {
        "new": 1,
        "apprentice_1": 1,
        "apprentice_2": 1,
        "apprentice_3": 1,
        "apprentice_4": 1,
        "guru_1": 1,
        "guru_2": 1,
        "master": 1,
        "enlightened": 1,
        "burned": 1,
        "known": 0,
        "suspended": 1,
    }


async def test_levels_reports_the_current_level(client, session):
    await seed_item(session, wanikani_id=1, level=1, state=ItemState.KNOWN.value, stage=STAGE_BURNED)
    await seed_item(session, wanikani_id=2, level=2, state=ItemState.NEW.value, stage=0)

    body = (await client.get("/api/stats/levels")).json()

    # Level 1 has nothing left to learn; level 2 is where the lesson queue is.
    assert body["current_level"] == 2


# --- /api/stats/activity -----------------------------------------------------


async def test_activity_zero_fills_the_whole_window(client):
    body = (await client.get("/api/stats/activity", params={"days": 5})).json()

    assert len(body["days"]) == 5
    assert all(day["answers"] == 0 and day["correct"] == 0 for day in body["days"])
    assert body["days"][-1]["date"] == NOW.date().isoformat()
    assert body["streak"] == 0


async def test_activity_tz_moves_a_late_utc_answer_to_the_next_local_day(client, session):
    subject_id = await seed_item(session, wanikani_id=1, state=ItemState.LEARNING.value, stage=2)
    day = (NOW - timedelta(days=100)).date()
    answered_at = datetime(day.year, day.month, day.day, 23, 30, tzinfo=timezone.utc)
    await log_answer(
        session, subject_id=subject_id, answered_at=answered_at, stage_before=2, stage_after=2
    )

    utc_body = (await client.get("/api/stats/activity", params={"days": 200})).json()
    berlin_body = (
        await client.get("/api/stats/activity", params={"days": 200, "tz": "Europe/Berlin"})
    ).json()

    utc_day = next(d for d in utc_body["days"] if d["answers"] > 0)
    berlin_day = next(d for d in berlin_body["days"] if d["answers"] > 0)
    assert utc_day["date"] == day.isoformat()
    assert berlin_day["date"] == (day + timedelta(days=1)).isoformat()


async def test_activity_counts_lessons_started_and_reached_guru_per_day(client, session):
    subject_id = await seed_item(
        session,
        wanikani_id=1,
        state=ItemState.LEARNING.value,
        stage=5,
        started_at=NOW - timedelta(days=1),
    )
    other_subject = await seed_item(session, wanikani_id=2, state=ItemState.LEARNING.value, stage=5)
    await log_answer(
        session,
        subject_id=subject_id,
        answered_at=NOW - timedelta(days=1),
        stage_before=4,
        stage_after=5,
    )
    # A second crossing the same day, different subject -- two distinct
    # subjects reaching Guru that day, not one row double counted.
    await log_answer(
        session,
        subject_id=other_subject,
        answered_at=NOW - timedelta(days=1),
        stage_before=4,
        stage_after=5,
    )

    body = (await client.get("/api/stats/activity", params={"days": 5})).json()

    day = next(d for d in body["days"] if d["date"] == (NOW - timedelta(days=1)).date().isoformat())
    assert day["lessons_started"] == 1
    assert day["reached_guru"] == 2


async def test_streak_counts_consecutive_days_ending_today(client, session):
    subject_id = await seed_item(session, wanikani_id=1, state=ItemState.LEARNING.value, stage=2)
    for offset in range(3):  # today, yesterday, the day before
        day = (NOW - timedelta(days=offset)).date()
        at = datetime(day.year, day.month, day.day, 10, 0, tzinfo=timezone.utc)
        await log_answer(session, subject_id=subject_id, answered_at=at, stage_before=2, stage_after=2)

    body = (await client.get("/api/stats/activity", params={"days": 10})).json()

    assert body["streak"] == 3


async def test_streak_ends_yesterday_when_today_has_no_answer_yet(client, session):
    subject_id = await seed_item(session, wanikani_id=1, state=ItemState.LEARNING.value, stage=2)
    for offset in (1, 2):  # yesterday and the day before, nothing today
        day = (NOW - timedelta(days=offset)).date()
        at = datetime(day.year, day.month, day.day, 10, 0, tzinfo=timezone.utc)
        await log_answer(session, subject_id=subject_id, answered_at=at, stage_before=2, stage_after=2)

    body = (await client.get("/api/stats/activity", params={"days": 10})).json()

    assert body["streak"] == 2


async def test_an_invalid_tz_is_rejected_for_activity(client):
    response = await client.get("/api/stats/activity", params={"tz": "Not/AZone"})

    assert response.status_code == 422


# --- /api/items?sort=lapses ---------------------------------------------------


async def test_items_sort_lapses_orders_by_lapses_then_incorrect_ratio(client, session):
    worst_ratio = await seed_item(
        session,
        wanikani_id=1,
        state=ItemState.LEARNING.value,
        stage=3,
        lapses=2,
        correct_count=2,
        incorrect_count=8,
    )
    same_lapses_better_ratio = await seed_item(
        session,
        wanikani_id=2,
        state=ItemState.LEARNING.value,
        stage=3,
        lapses=2,
        correct_count=8,
        incorrect_count=2,
    )
    no_lapses = await seed_item(
        session,
        wanikani_id=3,
        state=ItemState.LEARNING.value,
        stage=3,
        lapses=0,
        correct_count=5,
        incorrect_count=0,
    )

    body = (await client.get("/api/items", params={"sort": "lapses", "limit": 10})).json()
    ids = [item["subject"]["id"] for item in body["items"]]

    assert ids.index(worst_ratio) < ids.index(same_lapses_better_ratio) < ids.index(no_lapses)


async def test_items_invalid_sort_is_rejected(client):
    response = await client.get("/api/items", params={"sort": "bogus"})

    assert response.status_code == 422
