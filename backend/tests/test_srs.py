"""The SRS rules, exercised without a database.

This is the module that decides how much work the learner is asked to do, so
it gets the closest reading. The overrides in particular: a bug in
:func:`app.srs.mark_known` would silently put thousands of items back into a
queue the whole project exists to keep them out of.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import srs
from app.srs import ItemState, QuestionType

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class FakeProgress:
    """A stand-in for the ORM row, with the same attributes."""

    def __init__(self, stage: int = 0, state: str = ItemState.NEW.value) -> None:
        self.state = state
        self.srs_stage = stage
        self.next_review_at: datetime | None = None
        self.pending_meaning: bool | None = None
        self.pending_reading: bool | None = None
        self.session_incorrect = 0
        self.correct_count = 0
        self.incorrect_count = 0
        self.lapses = 0
        self.started_at: datetime | None = None
        self.passed_at: datetime | None = None
        self.burned_at: datetime | None = None
        self.known_at: datetime | None = None


def answer(progress: FakeProgress, object_type: str, question: QuestionType, correct: bool):
    return srs.apply_answer(progress, object_type, question, correct, NOW)


# --- scheduling ------------------------------------------------------------


def test_apprentice_one_comes_back_in_four_hours():
    assert srs.schedule(1, NOW) == NOW + timedelta(hours=4)


def test_burned_and_unlearned_are_never_scheduled():
    assert srs.schedule(srs.STAGE_BURNED, NOW) is None
    assert srs.schedule(srs.STAGE_UNLEARNED, NOW) is None


# --- the penalty arithmetic ------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "incorrect", "expected"),
    [
        (4, 1, 3),  # one miss below Guru: one step back
        (4, 2, 3),  # two misses still cost one step -- ceil(2/2) == 1
        (4, 3, 2),  # three misses: two steps
        (5, 1, 3),  # at Guru the penalty doubles
        (7, 2, 5),  # Master, one demotion times two
        (1, 4, 1),  # never below Apprentice I
    ],
)
def test_stage_after_incorrect(stage: int, incorrect: int, expected: int):
    assert srs.stage_after_incorrect(stage, incorrect) == expected


def test_correct_stops_at_burned():
    assert srs.stage_after_correct(srs.STAGE_ENLIGHTENED) == srs.STAGE_BURNED
    assert srs.stage_after_correct(srs.STAGE_BURNED) == srs.STAGE_BURNED


# --- answering -------------------------------------------------------------


def test_kanji_needs_both_questions_before_it_advances():
    progress = FakeProgress(stage=2, state=ItemState.LEARNING.value)

    first = answer(progress, "kanji", QuestionType.MEANING, True)
    assert not first.completed
    assert first.remaining == (QuestionType.READING,)
    assert progress.srs_stage == 2

    second = answer(progress, "kanji", QuestionType.READING, True)
    assert second.completed
    assert progress.srs_stage == 3
    assert progress.next_review_at == NOW + timedelta(hours=24)


def test_radical_advances_on_the_meaning_alone():
    progress = FakeProgress(stage=1, state=ItemState.LEARNING.value)
    outcome = answer(progress, "radical", QuestionType.MEANING, True)
    assert outcome.completed
    assert progress.srs_stage == 2


def test_a_wrong_answer_keeps_the_question_outstanding():
    progress = FakeProgress(stage=3, state=ItemState.LEARNING.value)

    outcome = answer(progress, "kanji", QuestionType.MEANING, False)
    assert not outcome.completed
    assert QuestionType.MEANING in outcome.remaining
    assert progress.session_incorrect == 1
    # Still at its old stage: the demotion happens once, when the item is done.
    assert progress.srs_stage == 3

    answer(progress, "kanji", QuestionType.MEANING, True)
    outcome = answer(progress, "kanji", QuestionType.READING, True)
    assert outcome.completed
    assert progress.srs_stage == 2


def test_missing_both_halves_costs_one_demotion_not_two():
    progress = FakeProgress(stage=4, state=ItemState.LEARNING.value)

    answer(progress, "kanji", QuestionType.MEANING, False)
    answer(progress, "kanji", QuestionType.READING, False)
    answer(progress, "kanji", QuestionType.MEANING, True)
    outcome = answer(progress, "kanji", QuestionType.READING, True)

    assert outcome.completed
    assert progress.srs_stage == 3
    assert progress.session_incorrect == 0  # cleared for the next review


def test_falling_out_of_guru_counts_a_lapse():
    progress = FakeProgress(stage=5, state=ItemState.LEARNING.value)

    answer(progress, "kanji", QuestionType.MEANING, False)
    answer(progress, "kanji", QuestionType.MEANING, True)
    answer(progress, "kanji", QuestionType.READING, True)

    assert progress.srs_stage == 3
    assert progress.lapses == 1


def test_a_miss_that_stays_above_guru_is_not_a_lapse():
    progress = FakeProgress(stage=8, state=ItemState.LEARNING.value)

    answer(progress, "kanji", QuestionType.MEANING, False)
    answer(progress, "kanji", QuestionType.MEANING, True)
    answer(progress, "kanji", QuestionType.READING, True)

    assert progress.srs_stage == 6
    assert progress.lapses == 0


def test_burning_records_the_timestamp_and_stops_scheduling():
    progress = FakeProgress(stage=8, state=ItemState.LEARNING.value)

    answer(progress, "radical", QuestionType.MEANING, True)

    assert progress.srs_stage == srs.STAGE_BURNED
    assert progress.burned_at == NOW
    assert progress.next_review_at is None


def test_demoting_a_burned_item_clears_burned_at():
    progress = FakeProgress(stage=srs.STAGE_BURNED, state=ItemState.LEARNING.value)
    progress.burned_at = NOW - timedelta(days=30)

    answer(progress, "radical", QuestionType.MEANING, False)
    answer(progress, "radical", QuestionType.MEANING, True)

    assert progress.srs_stage == 7
    assert progress.burned_at is None


# --- the overrides ---------------------------------------------------------


def test_mark_known_retires_the_item_and_records_the_intent():
    progress = FakeProgress(stage=0)

    srs.mark_known(progress, NOW)

    assert progress.state == ItemState.KNOWN
    assert progress.srs_stage == srs.STAGE_BURNED
    assert progress.next_review_at is None
    assert progress.known_at == NOW


def test_mark_known_at_enlightened_leaves_one_check_up():
    progress = FakeProgress(stage=0)

    srs.mark_known(progress, NOW, known_stage=srs.STAGE_ENLIGHTENED)

    assert progress.state == ItemState.KNOWN
    assert progress.srs_stage == srs.STAGE_ENLIGHTENED
    assert progress.next_review_at == NOW + timedelta(hours=2880)


def test_mark_known_clamps_a_nonsense_stage():
    progress = FakeProgress()
    srs.mark_known(progress, NOW, known_stage=99)
    assert progress.srs_stage == srs.STAGE_BURNED

    srs.mark_known(progress, NOW, known_stage=-3)
    assert progress.srs_stage == srs.STAGE_APPRENTICE_1


def test_reset_undoes_a_declaration():
    progress = FakeProgress()
    srs.mark_known(progress, NOW)

    srs.reset_to_apprentice(progress, NOW)

    assert progress.state == ItemState.LEARNING
    assert progress.srs_stage == srs.STAGE_APPRENTICE_1
    assert progress.known_at is None
    assert progress.passed_at is None
    assert progress.next_review_at == NOW + timedelta(hours=4)


def test_suspend_clears_the_queue_but_not_the_stage():
    progress = FakeProgress(stage=6, state=ItemState.LEARNING.value)
    progress.next_review_at = NOW

    srs.suspend(progress)

    assert progress.state == ItemState.SUSPENDED
    assert progress.next_review_at is None
    assert progress.srs_stage == 6


def test_unsuspend_brings_a_learning_item_back_due_now():
    progress = FakeProgress(stage=6, state=ItemState.LEARNING.value)
    srs.suspend(progress)

    srs.unsuspend(progress, NOW)

    assert progress.state == ItemState.LEARNING
    assert progress.next_review_at == NOW


def test_unsuspend_returns_an_unlearned_item_to_the_lesson_queue():
    progress = FakeProgress(stage=0)
    srs.suspend(progress)

    srs.unsuspend(progress, NOW)

    assert progress.state == ItemState.NEW
    assert progress.next_review_at is None


def test_half_answered_review_survives_a_reload():
    """The pending flags are persisted, so a closed tab is not a free pass."""
    progress = FakeProgress(stage=3, state=ItemState.LEARNING.value)

    answer(progress, "kanji", QuestionType.MEANING, True)
    # Simulate the learner coming back later: nothing is re-initialised.
    assert srs.outstanding(progress) == (QuestionType.READING,)

    srs.begin_review(progress, "kanji")
    assert srs.outstanding(progress) == (QuestionType.READING,)
