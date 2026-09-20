"""The dashboard numbers.

Declared-known items are counted separately from earned ones throughout. That
distinction is the whole reason ``state`` and ``srs_stage`` are both stored,
and a dashboard that blurred it would make the trainer flattering rather than
useful.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Progress, ReviewLog, get_session
from ..models import Stats
from ..runtime_config import load_runtime_config
from ..srs import (
    STAGE_APPRENTICE_1,
    STAGE_BURNED,
    STAGE_ENLIGHTENED,
    STAGE_GURU_1,
    STAGE_MASTER,
    ItemState,
    due_cutoff,
)

router = APIRouter()


@router.get("", response_model=Stats)
async def read_stats(session: AsyncSession = Depends(get_session)) -> Stats:
    config = await load_runtime_config(session)
    now = datetime.now(timezone.utc)
    cutoff = due_cutoff(now, config.review_grace_minutes)

    async def count(*filters: object) -> int:
        return await session.scalar(
            select(func.count()).select_from(Progress).where(*filters)
        ) or 0

    learning = Progress.state == ItemState.LEARNING.value
    scheduled = (learning, Progress.next_review_at.is_not(None))

    reviews_today = (
        await session.scalar(
            select(func.count())
            .select_from(ReviewLog)
            .where(ReviewLog.answered_at >= now.replace(hour=0, minute=0, second=0, microsecond=0))
        )
        or 0
    )
    correct_today = (
        await session.scalar(
            select(func.count())
            .select_from(ReviewLog)
            .where(
                ReviewLog.answered_at
                >= now.replace(hour=0, minute=0, second=0, microsecond=0),
                ReviewLog.correct.is_(True),
            )
        )
        or 0
    )

    return Stats(
        total_subjects=await count(),
        new_count=await count(Progress.state == ItemState.NEW.value),
        learning_count=await count(learning),
        known_count=await count(Progress.state == ItemState.KNOWN.value),
        suspended_count=await count(Progress.state == ItemState.SUSPENDED.value),
        # Burned counts only items that got there by review: a declared-known
        # item sits at the same stage and would otherwise inflate this.
        burned_count=await count(learning, Progress.srs_stage >= STAGE_BURNED),
        apprentice_count=await count(
            learning,
            Progress.srs_stage >= STAGE_APPRENTICE_1,
            Progress.srs_stage < STAGE_GURU_1,
        ),
        guru_count=await count(
            learning, Progress.srs_stage >= STAGE_GURU_1, Progress.srs_stage < STAGE_MASTER
        ),
        master_count=await count(learning, Progress.srs_stage == STAGE_MASTER),
        enlightened_count=await count(learning, Progress.srs_stage == STAGE_ENLIGHTENED),
        due_now=await count(*scheduled, Progress.next_review_at <= cutoff),
        due_next_hour=await count(*scheduled, Progress.next_review_at <= now + timedelta(hours=1)),
        due_today=await count(*scheduled, Progress.next_review_at <= now + timedelta(days=1)),
        reviews_today=reviews_today,
        accuracy_today=(correct_today / reviews_today) if reviews_today else None,
    )
