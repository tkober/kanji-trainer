"""How a WaniKani SRS stage becomes a starting position here.

The middle case is the one worth protecting: an item between "started" and
the threshold has to keep the stage it had. Flattening those to Apprentice I
would throw away exactly the progress the import exists to preserve.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.importer import classify, parse_timestamp
from app.srs import STAGE_BURNED, STAGE_ENLIGHTENED, ItemState


@pytest.mark.parametrize("wk_stage", [5, 6, 7, 8, 9])
def test_guru_and_above_arrive_already_known(wk_stage: int):
    mapping = classify(wk_stage, threshold=5, known_stage=STAGE_BURNED)
    assert mapping.state is ItemState.KNOWN
    assert mapping.srs_stage == STAGE_BURNED
    assert not mapping.scheduled


@pytest.mark.parametrize("wk_stage", [1, 2, 3, 4])
def test_apprentice_items_keep_their_stage_and_stay_scheduled(wk_stage: int):
    mapping = classify(wk_stage, threshold=5, known_stage=STAGE_BURNED)
    assert mapping.state is ItemState.LEARNING
    assert mapping.srs_stage == wk_stage
    assert mapping.scheduled


def test_a_locked_or_lesson_item_becomes_new():
    mapping = classify(0, threshold=5, known_stage=STAGE_BURNED)
    assert mapping.state is ItemState.NEW
    assert mapping.srs_stage == 0
    assert not mapping.scheduled


def test_the_threshold_moves_the_boundary():
    """Raising it is how a learner says "I trust my Master items, not Guru"."""
    strict = classify(5, threshold=7, known_stage=STAGE_BURNED)
    assert strict.state is ItemState.LEARNING
    assert strict.srs_stage == 5

    lenient = classify(5, threshold=4, known_stage=STAGE_BURNED)
    assert lenient.state is ItemState.KNOWN


def test_the_known_stage_is_configurable_and_clamped():
    assert classify(9, 5, STAGE_ENLIGHTENED).srs_stage == STAGE_ENLIGHTENED
    assert classify(9, 5, 99).srs_stage == STAGE_BURNED
    assert classify(9, 5, 0).srs_stage == 1


def test_parse_timestamp_handles_wanikanis_format():
    parsed = parse_timestamp("2026-09-20T15:00:00.000000Z")
    assert parsed == datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [None, "", "not a date", 42])
def test_parse_timestamp_tolerates_rubbish(value: object):
    assert parse_timestamp(value) is None
