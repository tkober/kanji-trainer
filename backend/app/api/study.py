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
    LevelSummary,
    OverrideResult,
    Queue,
    QueueItem,
    QuizIn,
    QuizOut,
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

    # --- the two second chances, both of which leave the item untouched ---
    #
    # Neither commits, so the `begin_review` above is rolled back with the
    # session and the item is exactly as it was. Neither reveals the expected
    # answer either: the question is still open, and a warning that showed the
    # answer would be a free reveal on demand.

    if check.retry:
        # A real reading of this character, of the type that was not asked.
        # WaniKani re-asks instead of counting it wrong, and charging for it
        # would punish knowing more than the question wanted.
        return _still_open(progress, subject, retry=True, hint=check.hint)

    if not check.correct and not payload.confirm and config.soft_answer_enabled:
        # Hold it and ask once. A typo otherwise costs exactly what not
        # knowing the item costs, and below four characters there is no typo
        # tolerance at all to catch it.
        return _still_open(progress, subject, held=True)

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
        typo=check.typo,
        hint=check.hint,
        completed=outcome.completed,
        remaining=list(outcome.remaining),
        srs_stage_before=outcome.stage_before,
        srs_stage_after=outcome.stage_after,
        stage_name_after=srs.stage_name(outcome.stage_after),
        next_review_at=outcome.next_review_at,
        subject=subject_detail(subject),
    )


def _still_open(
    progress: Progress,
    subject: Subject,
    *,
    held: bool = False,
    retry: bool = False,
    hint: str | None = None,
) -> AnswerOut:
    """A verdict that changes nothing and keeps the question open.

    Deliberately carries no ``expected`` and no ``subject``: the learner is
    about to answer this question again, and handing over the answer first
    would turn either second chance into a reveal button.
    """
    return AnswerOut(
        correct=False,
        expected="",
        hint=hint,
        held=held,
        retry=retry,
        completed=False,
        remaining=list(srs.outstanding(progress)),
        srs_stage_before=progress.srs_stage,
        srs_stage_after=progress.srs_stage,
        stage_name_after=srs.stage_name(progress.srs_stage),
        next_review_at=progress.next_review_at,
        subject=None,
    )


# --- lessons ---------------------------------------------------------------


async def _open_levels(session: AsyncSession) -> list[LevelSummary]:
    """Every level that still has unlearned items, with how many."""
    rows = await session.execute(
        select(Subject.level, func.count())
        .join(Progress, Progress.subject_id == Subject.id)
        .where(Progress.state == ItemState.NEW.value)
        .group_by(Subject.level)
        .order_by(Subject.level)
    )
    return [LevelSummary(level=level, open_count=count) for level, count in rows]


async def lowest_open_level(session: AsyncSession) -> int | None:
    """The level the learner is effectively on: the lowest with lessons left."""
    return await session.scalar(
        select(func.min(Subject.level))
        .select_from(Subject)
        .join(Progress, Progress.subject_id == Subject.id)
        .where(Progress.state == ItemState.NEW.value)
    )


async def _lessons_started_today(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Progress)
            .where(Progress.started_at.is_not(None), Progress.started_at >= _midnight())
        )
        or 0
    )


@router.get("/lessons", response_model=Lessons)
async def lesson_queue(
    level: int | None = Query(default=None, ge=1),
    limit: int | None = Query(default=None, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> Lessons:
    """One batch of lessons from a single level, in WaniKani's teaching order.

    Scoped to a level, defaulting to the lowest one that still has anything
    left. There is still no *gate* — pass ``level`` and you may learn level 40
    while level 3 is untouched, which is the freedom WaniKani does not give.
    What is gone is the unscoped count: "9.321 offen" is a number nobody can
    act on, and it buries the five items that actually come next.
    """
    config = await load_runtime_config(session)
    new_only = Progress.state == ItemState.NEW.value

    total = await session.scalar(select(func.count()).select_from(Progress).where(new_only)) or 0
    levels = await _open_levels(session)

    chosen = level if level is not None else (levels[0].level if levels else None)
    in_level = next((entry.open_count for entry in levels if entry.level == chosen), 0)

    batch = limit or config.lesson_batch_size
    remaining = batch
    if config.daily_lesson_limit:
        remaining = max(
            0, min(batch, config.daily_lesson_limit - await _lessons_started_today(session))
        )

    items: list[LessonItem] = []
    if chosen is not None and remaining > 0:
        rows = await session.execute(
            select(Progress, Subject)
            .join(Subject, Subject.id == Progress.subject_id)
            .where(new_only, Subject.level == chosen)
            .order_by(Subject.sort_order, Subject.id)
            .limit(remaining)
        )
        items = [
            LessonItem(subject=subject_detail(subject), srs_stage=progress.srs_stage)
            for progress, subject in rows
        ]

    return Lessons(
        items=items,
        level=chosen,
        total_in_level=in_level,
        total_available=total,
        daily_limit=config.daily_lesson_limit,
        batch_size=batch,
        levels=levels,
    )


def _midnight() -> datetime:
    """Start of the current UTC day.

    UTC rather than Europe/Berlin: the daily lesson limit is a pacing device,
    not an appointment, and a boundary that shifts twice a year is a worse
    surprise than one that falls at 01:00 or 02:00 local.
    """
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@router.post("/lessons/quiz", response_model=QuizOut)
async def quiz_answer(
    payload: QuizIn, session: AsyncSession = Depends(get_session)
) -> QuizOut:
    """Check one lesson-quiz answer. Moves nothing.

    The quiz sits between reading an item and it entering the SRS: the first
    retrieval, right after the exposure that makes it possible. Failing costs
    no stage, because the item has no stage yet — it simply comes round again
    until it is produced correctly, and only then does the batch start.

    Deliberately restricted to items that are still lessons. The response
    names the expected answer, which for an item already in the rotation would
    be a way to read exactly what ``GET /api/reviews`` withholds.
    """
    config = await load_runtime_config(session)

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

    if progress.state != ItemState.NEW.value:
        raise HTTPException(
            status_code=409, detail="This item is not a lesson; it has already been learned."
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

    return QuizOut(
        correct=check.correct,
        # Nothing is at stake in a lesson quiz, so the expected answer is not
        # withheld on a retry the way it is in a review.
        expected=check.expected,
        secondary=check.secondary,
        typo=check.typo,
        retry=check.retry,
        hint=check.hint,
    )


@router.post("/lessons/start", response_model=OverrideResult)
async def start_lessons(
    payload: StartLessonsIn, session: AsyncSession = Depends(get_session)
) -> OverrideResult:
    """Move the given items into the review rotation at Apprentice I.

    Called once the batch has passed its quiz. Returns only a count — the
    caller reloads the queue itself, because it knows which level it is
    working in and this endpoint would otherwise drag it back to the lowest.
    """
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
    started = list(rows.scalars())
    for progress in started:
        srs.start_lesson(progress, now, config.srs_intervals)
        progress.updated_at = now

    await session.commit()
    return OverrideResult(changed=len(started))
