"""Reconstructing "settled over time" -- earned (Guru+) and known counts per
local calendar day, for the stats page's Growth card (issue #61).

Kept as a small, dependency-free pure function (the same "no database, no
clock" discipline ``srs.py`` follows) so the historical reconstruction can be
unit-tested against plain rows, with ``app/api/stats.py`` doing nothing but
the three queries and the tz/day-window plumbing around it.

The whole thing rests on one simplification: a subject's *current* row
(``state``, ``srs_stage``, ``known_at``, ``updated_at``) is always exactly
right, but its *history* is only as good as ``review_log`` -- an override
(``mark_known``, ``reset_to_apprentice``, a hand edit) can silently
invalidate a replay built from review log crossings alone. Rather than chase
every such path, each subject's reconstructed state *at now* is compared
against its actual current row, and any difference is applied as one
corrective event at ``updated_at`` -- the series is always allowed to end
exactly where the dashboard and ``/stats/levels`` say it does, even if an
intermediate day or two is only approximate (see the "reset without a log
row" test).

Only currently non-suspended subjects ever contribute -- same rule
``/stats/levels`` applies to today's snapshot, extended here to the *whole*
history: a subject hidden today never contributes a day of "settled" either,
even a day from before it was suspended.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from enum import StrEnum

from .srs import STAGE_GURU_1, ItemState

#: WaniKani's own threshold, reused as this module's only magic number.
_GURU = STAGE_GURU_1


class _Metric(StrEnum):
    EARNED = "earned"
    KNOWN = "known"


@dataclass(frozen=True)
class SubjectRow:
    """One subject's *current* progress -- enough to know whether it should
    end the series earned, known, or neither, and to reconcile against."""

    subject_id: int
    state: str
    srs_stage: int
    known_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class ReviewLogRow:
    """One ``review_log`` row, trimmed to what the reconstruction reads."""

    subject_id: int
    answered_at: datetime
    srs_stage_before: int
    srs_stage_after: int


@dataclass(frozen=True)
class GrowthDay:
    date: date
    earned: int
    known: int


@dataclass(frozen=True)
class GrowthResult:
    """Full history, oldest day first, ending on "now".

    ``app/api/stats.py`` slices the tail of ``days`` for a bounded request;
    ``known_on_import`` is read from this un-sliced series, so it is right
    even when the requested window is narrower than the import date.
    """

    days: list[GrowthDay]
    imported_on: date | None
    known_on_import: int


@dataclass(frozen=True)
class _Event:
    at: datetime
    metric: _Metric
    delta: int


def _local_midnight(day: date, zone: tzinfo) -> datetime:
    """The UTC instant local midnight at the start of ``day`` falls on --
    same definition as ``api/stats.py``'s own helper, duplicated rather than
    imported so this module stays dependency-free."""
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)


def _subject_events(
    subject: SubjectRow,
    logs: list[ReviewLogRow],
    import_time: datetime | None,
) -> list[_Event]:
    """Every +1/-1 one subject contributes, in no particular order."""
    logs = sorted(logs, key=lambda row: row.answered_at)
    known_at = subject.known_at

    raw: list[tuple[datetime, int]] = []  # earned, entirely ignoring `known`

    if import_time is not None:
        # "Stage at T" before the first logged answer is the stage the
        # subject was imported with (its first review's own
        # `srs_stage_before`) -- or, if it has never been reviewed in the app
        # at all, its current stage. The latter is only correct for a
        # subject untouched since import (it covers an item imported as
        # *learning* at Guru+ because that import's own known-stage setting
        # was higher than 5); a subject later marked known or reset without
        # ever being reviewed loses its true import-time stage along with
        # it, which the reconciliation below cannot see either -- there is no
        # review_log row for it to disagree with. Accepted as the honest
        # edge of a review-log-only reconstruction.
        baseline_stage = logs[0].srs_stage_before if logs else subject.srs_stage
        if baseline_stage >= _GURU and (known_at is None or import_time < known_at):
            raw.append((import_time, 1))

    for log in logs:
        if known_at is not None and log.answered_at >= known_at:
            # Everything from here on is a review of an already-known item
            # (e.g. the stage-8 "one check-up left" review) -- it cannot
            # un-know or re-earn anything.
            break
        before_passed = log.srs_stage_before >= _GURU
        after_passed = log.srs_stage_after >= _GURU
        if not before_passed and after_passed:
            raw.append((log.answered_at, 1))
        elif before_passed and not after_passed:
            raw.append((log.answered_at, -1))

    events = [_Event(at, _Metric.EARNED, delta) for at, delta in raw]

    if known_at is not None:
        # Known takes precedence from `known_at` on: if the subject was
        # sitting in `earned` right before this instant, it leaves that
        # series exactly as it enters `known`.
        earned_before_known = sum(delta for at, delta in raw if at < known_at)
        if earned_before_known > 0:
            events.append(_Event(known_at, _Metric.EARNED, -1))
        events.append(_Event(known_at, _Metric.KNOWN, 1))

    # Reconcile against the row's actual current state -- `state`/`srs_stage`
    # rather than `known_at`, so a known item whose `known_at` is still NULL
    # (the pre-migration gap this issue also backfills) still ends up
    # `known` rather than silently dropped. This is also what makes a reset
    # without a log row (`reset_to_apprentice` clears `known_at` and the
    # stage with no review_log entry) come out right: whatever the replay
    # above reconstructed, the subject's current row always wins.
    actual_earned = subject.state == ItemState.LEARNING.value and subject.srs_stage >= _GURU
    actual_known = subject.state == ItemState.KNOWN.value
    reconstructed_earned = sum(e.delta for e in events if e.metric is _Metric.EARNED)
    reconstructed_known = sum(e.delta for e in events if e.metric is _Metric.KNOWN)
    earned_fix = (1 if actual_earned else 0) - reconstructed_earned
    known_fix = (1 if actual_known else 0) - reconstructed_known
    if earned_fix:
        events.append(_Event(subject.updated_at, _Metric.EARNED, earned_fix))
    if known_fix:
        events.append(_Event(subject.updated_at, _Metric.KNOWN, known_fix))

    return events


def _sweep(
    events: list[_Event],
    start_day: date,
    end_day: date,
    zone: tzinfo,
    now: datetime,
) -> list[GrowthDay]:
    """Zero-filled cumulative totals, one entry per local day, oldest first.

    Each day's value is the state at the *end* of that local day -- except
    the last one, which stops at ``now`` rather than local midnight, so
    today is never counted as if it had already finished.
    """
    ordered = sorted(events, key=lambda event: event.at)
    cumulative = {_Metric.EARNED: 0, _Metric.KNOWN: 0}
    result: list[GrowthDay] = []
    index = 0
    total = len(ordered)
    day_count = (end_day - start_day).days + 1
    for offset in range(day_count):
        day = start_day + timedelta(days=offset)
        day_end = now if day == end_day else _local_midnight(day + timedelta(days=1), zone)
        while index < total and ordered[index].at < day_end:
            cumulative[ordered[index].metric] += ordered[index].delta
            index += 1
        result.append(
            GrowthDay(date=day, earned=cumulative[_Metric.EARNED], known=cumulative[_Metric.KNOWN])
        )
    return result


def compute_growth(
    subjects: list[SubjectRow],
    review_logs: list[ReviewLogRow],
    import_time: datetime | None,
    *,
    now: datetime,
    zone: tzinfo,
) -> GrowthResult:
    """Build the whole history, oldest day first, ending today.

    ``subjects`` may include suspended ones -- they are dropped here rather
    than trusted entirely to the caller's query, so "suspended never
    contributes, even historically" is a rule this function itself
    guarantees and a unit test can pin down directly.
    """
    today = now.astimezone(zone).date()
    imported_on = import_time.astimezone(zone).date() if import_time is not None else None

    active = [s for s in subjects if s.state != ItemState.SUSPENDED.value]
    by_subject: dict[int, list[ReviewLogRow]] = defaultdict(list)
    for log in review_logs:
        by_subject[log.subject_id].append(log)

    events: list[_Event] = []
    for subject in active:
        events.extend(_subject_events(subject, by_subject.get(subject.subject_id, []), import_time))

    if not events:
        return GrowthResult(
            days=[GrowthDay(date=today, earned=0, known=0)],
            imported_on=imported_on,
            known_on_import=0,
        )

    start_day = min(event.at.astimezone(zone).date() for event in events)
    days = _sweep(events, start_day, today, zone, now)

    known_on_import = 0
    if imported_on is not None:
        entry = next((d for d in days if d.date == imported_on), None)
        known_on_import = entry.known if entry is not None else 0

    return GrowthResult(days=days, imported_on=imported_on, known_on_import=known_on_import)
