"""`GET /api/stats/growth` -- the HTTP layer around `app.growth`. The
reconstruction's own rules are pinned down in `test_growth.py`; this file
only checks the query/response plumbing: that the last day agrees with
`/stats/levels` and `/stats`, that `max_level` actually filters, and that the
import marker lands where `import_runs` says it did.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import ImportRun, Progress, ReviewLog, Subject
from app.srs import ItemState

NOW = datetime.now(timezone.utc)
IMPORTED_AT = NOW - timedelta(days=30)


async def seed_subject(
    session,
    *,
    wanikani_id: int,
    level: int = 1,
    object_type: str = "kanji",
    state: str,
    stage: int = 0,
    known_at: datetime | None = None,
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
        Progress(subject_id=subject.id, state=state, srs_stage=stage, known_at=known_at)
    )
    await session.commit()
    return subject.id


async def seed_import(session, *, finished_at: datetime) -> None:
    session.add(
        ImportRun(
            status="succeeded",
            known_srs_stage=5,
            started_at=finished_at - timedelta(minutes=5),
            finished_at=finished_at,
        )
    )
    await session.commit()


async def cross_guru(session, *, subject_id: int, answered_at: datetime) -> None:
    session.add(
        ReviewLog(
            subject_id=subject_id,
            question_type="meaning",
            correct=True,
            srs_stage_before=4,
            srs_stage_after=5,
            answered_at=answered_at,
        )
    )
    await session.commit()


async def test_growth_last_day_matches_levels_and_stats(client, session):
    await seed_import(session, finished_at=IMPORTED_AT)
    # Known from the import, level 1.
    await seed_subject(
        session, wanikani_id=1, level=1, state=ItemState.KNOWN.value, stage=9, known_at=IMPORTED_AT
    )
    # Earned via an in-app review, level 2.
    earned_id = await seed_subject(
        session, wanikani_id=2, level=2, state=ItemState.LEARNING.value, stage=5
    )
    await cross_guru(session, subject_id=earned_id, answered_at=NOW - timedelta(days=10))
    # A new item, contributes nothing.
    await seed_subject(session, wanikani_id=3, level=3, state=ItemState.NEW.value)
    # Suspended, but was earned once -- must not show up at all.
    suspended_id = await seed_subject(
        session, wanikani_id=4, level=1, state=ItemState.SUSPENDED.value, stage=5
    )
    await cross_guru(session, subject_id=suspended_id, answered_at=NOW - timedelta(days=20))

    growth = (await client.get("/api/stats/growth")).json()
    levels = (await client.get("/api/stats/levels")).json()
    stats = (await client.get("/api/stats")).json()

    last_day = growth["days"][-1]
    level_earned = sum(
        entry["counts"]["guru_1"]
        + entry["counts"]["guru_2"]
        + entry["counts"]["master"]
        + entry["counts"]["enlightened"]
        + entry["counts"]["burned"]
        for entry in levels["levels"]
    )
    level_known = sum(entry["counts"]["known"] for entry in levels["levels"])

    assert last_day["earned"] == level_earned == 1
    assert last_day["known"] == level_known == 1
    assert last_day["earned"] == stats["burned_count"] + stats["guru_count"] + stats["master_count"] + (
        stats["enlightened_count"]
    )
    assert last_day["known"] == stats["known_count"]


async def test_growth_max_level_filters_subjects(client, session):
    await seed_import(session, finished_at=IMPORTED_AT)
    await seed_subject(
        session, wanikani_id=1, level=1, state=ItemState.KNOWN.value, stage=9, known_at=IMPORTED_AT
    )
    earned_id = await seed_subject(
        session, wanikani_id=2, level=5, state=ItemState.LEARNING.value, stage=5
    )
    await cross_guru(session, subject_id=earned_id, answered_at=NOW - timedelta(days=10))
    await session.commit()

    unfiltered = (await client.get("/api/stats/growth")).json()
    filtered = (await client.get("/api/stats/growth", params={"max_level": 1})).json()

    assert unfiltered["days"][-1]["earned"] == 1
    assert unfiltered["days"][-1]["known"] == 1
    assert filtered["days"][-1]["earned"] == 0
    assert filtered["days"][-1]["known"] == 1


async def test_growth_import_marker_lands_on_the_import_date(client, session):
    await seed_import(session, finished_at=IMPORTED_AT)
    await seed_subject(
        session, wanikani_id=1, level=1, state=ItemState.KNOWN.value, stage=9, known_at=IMPORTED_AT
    )

    body = (await client.get("/api/stats/growth")).json()

    assert body["imported_on"] == IMPORTED_AT.date().isoformat()
    assert body["known_on_import"] == 1


async def test_growth_days_param_clamps_to_available_history(client, session):
    await seed_import(session, finished_at=IMPORTED_AT)
    await seed_subject(
        session, wanikani_id=1, level=1, state=ItemState.KNOWN.value, stage=9, known_at=IMPORTED_AT
    )

    body = (await client.get("/api/stats/growth", params={"days": 365})).json()

    # Only ~30 days of history exist; the response is not padded out to 365.
    assert len(body["days"]) < 365
    assert body["days"][0]["date"] == IMPORTED_AT.date().isoformat()


async def test_growth_invalid_tz_is_rejected(client):
    response = await client.get("/api/stats/growth", params={"tz": "Not/AZone"})

    assert response.status_code == 422
