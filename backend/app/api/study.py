"""Lessons and reviews -- the two queues the learner actually works in."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import srs
from ..answers import check_meaning, check_reading
from ..db import Progress, ReviewLog, Subject, get_session
from ..models import (
    AnswerIn,
    AnswerOut,
    LessonItem,
    Lessons,
    Queue,
    QueueItem,
    StartLessonsIn,
)
from ..runtime_config import load_runtime_config
from ..serialize import subject_detail, subject_summary
from ..srs import ItemState, QuestionType

router = APIRouter()


def _questions(progress: Progress, subject: Subject) -> list[QuestionType]:
    """What this item still owes.

    An item with no review in flight owes everything its type is asked; one
    that was half-answered before the tab closed owes only the rest. That
    persistence is the reason ``pending_*`` live in the database.
    """
    pending = srs.outstanding(progress)
    if pending or progress.pending_meaning is not None or progress.pending_reading is not None:
        return list(pending)
    return list(srs.required_questions(subject.object_type))


# --- reviews ---------------------------------------------------------------


@router.get("/reviews", response_model=Queue)
async def review_queue(
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> Queue:
    """Items that are due, soonest first."""
    config = await load_runtime_config(session)
    cutoff = srs.due_cutoff(datetime.now(timezone.utc), config.review_grace_minutes)

    due = (
        Progress.state == ItemState.LEARNING.value,
        Progress.next_review_at.is_not(None),
        Progress.next_review_at <= cutoff,
    )

    total = await session.scalar(select(func.count()).select_from(Progress).where(*due)) or 0
    rows = await session.execute(
        select(Progress, Subject)
        .join(Subject, Subject.id == Progress.subject_id)
        .where(*due)
        .order_by(Progress.next_review_at, Progress.subject_id)
        .limit(limit)
    )

    return Queue(
        items=[
            QueueItem(
                subject=subject_summary(subject),
                srs_stage=progress.srs_stage,
                stage_name=srs.stage_name(progress.srs_stage),
                questions=_questions(progress, subject),
            )
            for progress, subject in rows
        ],
        total_due=total,
    )


@router.post("/reviews/answer", response_model=AnswerOut)
async def submit_answer(
    payload: AnswerIn, session: AsyncSession = Depends(get_session)
) -> AnswerOut:
    """Check one answer and move the item if it has answered everything."""
    config = await load_runtime_config(session)
    now = datetime.now(timezone.utc)

    row = (
        await session.execute(
            select(Progress, Subject)
            .join(Subject, Subject.id == Progress.subject_id)
            .where(Progress.subject_id == payload.subject_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Subject not found.")
    progress, subject = row

    if progress.state != ItemState.LEARNING.value:
        raise HTTPException(
            status_code=409, detail="This item is not currently in the review rotation."
        )

    srs.begin_review(progress, subject.object_type)
    if payload.question not in srs.outstanding(progress):
        # The browser is a step ahead of the server -- a double submit, or a
        # queue fetched before another device answered the same item.
        raise HTTPException(
            status_code=409, detail="That question has already been answered for this item."
        )

    if payload.question is QuestionType.MEANING:
        check = check_meaning(
            payload.answer,
            subject.meanings,
            subject.auxiliary_meanings,
            config.meaning_typo_tolerance_divisor,
        )
    else:
        check = check_reading(payload.answer, subject.readings, subject.object_type)

    outcome = srs.apply_answer(
        progress,
        subject.object_type,
        payload.question,
        check.correct,
        now,
        config.srs_intervals,
    )
    progress.updated_at = now

    session.add(
        ReviewLog(
            subject_id=subject.id,
            question_type=payload.question.value,
            correct=check.correct,
            given_answer=payload.answer[:200],
            srs_stage_before=outcome.stage_before,
            srs_stage_after=outcome.stage_after,
            answered_at=now,
        )
    )
    await session.commit()

    return AnswerOut(
        correct=check.correct,
        expected=check.expected,
        secondary=check.secondary,
        hint=check.hint,
        completed=outcome.completed,
        remaining=list(outcome.remaining),
        srs_stage_before=outcome.stage_before,
        srs_stage_after=outcome.stage_after,
        stage_name_after=srs.stage_name(outcome.stage_after),
        next_review_at=outcome.next_review_at,
        subject=subject_detail(subject),
    )


# --- lessons ---------------------------------------------------------------


@router.get("/lessons", response_model=Lessons)
async def lesson_queue(
    limit: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> Lessons:
    """Items not yet learned, in WaniKani's teaching order.

    Unlike WaniKani there is no gate here: nothing waits for its radicals to
    reach Guru first. Ordering by level and type still presents them in a
    sensible sequence, but a learner who wants to jump ahead may.
    """
    config = await load_runtime_config(session)
    new_only = Progress.state == ItemState.NEW.value

    total = await session.scalar(select(func.count()).select_from(Progress).where(new_only)) or 0

    remaining = limit
    if config.daily_lesson_limit:
        started_today = (
            await session.scalar(
                select(func.count())
                .select_from(Progress)
                .where(Progress.started_at.is_not(None), Progress.started_at >= _midnight())
            )
            or 0
        )
        remaining = max(0, min(limit, config.daily_lesson_limit - started_today))

    rows = await session.execute(
        select(Progress, Subject)
        .join(Subject, Subject.id == Progress.subject_id)
        .where(new_only)
        .order_by(Subject.sort_order, Subject.id)
        .limit(remaining)
    )

    return Lessons(
        items=[
            LessonItem(subject=subject_detail(subject), srs_stage=progress.srs_stage)
            for progress, subject in rows
        ],
        total_available=total,
        daily_limit=config.daily_lesson_limit,
    )


def _midnight() -> datetime:
    """Start of the current UTC day.

    UTC rather than Europe/Berlin: the daily lesson limit is a pacing device,
    not an appointment, and a boundary that shifts twice a year is a worse
    surprise than one that falls at 01:00 or 02:00 local.
    """
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@router.post("/lessons/start", response_model=Lessons)
async def start_lessons(
    payload: StartLessonsIn, session: AsyncSession = Depends(get_session)
) -> Lessons:
    """Move the given items into the review rotation at Apprentice I."""
    if not payload.subject_ids:
        raise HTTPException(status_code=400, detail="No subjects given.")

    config = await load_runtime_config(session)
    now = datetime.now(timezone.utc)

    rows = await session.execute(
        select(Progress).where(
            Progress.subject_id.in_(payload.subject_ids),
            Progress.state == ItemState.NEW.value,
        )
    )
    for progress in rows.scalars():
        srs.start_lesson(progress, now, config.srs_intervals)
        progress.updated_at = now

    await session.commit()
    return await lesson_queue(limit=20, session=session)
