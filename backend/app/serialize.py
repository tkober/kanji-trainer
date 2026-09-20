"""ORM rows to API shapes.

One place rather than a helper per router, so that the rule "a queue item
never carries its answers" is visible as a single pair of functions:
:func:`subject_summary` and :func:`subject_detail` differ by exactly that.
"""

from __future__ import annotations

from .db import Progress, Subject
from .models import ProgressOut, SubjectDetail, SubjectSummary
from .srs import stage_name


def subject_summary(subject: Subject) -> SubjectSummary:
    """The safe shape: enough to render the prompt, nothing to answer it."""
    return SubjectSummary(
        id=subject.id,
        object_type=subject.object_type,
        level=subject.level,
        slug=subject.slug,
        characters=subject.characters,
        character_image_url=subject.character_image_url,
    )


def subject_detail(subject: Subject) -> SubjectDetail:
    """Everything, answers included. Only after the question is answered."""
    return SubjectDetail(
        id=subject.id,
        object_type=subject.object_type,
        level=subject.level,
        slug=subject.slug,
        characters=subject.characters,
        character_image_url=subject.character_image_url,
        meanings=subject.meanings,
        readings=subject.readings,
        parts_of_speech=subject.parts_of_speech,
        component_subject_ids=subject.component_subject_ids,
        meaning_mnemonic=subject.meaning_mnemonic,
        meaning_hint=subject.meaning_hint,
        reading_mnemonic=subject.reading_mnemonic,
        reading_hint=subject.reading_hint,
    )


def progress_out(progress: Progress) -> ProgressOut:
    return ProgressOut(
        state=progress.state,
        srs_stage=progress.srs_stage,
        stage_name=stage_name(progress.srs_stage),
        next_review_at=progress.next_review_at,
        correct_count=progress.correct_count,
        incorrect_count=progress.incorrect_count,
        lapses=progress.lapses,
        known_at=progress.known_at,
    )
