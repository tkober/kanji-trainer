"""The dashboard numbers, the level breakdown and the activity calendar.

Declared-known items are counted separately from earned ones throughout. That
distinction is the whole reason ``state`` and ``srs_stage`` are both stored,
and a dashboard that blurred it would make the trainer flattering rather than
useful.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import ImportRun, Progress, ReviewLog, Subject, get_session
from ..growth import GrowthResult, ReviewLogRow, SubjectRow, compute_growth
from ..models import (
    Activity,
    ActivityDay,
    GrowthDayOut,
    GrowthOut,
    LevelStats,
    LevelTypeCounts,
    StageCounts,
    Stats,
)
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
from .study import lowest_open_level

router = APIRouter()

#: The importer's own type order (`wanikani._TYPE_ORDER`), reused so the level
#: breakdown lists types the same way the rest of the app does. Anything not
#: in it (there shouldn't be anything) sorts after, alphabetically.
_OBJECT_TYPE_ORDER: tuple[str, ...] = ("radical", "kanji", "vocabulary", "kana_vocabulary")

#: Which `StageCounts` field a `state == "learning"` item's `srs_stage` maps
#: to. Stage 0 is deliberately absent -- see `_bucket_field`.
_LEARNING_STAGE_FIELDS: dict[int, str] = {
    1: "apprentice_1",
    2: "apprentice_2",
    3: "apprentice_3",
    4: "apprentice_4",
    STAGE_GURU_1: "guru_1",
    6: "guru_2",
    STAGE_MASTER: "master",
    STAGE_ENLIGHTENED: "enlightened",
    STAGE_BURNED: "burned",
}


def _utcnow() -> datetime:
    """The current instant -- one seam for the tests to freeze the clock at.

    "Today" depends on the time of day in two zones at once, so a test that
    reads the real clock passes or fails depending on when it runs (issue
    #68). Monkeypatched in tests, same pattern as `illustrations._client_factory`.
    """
    return datetime.now(timezone.utc)


def _resolve_tz(tz: str | None) -> tzinfo:
    """An IANA zone, or UTC when none is given.

    UTC is returned as a real zone rather than handled as a separate branch
    everywhere else: local midnight in UTC *is* UTC midnight, so every caller
    below can use the same helper either way.
    """
    if tz is None:
        return timezone.utc
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown time zone: {tz!r}.") from exc


def _local_midnight(day: date, tz: tzinfo) -> datetime:
    """The UTC instant local midnight at the start of ``day`` falls on.

    Shared between ``/stats``' "today" window and ``/stats/activity``'s
    per-day bucketing, so both agree on where a calendar day begins.
    """
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)


def _object_type_sort_key(object_type: str) -> tuple[int, str]:
    try:
        return (_OBJECT_TYPE_ORDER.index(object_type), object_type)
    except ValueError:
        return (len(_OBJECT_TYPE_ORDER), object_type)


def _bucket_field(state: str, stage: int) -> str:
    """Which :class:`StageCounts` field one ``(state, srs_stage)`` pair feeds.

    ``known`` and ``suspended`` ignore the stage entirely: a declared-known or
    hidden item is not "earned" at whatever stage it happens to sit at, same
    reasoning as ``burned_count`` on ``/stats`` only counting ``state ==
    learning``. A learning item at stage 0 should not occur -- the importer
    and ``srs.py`` only ever settle a learning item at stage >= 1 -- but if it
    ever does, it folds into ``new`` rather than raising or being dropped.
    """
    if state == ItemState.NEW.value:
        return "new"
    if state == ItemState.KNOWN.value:
        return "known"
    if state == ItemState.SUSPENDED.value:
        return "suspended"
    return _LEARNING_STAGE_FIELDS.get(stage, "new")


@router.get("", response_model=Stats)
async def read_stats(
    tz: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Stats:
    """IANA ``tz`` moves "today" (``reviews_today``/``accuracy_today``) to
    local midnight; without it "today" stays UTC midnight, as before."""
    zone = _resolve_tz(tz)
    config = await load_runtime_config(session)
    now = _utcnow()
    cutoff = due_cutoff(now, config.review_grace_minutes)
    today_start = _local_midnight(now.astimezone(zone).date(), zone)

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
            .where(ReviewLog.answered_at >= today_start)
        )
        or 0
    )
    correct_today = (
        await session.scalar(
            select(func.count())
            .select_from(ReviewLog)
            .where(
                ReviewLog.answered_at >= today_start,
                ReviewLog.correct.is_(True),
            )
        )
        or 0
    )

    # The badge has to show what the lesson screen will actually offer. The
    # collection-wide `new_count` below is five figures after importing a reset
    # account, and a badge saying 9.321 is worse than no badge at all.
    current_level = await lowest_open_level(session)
    lessons_available = 0
    if current_level is not None:
        lessons_available = (
            await session.scalar(
                select(func.count())
                .select_from(Progress)
                .join(Subject, Subject.id == Progress.subject_id)
                .where(Progress.state == ItemState.NEW.value, Subject.level == current_level)
            )
            or 0
        )

    # The earliest item not due yet -- "next 3 at 14:00" on the dashboard.
    # Strictly after `cutoff`: an item inside the grace window is due *now*,
    # not "next", same boundary `due_now` below uses.
    next_review_at = await session.scalar(
        select(Progress.next_review_at)
        .where(*scheduled, Progress.next_review_at > cutoff)
        .order_by(Progress.next_review_at)
        .limit(1)
    )
    next_review_count = 0
    if next_review_at is not None:
        hour_start = next_review_at.replace(minute=0, second=0, microsecond=0)
        hour_end = hour_start + timedelta(hours=1)
        next_review_count = await count(
            *scheduled,
            Progress.next_review_at > cutoff,
            Progress.next_review_at >= hour_start,
            Progress.next_review_at < hour_end,
        )

    async def guru_crossings(start: datetime, end: datetime) -> int:
        """Distinct subjects with a Guru crossing in ``[start, end)``.

        Rolling windows measured from ``now``, deliberately independent of
        `tz` -- "the last 7 days" needs no calendar, and a crossing counts
        once per subject even if it fell out and passed again in the window.
        """
        return (
            await session.scalar(
                select(func.count(func.distinct(ReviewLog.subject_id))).where(
                    ReviewLog.srs_stage_before < STAGE_GURU_1,
                    ReviewLog.srs_stage_after >= STAGE_GURU_1,
                    ReviewLog.answered_at >= start,
                    ReviewLog.answered_at < end,
                )
            )
            or 0
        )

    guru_last_7_days = await guru_crossings(now - timedelta(days=7), now)
    guru_previous_7_days = await guru_crossings(now - timedelta(days=14), now - timedelta(days=7))

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
        lessons_available=lessons_available,
        current_level=current_level,
        due_now=await count(*scheduled, Progress.next_review_at <= cutoff),
        due_next_hour=await count(*scheduled, Progress.next_review_at <= now + timedelta(hours=1)),
        due_today=await count(*scheduled, Progress.next_review_at <= now + timedelta(days=1)),
        reviews_today=reviews_today,
        accuracy_today=(correct_today / reviews_today) if reviews_today else None,
        next_review_at=next_review_at,
        next_review_count=next_review_count,
        guru_last_7_days=guru_last_7_days,
        guru_previous_7_days=guru_previous_7_days,
    )


@router.get("/levels", response_model=LevelStats)
async def read_level_stats(session: AsyncSession = Depends(get_session)) -> LevelStats:
    """Per level x object type, how many items sit in each SRS bucket.

    One ``GROUP BY level, object_type, state, srs_stage``, folded in Python --
    the frontend builds its own reference frame (a level range, a type
    filter, the known toggle) on top of this rather than the backend baking
    one in.
    """
    rows = await session.execute(
        select(Subject.level, Subject.object_type, Progress.state, Progress.srs_stage, func.count())
        .select_from(Progress)
        .join(Subject, Subject.id == Progress.subject_id)
        .group_by(Subject.level, Subject.object_type, Progress.state, Progress.srs_stage)
    )

    buckets: dict[tuple[int, str], StageCounts] = {}
    for level, object_type, state, stage, n in rows:
        counts = buckets.setdefault((level, object_type), StageCounts())
        field = _bucket_field(state, stage)
        setattr(counts, field, getattr(counts, field) + n)

    levels = [
        LevelTypeCounts(level=level, object_type=object_type, counts=counts)
        for (level, object_type), counts in buckets.items()
    ]
    levels.sort(key=lambda item: (item.level, _object_type_sort_key(item.object_type)))

    return LevelStats(current_level=await lowest_open_level(session), levels=levels)


@router.get("/activity", response_model=Activity)
async def read_activity(
    days: int = Query(default=182, ge=1, le=730),
    tz: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Activity:
    """One entry per local calendar day over the last ``days`` days, zero-filled.

    Bucketed in Python rather than in SQL: Postgres wants ``date_trunc`` and
    SQLite wants ``strftime``, and splitting that for a few thousand
    timestamps is not worth a second code path (see ``forecast.py``, which
    made the same call for hourly buckets).
    """
    zone = _resolve_tz(tz)
    now = _utcnow()
    today = now.astimezone(zone).date()
    start_day = today - timedelta(days=days - 1)
    window_start = _local_midnight(start_day, zone)
    window_end = _local_midnight(today + timedelta(days=1), zone)

    buckets: dict[date, dict[str, int]] = {
        start_day + timedelta(days=offset): {"answers": 0, "correct": 0, "lessons_started": 0}
        for offset in range(days)
    }

    answer_rows = await session.execute(
        select(ReviewLog.answered_at, ReviewLog.correct).where(
            ReviewLog.answered_at >= window_start, ReviewLog.answered_at < window_end
        )
    )
    for answered_at, correct in answer_rows:
        bucket = buckets.get(answered_at.astimezone(zone).date())
        if bucket is None:
            continue
        bucket["answers"] += 1
        if correct:
            bucket["correct"] += 1

    lesson_rows = await session.scalars(
        select(Progress.started_at).where(
            Progress.started_at.is_not(None),
            Progress.started_at >= window_start,
            Progress.started_at < window_end,
        )
    )
    for started_at in lesson_rows:
        bucket = buckets.get(started_at.astimezone(zone).date())
        if bucket is not None:
            bucket["lessons_started"] += 1

    guru_rows = await session.execute(
        select(ReviewLog.answered_at, ReviewLog.subject_id).where(
            ReviewLog.srs_stage_before < STAGE_GURU_1,
            ReviewLog.srs_stage_after >= STAGE_GURU_1,
            ReviewLog.answered_at >= window_start,
            ReviewLog.answered_at < window_end,
        )
    )
    guru_by_day: dict[date, set[int]] = {}
    for answered_at, subject_id in guru_rows:
        day = answered_at.astimezone(zone).date()
        if day in buckets:
            guru_by_day.setdefault(day, set()).add(subject_id)

    activity_days = [
        ActivityDay(
            date=day,
            answers=buckets[day]["answers"],
            correct=buckets[day]["correct"],
            lessons_started=buckets[day]["lessons_started"],
            reached_guru=len(guru_by_day.get(day, ())),
        )
        for day in sorted(buckets)
    ]

    # Consecutive days with at least one answer, ending today -- or ending
    # yesterday if today has not had one yet, so the streak does not reset
    # the moment the clock passes local midnight with no review done yet.
    streak = 0
    cursor = today if buckets[today]["answers"] > 0 else today - timedelta(days=1)
    while cursor >= start_day and buckets.get(cursor, {}).get("answers", 0) > 0:
        streak += 1
        cursor -= timedelta(days=1)

    return Activity(days=activity_days, streak=streak)


@router.get("/growth", response_model=GrowthOut)
async def read_growth(
    days: int | None = Query(default=None, ge=1, le=3650),
    tz: str | None = Query(default=None),
    max_level: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
) -> GrowthOut:
    """"Settled over time" -- earned (Guru+) and known, reconstructed per
    local day from `review_log` and `known_at` (the reconstruction itself
    lives in `app.growth`, kept dependency-free and unit-tested on its own).

    Only non-suspended subjects count, exactly like `/stats/levels` -- a
    subject hidden today never contributes a day of history either, even one
    from before it was suspended (`compute_growth` enforces this itself, not
    just this query). `max_level` scopes to the stats page's own frame
    (level <= max_level); the Growth card is the one place on the page a
    frame switch causes a request at all -- every other card's frame
    narrowing happens client-side against the already-fetched `/stats/levels`
    rows, which this endpoint cannot reuse since it needs the full
    per-subject history, not just today's bucket counts.
    """
    zone = _resolve_tz(tz)
    now = _utcnow()

    subject_query = select(
        Progress.subject_id,
        Progress.state,
        Progress.srs_stage,
        Progress.known_at,
        Progress.updated_at,
    ).select_from(Progress)
    if max_level is not None:
        subject_query = subject_query.join(Subject, Subject.id == Progress.subject_id).where(
            Subject.level <= max_level
        )
    subject_rows = (await session.execute(subject_query)).all()
    subjects = [
        SubjectRow(subject_id=row[0], state=row[1], srs_stage=row[2], known_at=row[3], updated_at=row[4])
        for row in subject_rows
    ]
    # `max_level` only narrows which subjects count, so the matching
    # review_log rows are filtered in Python against that same set -- one
    # query each over progress/subjects and review_log, no per-day queries.
    included_ids = {s.subject_id for s in subjects}

    log_rows = await session.execute(
        select(
            ReviewLog.subject_id,
            ReviewLog.answered_at,
            ReviewLog.srs_stage_before,
            ReviewLog.srs_stage_after,
        )
    )
    review_logs = [
        ReviewLogRow(subject_id=row[0], answered_at=row[1], srs_stage_before=row[2], srs_stage_after=row[3])
        for row in log_rows
        if row[0] in included_ids
    ]

    import_time = await session.scalar(
        select(ImportRun.finished_at)
        .where(ImportRun.status == "succeeded")
        .order_by(ImportRun.finished_at)
        .limit(1)
    )

    result: GrowthResult = compute_growth(subjects, review_logs, import_time, now=now, zone=zone)

    history = result.days
    if days is not None and len(history) > days:
        history = history[-days:]

    return GrowthOut(
        days=[GrowthDayOut(date=d.date, earned=d.earned, known=d.known) for d in history],
        imported_on=result.imported_on,
        known_on_import=result.known_on_import,
    )
