"""Lessons and reviews -- the two queues the learner actually works in."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import srs
from ..answers import check_meaning, check_reading
from ..db import Progress, ReviewLog, Subject, get_session
from ..models import (
    AnswerIn,
    AnswerOut,
    LessonItem,
    LessonTile,
    Lessons,
    LevelSummary,
    OverrideResult,
    Queue,
    QueueItem,
    QuizIn,
    QuizOut,
    StartLessonsIn,
)
from ..runtime_config import RuntimeConfig, load_runtime_config
from ..serialize import subject_detail, subject_summary
from ..srs import ItemState, QuestionType
from ..synonyms import load_synonyms, load_synonyms_map

router = APIRouter()


async def _kanji_readings_for(
    session: AsyncSession, subject: Subject, question: QuestionType
) -> tuple[dict, ...]:
    """The component kanji's readings, when a vocabulary reading question can
    be confused with them -- empty otherwise.

    Deliberately narrow: only a vocabulary item whose ``characters`` are
    exactly one kanji qualifies. For a multi-kanji word a component's reading
    is a *fragment* of the word's reading, not "the wrong type of reading" --
    that confusion (WaniKani's own hint) only exists when the vocabulary and
    the kanji are the same character and could plausibly share a reading.
    A word with okurigana (出る next to the kanji 出) is left out as well: the
    exact-characters case is the one that cannot misfire.
    """
    if (
        question is not QuestionType.READING
        or subject.object_type != "vocabulary"
        or subject.characters is None
        or len(subject.characters) != 1
        or not subject.component_subject_ids
    ):
        return ()

    kanji = await session.scalar(
        select(Subject).where(
            Subject.id.in_(subject.component_subject_ids),
            Subject.object_type == "kanji",
            Subject.characters == subject.characters,
        )
    )
    return tuple(kanji.readings) if kanji is not None else ()


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


def _review_order(config: RuntimeConfig) -> list[ColumnElement]:
    """Build the queue's ORDER BY, most to least important.

    A fixed order breaks isolated SRS recall -- a review right after another
    one of the same kind lets the first cue the second, and after a WaniKani
    import most items share a due time and the leftover subject_id tiebreak
    would silently reproduce WaniKani's own radicals-kanji-vocabulary
    insertion order. So sorting is configurable and happens here, in SQL,
    before the LIMIT: the page is at most a few hundred rows out of possibly
    thousands due, and shuffling only the fetched page would just shuffle the
    oldest backlog.

    Leading key always: an item with a review in flight (closed tab,
    half-answered) is due *now*, regardless of order settings -- losing track
    of it would be worse than any ordering preference.
    """
    order: list[ColumnElement] = [
        case(
            (
                or_(Progress.pending_meaning.is_not(None), Progress.pending_reading.is_not(None)),
                0,
            ),
            else_=1,
        )
    ]

    if config.review_type_order == "grouped":
        order.append(
            case(
                (Subject.object_type == "radical", 0),
                (Subject.object_type == "kanji", 1),
                else_=2,  # vocabulary and kana_vocabulary together
            )
        )

    if config.review_item_order == "oldest_first":
        order.append(Progress.next_review_at)
    elif config.review_item_order == "lowest_stage_first":
        order.append(Progress.srs_stage)
    elif config.review_item_order == "lowest_level_first":
        order.append(Subject.level)
    # "random" adds no key of its own here -- the tiebreak below is func.random()
    # regardless, which is exactly what "random" order means.

    # Every order ends on a random tiebreak, because ties are the common case
    # after an import: hundreds of items share a due time, a stage or a level.
    # A subject_id tiebreak would work too, but subject ids were assigned in
    # WaniKani's own type order, which is exactly the grouping this function
    # exists to make optional -- so break ties randomly instead.
    order.append(func.random())
    return order


@router.get("/reviews", response_model=Queue)
async def review_queue(
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> Queue:
    """Items that are due, ordered per the review-order settings."""
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
        .order_by(*_review_order(config))
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

    # Loaded for a reading answer too, although only the meaning check reads
    # them: the detail in the response is what the "Show item" panel edits,
    # and the synonym editor saves a whole list -- an empty one shown after a
    # reading would overwrite every synonym the item has on the first add.
    synonyms = await load_synonyms(session, subject.id)
    # Alt+H gives up regardless of what was typed -- check against an empty
    # answer instead, which both checkers already treat as a plain wrong
    # answer (no retry, no typo, `expected` the primary meaning or reading).
    # That is exactly the one way to answer that is allowed to reveal
    # anything on purpose.
    answer_text = "" if payload.gave_up else payload.answer
    if payload.question is QuestionType.MEANING:
        check = check_meaning(
            answer_text,
            subject.meanings,
            subject.auxiliary_meanings,
            config.meaning_typo_tolerance_divisor,
            synonyms,
        )
    else:
        kanji_readings = await _kanji_readings_for(session, subject, payload.question)
        check = check_reading(
            answer_text, subject.readings, subject.object_type, kanji_readings
        )

    # --- the two second chances, both of which leave the item untouched ---
    #
    # Neither commits, so the `begin_review` above is rolled back with the
    # session and the item is exactly as it was. Neither reveals the expected
    # answer either: the question is still open, and a warning that showed the
    # answer would be a free reveal on demand. A given-up answer skips both --
    # it is not an attempt, let alone one worth a second chance.

    if check.retry:
        # A real reading of this character, of the type that was not asked.
        # WaniKani re-asks instead of counting it wrong, and charging for it
        # would punish knowing more than the question wanted.
        return _still_open(progress, subject, retry=True, hint=check.hint)

    if (
        not payload.gave_up
        and not check.correct
        and not payload.confirm
        and config.soft_answer_enabled
    ):
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
            given_answer="" if payload.gave_up else payload.answer[:200],
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
        subject=subject_detail(subject, synonyms),
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


async def _level_summaries(session: AsyncSession) -> list[LevelSummary]:
    """Every level that has any subject, open and total counts both.

    Every level, not just the ones with something left, so the picker can say
    "Level 5 — done" for a level fully learned rather than dropping it from
    the list -- which used to read as "nothing was ever imported there".
    """
    is_open = Progress.state == ItemState.NEW.value
    rows = await session.execute(
        select(
            Subject.level,
            func.count().label("total"),
            func.sum(case((is_open, 1), else_=0)).label("open"),
            func.sum(
                case((is_open & (Subject.object_type == "radical"), 1), else_=0)
            ).label("open_radicals"),
            func.sum(
                case((is_open & (Subject.object_type == "kanji"), 1), else_=0)
            ).label("open_kanji"),
            func.sum(
                case(
                    (
                        is_open
                        & Subject.object_type.in_(("vocabulary", "kana_vocabulary")),
                        1,
                    ),
                    else_=0,
                )
            ).label("open_vocabulary"),
        )
        .join(Progress, Progress.subject_id == Subject.id)
        .group_by(Subject.level)
        .order_by(Subject.level)
    )
    return [
        LevelSummary(
            level=level,
            open_count=int(open_ or 0),
            total_count=total,
            open_radicals=int(open_radicals or 0),
            open_kanji=int(open_kanji or 0),
            open_vocabulary=int(open_vocabulary or 0),
        )
        for level, total, open_, open_radicals, open_kanji, open_vocabulary in rows
    ]


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
    session: AsyncSession = Depends(get_session),
) -> Lessons:
    """The selection view: every level, and every item of the chosen one.

    Scoped to a level, defaulting to the lowest one that still has anything
    left, else the lowest level with any subject at all. There is still no
    *gate* — pass ``level`` and you may learn level 40 while level 3 is
    untouched, which is the freedom WaniKani does not give.

    Tiles cover the whole level, learned and known and suspended items
    included, so the learner sees what they already have alongside what is
    still open rather than a queue that only ever shows the new stuff. The
    learner picks which of the open ones to work on; ``GET /lessons/items``
    fetches the detail for exactly those, and ``POST /lessons/start`` commits
    them once they have passed the quiz.
    """
    config = await load_runtime_config(session)
    new_only = Progress.state == ItemState.NEW.value

    total = await session.scalar(select(func.count()).select_from(Progress).where(new_only)) or 0
    levels = await _level_summaries(session)

    chosen = level
    if chosen is None:
        chosen = await lowest_open_level(session)
    if chosen is None:
        chosen = levels[0].level if levels else None

    in_level = next((entry.open_count for entry in levels if entry.level == chosen), 0)

    tiles: list[LessonTile] = []
    if chosen is not None:
        rows = await session.execute(
            select(Progress, Subject)
            .join(Subject, Subject.id == Progress.subject_id)
            .where(Subject.level == chosen)
            .order_by(Subject.sort_order, Subject.id)
        )
        tiles = [
            LessonTile(subject=subject_summary(subject), state=progress.state, srs_stage=progress.srs_stage)
            for progress, subject in rows
        ]

    remaining_today = None
    if config.daily_lesson_limit:
        remaining_today = max(
            0, config.daily_lesson_limit - await _lessons_started_today(session)
        )

    return Lessons(
        level=chosen,
        total_in_level=in_level,
        total_available=total,
        daily_limit=config.daily_lesson_limit,
        batch_size=config.lesson_batch_size,
        levels=levels,
        tiles=tiles,
        remaining_today=remaining_today,
    )


@router.get("/lessons/items", response_model=list[LessonItem])
async def lesson_items(
    ids: list[int] = Query(default=[]),
    session: AsyncSession = Depends(get_session),
) -> list[LessonItem]:
    """Detail for the given ids that are still lessons -- others silently omitted.

    Declared ahead of ``/lessons/quiz`` and ``/lessons/start`` so a future
    path parameter under ``/lessons`` cannot shadow it. Same reasoning as the
    quiz's 409: a tile carries no answers, and handing back detail for an item
    already in the review rotation would be a way to read exactly what
    `GET /api/reviews` withholds -- so an id that is not currently a lesson is
    dropped rather than reported as an error, since the caller cannot always
    tell in advance (another tab may have started it in the meantime).
    """
    if len(ids) > 200:
        raise HTTPException(status_code=422, detail="At most 200 ids per request.")
    if not ids:
        return []

    rows = (
        await session.execute(
            select(Progress, Subject)
            .join(Subject, Subject.id == Progress.subject_id)
            .where(Subject.id.in_(ids), Progress.state == ItemState.NEW.value)
            .order_by(Subject.sort_order, Subject.id)
        )
    ).all()
    synonyms_map = await load_synonyms_map(session, (subject.id for _, subject in rows))
    return [
        LessonItem(
            subject=subject_detail(subject, synonyms_map.get(subject.id, [])),
            srs_stage=progress.srs_stage,
        )
        for progress, subject in rows
    ]


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

    # Alt+H gives up regardless of what was typed -- same empty-answer trick
    # as the review endpoint, which both checkers already treat as a plain
    # wrong answer with `expected` filled in.
    answer_text = "" if payload.gave_up else payload.answer
    if payload.question is QuestionType.MEANING:
        synonyms = await load_synonyms(session, subject.id)
        check = check_meaning(
            answer_text,
            subject.meanings,
            subject.auxiliary_meanings,
            config.meaning_typo_tolerance_divisor,
            synonyms,
        )
    else:
        kanji_readings = await _kanji_readings_for(session, subject, payload.question)
        check = check_reading(
            answer_text, subject.readings, subject.object_type, kanji_readings
        )

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
