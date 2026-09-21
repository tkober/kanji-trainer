"""The review forecast: what is coming, and when.

WaniKani's version of this screen is the one that tells a learner whether
tomorrow is going to be twenty reviews or three hundred — which is the
decision "should I do lessons today" actually depends on. Without it the
lesson button is a bet.

Bucketing happens in Python rather than in SQL on purpose. Postgres wants
``date_trunc`` and SQLite wants ``strftime``, and that difference would have to
be collected in db.py with the rest of the dialect split; for a few thousand
timestamps it is not worth a second code path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Progress, get_session
from ..models import Forecast, ForecastBucket
from ..runtime_config import load_runtime_config
from ..srs import (
    STAGE_APPRENTICE_1,
    STAGE_GURU_1,
    STAGE_MASTER,
    ItemState,
    due_cutoff,
)

router = APIRouter()


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _band(stage: int) -> str:
    """Which colour band a bar segment belongs to.

    Apprentice items dominate a forecast and come back within two days;
    everything above Guru arrives in weeks. Splitting the bar says whether
    tomorrow's pile is new material or old material returning, which is a
    different problem to have.
    """
    if stage < STAGE_GURU_1:
        return "apprentice"
    if stage < STAGE_MASTER:
        return "guru"
    return "master"


@router.get("", response_model=Forecast)
async def read_forecast(
    hours: int = Query(default=168, ge=1, le=744),
    session: AsyncSession = Depends(get_session),
) -> Forecast:
    """Hourly buckets from the next full hour onwards.

    Empty hours are included rather than skipped: a forecast with the quiet
    stretches collapsed out shows an even workload that is not there.
    """
    config = await load_runtime_config(session)
    now = datetime.now(timezone.utc)
    start = _floor_hour(now)
    end = start + timedelta(hours=hours)

    # Everything already overdue is the pile the learner starts from, so it is
    # the opening value of the cumulative line rather than a bar of its own.
    cutoff = due_cutoff(now, config.review_grace_minutes)

    rows = await session.execute(
        select(Progress.next_review_at, Progress.srs_stage).where(
            Progress.state == ItemState.LEARNING.value,
            Progress.next_review_at.is_not(None),
            Progress.next_review_at < end,
            Progress.srs_stage >= STAGE_APPRENTICE_1,
        )
    )

    due_now = 0
    counts: dict[datetime, dict[str, int]] = {}
    for review_at, stage in rows:
        if review_at <= cutoff:
            due_now += 1
            continue
        bucket = _floor_hour(review_at)
        slot = counts.setdefault(bucket, {"apprentice": 0, "guru": 0, "master": 0})
        slot[_band(stage)] += 1

    buckets: list[ForecastBucket] = []
    running = due_now
    for offset in range(hours):
        at = start + timedelta(hours=offset)
        slot = counts.get(at, {"apprentice": 0, "guru": 0, "master": 0})
        count = slot["apprentice"] + slot["guru"] + slot["master"]
        running += count
        buckets.append(
            ForecastBucket(
                at=at,
                count=count,
                apprentice=slot["apprentice"],
                guru=slot["guru"],
                master=slot["master"],
                cumulative=running,
            )
        )

    return Forecast(
        now=now,
        due_now=due_now,
        hours=hours,
        total=running - due_now,
        buckets=buckets,
    )
