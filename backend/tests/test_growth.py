"""`app.growth.compute_growth` -- the pure reconstruction behind the stats
page's Growth card (issue #61). No database, no client: plain rows in,
`GrowthDay`s out, same discipline as `test_srs.py`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.growth import GrowthDay, ReviewLogRow, SubjectRow, compute_growth
from app.srs import ItemState

UTC = timezone.utc
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def subject(
    subject_id: int,
    *,
    state: str,
    stage: int,
    known_at: datetime | None = None,
    updated_at: datetime = NOW,
) -> SubjectRow:
    return SubjectRow(
        subject_id=subject_id, state=state, srs_stage=stage, known_at=known_at, updated_at=updated_at
    )


def log(subject_id: int, at: datetime, before: int, after: int) -> ReviewLogRow:
    return ReviewLogRow(subject_id=subject_id, answered_at=at, srs_stage_before=before, srs_stage_after=after)


def day_of(days: list[GrowthDay], d: date) -> GrowthDay:
    return next(entry for entry in days if entry.date == d)


# --- Guru crossings and lapses ------------------------------------------------


def test_a_guru_crossing_adds_a_day_of_earned():
    crossing = NOW - timedelta(days=10)
    subjects = [subject(1, state=ItemState.LEARNING.value, stage=5, updated_at=crossing)]
    logs = [log(1, crossing, before=4, after=5)]

    result = compute_growth(subjects, logs, None, now=NOW, zone=UTC)

    # The crossing is the earliest event, so it is the series' first day --
    # there is no earlier zero-filled day to check against.
    assert result.days[0].date == crossing.date()
    assert day_of(result.days, crossing.date()).earned == 1
    assert day_of(result.days, NOW.date()).earned == 1


def test_a_lapse_back_below_guru_removes_the_earned_day():
    crossed = NOW - timedelta(days=20)
    lapsed = NOW - timedelta(days=10)
    subjects = [subject(1, state=ItemState.LEARNING.value, stage=3, updated_at=lapsed)]
    logs = [
        log(1, crossed, before=4, after=5),
        log(1, lapsed, before=5, after=3),
    ]

    result = compute_growth(subjects, logs, None, now=NOW, zone=UTC)

    mid_day = day_of(result.days, crossed.date() + timedelta(days=1))
    after_lapse = day_of(result.days, lapsed.date())
    assert mid_day.earned == 1
    assert after_lapse.earned == 0
    assert day_of(result.days, NOW.date()).earned == 0


# --- known -------------------------------------------------------------------


def test_known_from_import_counts_from_the_import_date():
    import_time = NOW - timedelta(days=30)
    subjects = [subject(1, state=ItemState.KNOWN.value, stage=9, known_at=import_time)]

    result = compute_growth(subjects, [], import_time, now=NOW, zone=UTC)

    assert day_of(result.days, import_time.date()).known == 1
    assert day_of(result.days, NOW.date()).known == 1
    assert result.imported_on == import_time.date()
    assert result.known_on_import == 1


def test_known_marked_later_moves_the_subject_out_of_earned():
    crossed = NOW - timedelta(days=60)
    declared_known = NOW - timedelta(days=5)
    subjects = [
        subject(1, state=ItemState.KNOWN.value, stage=9, known_at=declared_known, updated_at=declared_known)
    ]
    logs = [log(1, crossed, before=4, after=5)]

    result = compute_growth(subjects, logs, None, now=NOW, zone=UTC)

    before_known = day_of(result.days, declared_known.date() - timedelta(days=1))
    after_known = day_of(result.days, declared_known.date())
    assert before_known.earned == 1
    assert before_known.known == 0
    assert after_known.earned == 0
    assert after_known.known == 1


# --- reset without a log row --------------------------------------------------


def test_reset_to_apprentice_reconciles_at_updated_at():
    """`reset_to_apprentice` drops the stage and clears `known_at` with no
    `review_log` entry -- the subject's own crossing is still on record, but
    its current row says Apprentice I again."""
    crossed = NOW - timedelta(days=40)
    reset_at = NOW - timedelta(days=5)
    subjects = [subject(1, state=ItemState.LEARNING.value, stage=1, known_at=None, updated_at=reset_at)]
    logs = [log(1, crossed, before=4, after=5)]

    result = compute_growth(subjects, logs, None, now=NOW, zone=UTC)

    before_reset = day_of(result.days, reset_at.date() - timedelta(days=1))
    after_reset = day_of(result.days, reset_at.date())
    assert before_reset.earned == 1
    assert after_reset.earned == 0
    assert day_of(result.days, NOW.date()).earned == 0


# --- suspended -----------------------------------------------------------------


def test_suspended_subjects_are_excluded_even_from_their_own_past():
    crossed = NOW - timedelta(days=10)
    subjects = [subject(1, state=ItemState.SUSPENDED.value, stage=5, updated_at=crossed)]
    logs = [log(1, crossed, before=4, after=5)]

    result = compute_growth(subjects, logs, None, now=NOW, zone=UTC)

    assert all(d.earned == 0 and d.known == 0 for d in result.days)


# --- imported at Guru+ with no review history ---------------------------------


def test_imported_at_guru_plus_with_no_reviews_counts_as_earned_from_import():
    """A higher-than-default `known_srs_stage` setting can import an item as
    *learning* even though its WaniKani stage was already Guru+."""
    import_time = NOW - timedelta(days=15)
    subjects = [subject(1, state=ItemState.LEARNING.value, stage=6, known_at=None)]

    result = compute_growth(subjects, [], import_time, now=NOW, zone=UTC)

    assert result.days[0].date == import_time.date()
    assert day_of(result.days, import_time.date()).earned == 1
    assert day_of(result.days, NOW.date()).earned == 1


# --- tz day bucketing ----------------------------------------------------------


def test_tz_moves_a_late_answer_to_the_next_local_day():
    """23:30 UTC is already the next local day in Berlin (always ahead of
    UTC, DST or not) -- same rule `/stats/activity` bucketing follows."""
    day = date(2026, 9, 1)
    crossing = datetime(day.year, day.month, day.day, 23, 30, tzinfo=UTC)
    subjects = [subject(1, state=ItemState.LEARNING.value, stage=5)]
    logs = [log(1, crossing, before=4, after=5)]
    berlin = ZoneInfo("Europe/Berlin")

    utc_result = compute_growth(subjects, logs, None, now=NOW, zone=UTC)
    berlin_result = compute_growth(subjects, logs, None, now=NOW, zone=berlin)

    # Same instant, different local day: UTC buckets it on the 1st, Berlin
    # (always ahead of UTC) on the 2nd -- each result's series starts on its
    # own such day, one apart.
    assert utc_result.days[0].date == day
    assert berlin_result.days[0].date == day + timedelta(days=1)
    assert day_of(utc_result.days, day).earned == 1
    assert day_of(berlin_result.days, day + timedelta(days=1)).earned == 1
