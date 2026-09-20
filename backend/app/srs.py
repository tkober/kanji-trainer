"""The spaced-repetition engine, and the three ways out of it.

The stage ladder and the penalty arithmetic are WaniKani's, deliberately: the
learner this is built for has years of muscle memory for what "Guru" means and
how far a wrong answer throws an item back. What is *not* WaniKani's is that
every stage is reachable by declaring it. :func:`mark_known` is the whole
reason this project exists -- WaniKani has no way to say "ich kann das schon",
so restarting after a break means re-earning thousands of items at four hours
a step.

Nothing here touches the database or the clock on its own: ``now`` is always
passed in and the progress row is mutated in place. That keeps the rules
testable without a fixture, which matters more here than anywhere else in the
codebase.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

# --- the ladder ------------------------------------------------------------

STAGE_UNLEARNED = 0
STAGE_APPRENTICE_1 = 1
STAGE_GURU_1 = 5
STAGE_MASTER = 7
STAGE_ENLIGHTENED = 8
STAGE_BURNED = 9

# WaniKani's names, kept in English while the rest of the UI copy is German.
# They are the vocabulary the learner already thinks in; translating "Guru" to
# something German would make their own history unreadable to them.
STAGE_NAMES: tuple[str, ...] = (
    "Ungelernt",
    "Apprentice I",
    "Apprentice II",
    "Apprentice III",
    "Apprentice IV",
    "Guru I",
    "Guru II",
    "Master",
    "Enlightened",
    "Burned",
)


class ItemState(StrEnum):
    """What the learner intends for an item, as opposed to where it stands.

    Both this and ``srs_stage`` are stored because stage 9 is reachable two
    ways -- by answering correctly eight times, or by saying "das kann ich" --
    and statistics that cannot tell those apart are worthless.
    """

    NEW = "new"
    LEARNING = "learning"
    KNOWN = "known"
    SUSPENDED = "suspended"


class QuestionType(StrEnum):
    MEANING = "meaning"
    READING = "reading"


#: Which questions an object type is asked. Radicals have only a name, and
#: kana vocabulary is already spelled in kana -- asking for its reading would
#: be asking the learner to copy the prompt.
REQUIRED_QUESTIONS: dict[str, tuple[QuestionType, ...]] = {
    "radical": (QuestionType.MEANING,),
    "kanji": (QuestionType.MEANING, QuestionType.READING),
    "vocabulary": (QuestionType.MEANING, QuestionType.READING),
    "kana_vocabulary": (QuestionType.MEANING,),
}

DEFAULT_INTERVAL_HOURS: tuple[int, ...] = (0, 4, 8, 24, 48, 168, 336, 720, 2880, 0)


def required_questions(object_type: str) -> tuple[QuestionType, ...]:
    """The questions one review of this object type consists of."""
    return REQUIRED_QUESTIONS.get(object_type, (QuestionType.MEANING, QuestionType.READING))


def stage_name(stage: int) -> str:
    """Human label for a stage, clamped so an odd value still renders."""
    return STAGE_NAMES[max(0, min(STAGE_BURNED, stage))]


def is_apprentice(stage: int) -> bool:
    return STAGE_APPRENTICE_1 <= stage < STAGE_GURU_1


def is_passed(stage: int) -> bool:
    """Guru I or above -- WaniKani's threshold for "this one has landed"."""
    return stage >= STAGE_GURU_1


# --- scheduling ------------------------------------------------------------


def schedule(
    stage: int,
    now: datetime,
    intervals: tuple[int, ...] = DEFAULT_INTERVAL_HOURS,
) -> datetime | None:
    """When an item that has just entered ``stage`` comes back.

    None means "never again on its own": stage 0 has not been learned yet and
    stage 9 is burned. Both are out of the review rotation, for opposite
    reasons.
    """
    if stage <= STAGE_UNLEARNED or stage >= STAGE_BURNED:
        return None
    hours = intervals[stage] if stage < len(intervals) else 0
    if hours <= 0:
        return None
    return now + timedelta(hours=hours)


def penalty_factor(stage: int) -> int:
    """How hard a wrong answer hits, by where the item stands.

    Guru and above fall twice as far. The asymmetry is the point: an item the
    learner had genuinely secured and then lost needs more re-exposure than
    one that never got there.
    """
    return 2 if is_passed(stage) else 1


def stage_after_incorrect(stage: int, incorrect_count: int) -> int:
    """Where a failed review leaves an item.

    Every *two* wrong answers cost one step (times the penalty factor), so
    missing both the meaning and the reading of a kanji is one demotion rather
    than two. Never below Apprentice I -- an item in review has been learned,
    and stage 0 would put it back in the lesson queue.
    """
    if incorrect_count <= 0:
        return stage
    steps = math.ceil(incorrect_count / 2) * penalty_factor(stage)
    return max(STAGE_APPRENTICE_1, stage - steps)


def stage_after_correct(stage: int) -> int:
    """One step up, stopping at burned."""
    return min(STAGE_BURNED, stage + 1)


# --- applying an answer ----------------------------------------------------


@dataclass(frozen=True)
class AnswerOutcome:
    """What one answered question did to the item."""

    correct: bool
    stage_before: int
    stage_after: int
    #: True when this answer completed the review -- i.e. every question the
    #: item owed has now been answered correctly and the stage has moved.
    completed: bool
    #: Questions still outstanding for this item, in asking order.
    remaining: tuple[QuestionType, ...]
    next_review_at: datetime | None


class _ProgressLike:
    """Structural documentation of what :func:`apply_answer` mutates.

    Not enforced -- :class:`app.db.Progress` satisfies it, and the tests pass
    a plain stand-in so the rules can be exercised without a database.
    """

    state: str
    srs_stage: int
    next_review_at: datetime | None
    pending_meaning: bool | None
    pending_reading: bool | None
    session_incorrect: int
    correct_count: int
    incorrect_count: int
    lapses: int
    started_at: datetime | None
    passed_at: datetime | None
    burned_at: datetime | None
    known_at: datetime | None


def begin_review(progress: _ProgressLike, object_type: str) -> None:
    """Set the outstanding questions, if this review has not started yet.

    Called on the first answer rather than when the queue is handed out: a GET
    that writes is surprising, and initialising here means an item the learner
    looked at and navigated away from is untouched.
    """
    if progress.pending_meaning is not None or progress.pending_reading is not None:
        return
    required = required_questions(object_type)
    progress.pending_meaning = QuestionType.MEANING in required
    progress.pending_reading = QuestionType.READING in required
    progress.session_incorrect = 0


def outstanding(progress: _ProgressLike) -> tuple[QuestionType, ...]:
    """Questions the item still owes, in asking order."""
    pending: list[QuestionType] = []
    if progress.pending_meaning:
        pending.append(QuestionType.MEANING)
    if progress.pending_reading:
        pending.append(QuestionType.READING)
    return tuple(pending)


def apply_answer(
    progress: _ProgressLike,
    object_type: str,
    question: QuestionType,
    correct: bool,
    now: datetime,
    intervals: tuple[int, ...] = DEFAULT_INTERVAL_HOURS,
) -> AnswerOutcome:
    """Record one answer, advancing the item when it has answered everything.

    A wrong answer does not demote immediately: it is counted and the question
    stays outstanding, so the learner is asked again in the same session and
    the item only moves once it is fully answered. That is WaniKani's
    behaviour and it is what makes the "every two mistakes" rule mean
    anything.
    """
    begin_review(progress, object_type)
    stage_before = progress.srs_stage

    if correct:
        progress.correct_count += 1
        if question is QuestionType.MEANING:
            progress.pending_meaning = False
        else:
            progress.pending_reading = False
    else:
        progress.incorrect_count += 1
        progress.session_incorrect += 1

    remaining = outstanding(progress)
    if remaining:
        return AnswerOutcome(
            correct=correct,
            stage_before=stage_before,
            stage_after=stage_before,
            completed=False,
            remaining=remaining,
            next_review_at=progress.next_review_at,
        )

    # Everything answered: the item moves exactly once, by the accumulated
    # mistakes.
    if progress.session_incorrect:
        stage_after = stage_after_incorrect(stage_before, progress.session_incorrect)
        if is_passed(stage_before) and not is_passed(stage_after):
            progress.lapses += 1
    else:
        stage_after = stage_after_correct(stage_before)

    _settle(progress, stage_after, now, intervals)

    return AnswerOutcome(
        correct=correct,
        stage_before=stage_before,
        stage_after=stage_after,
        completed=True,
        remaining=(),
        next_review_at=progress.next_review_at,
    )


def _settle(
    progress: _ProgressLike,
    stage: int,
    now: datetime,
    intervals: tuple[int, ...],
) -> None:
    """Move the item to ``stage``, reschedule it and clear the review."""
    progress.srs_stage = stage
    progress.state = ItemState.LEARNING
    progress.next_review_at = schedule(stage, now, intervals)
    progress.pending_meaning = None
    progress.pending_reading = None
    progress.session_incorrect = 0

    if progress.started_at is None and stage > STAGE_UNLEARNED:
        progress.started_at = now
    if progress.passed_at is None and is_passed(stage):
        progress.passed_at = now
    if stage >= STAGE_BURNED:
        progress.burned_at = progress.burned_at or now
    else:
        # Demoting a burned item has to clear the timestamp, or the dashboard
        # keeps counting it as burned while it sits in the review queue.
        progress.burned_at = None


# --- the overrides ---------------------------------------------------------


def start_lesson(
    progress: _ProgressLike,
    now: datetime,
    intervals: tuple[int, ...] = DEFAULT_INTERVAL_HOURS,
) -> None:
    """Take an item out of the lesson queue and into the rotation."""
    _settle(progress, STAGE_APPRENTICE_1, now, intervals)


def mark_known(
    progress: _ProgressLike,
    now: datetime,
    known_stage: int = STAGE_BURNED,
    intervals: tuple[int, ...] = DEFAULT_INTERVAL_HOURS,
) -> None:
    """"Das kann ich" -- park the item at ``known_stage`` without review.

    At stage 9 the item is retired outright. At 8 it keeps one four-month
    check-up, which is the honest setting for someone who is *fairly* sure:
    the whole failure mode this project exists to avoid is re-grinding known
    material, but the second failure mode is quietly declaring away an item
    that was never actually secure.

    ``state`` becomes KNOWN rather than LEARNING so the dashboard can keep
    declared items and earned ones apart for good.
    """
    stage = max(STAGE_APPRENTICE_1, min(STAGE_BURNED, known_stage))
    _settle(progress, stage, now, intervals)
    progress.state = ItemState.KNOWN
    progress.known_at = now


def reset_to_apprentice(
    progress: _ProgressLike,
    now: datetime,
    intervals: tuple[int, ...] = DEFAULT_INTERVAL_HOURS,
) -> None:
    """"Nochmal von vorn" -- back to Apprentice I, due in four hours.

    The counterpart to :func:`mark_known`, and the reason declaring an item
    known is not a one-way door: an item that turns out to be shakier than it
    looked can be put back without touching anything else.
    """
    _settle(progress, STAGE_APPRENTICE_1, now, intervals)
    progress.known_at = None
    progress.passed_at = None


def suspend(progress: _ProgressLike) -> None:
    """"Ausblenden" -- out of every queue, without losing the history.

    Deliberately not a delete: the review log still refers to the subject, and
    an item suspended in frustration is usually wanted back later.
    """
    progress.state = ItemState.SUSPENDED
    progress.next_review_at = None
    progress.pending_meaning = None
    progress.pending_reading = None
    progress.session_incorrect = 0


def unsuspend(progress: _ProgressLike, now: datetime) -> None:
    """Put a hidden item back where it was.

    A suspended item that had never been learned returns to the lesson queue,
    and a declared-known or burned one stays out of the rotation. Anything
    else comes back at its old stage and due immediately rather than at the
    remainder of an interval that elapsed while it was hidden -- the point of
    unhiding an item is to see it.
    """
    if progress.srs_stage <= STAGE_UNLEARNED:
        progress.state = ItemState.NEW
        progress.next_review_at = None
        return
    if progress.srs_stage >= STAGE_BURNED:
        progress.state = ItemState.KNOWN if progress.known_at else ItemState.LEARNING
        progress.next_review_at = None
        return
    progress.state = ItemState.LEARNING
    progress.next_review_at = now


def due_cutoff(now: datetime, grace_minutes: int) -> datetime:
    """The timestamp a review must be scheduled before to count as due.

    WaniKani rounds review times down to the hour, so a four-hour item is
    answerable after about three. Without an equivalent, a session that runs
    long leaves items dangling minutes out of reach -- which is exactly the
    friction this trainer is supposed to remove.
    """
    return now + timedelta(minutes=max(0, grace_minutes))
