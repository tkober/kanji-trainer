"""ORM rows to API shapes.

One place rather than a helper per router, so that the rule "a queue item
never carries its answers" is visible as a single pair of functions:
:func:`subject_summary` and :func:`subject_detail` differ by exactly that.
"""

from __future__ import annotations

from urllib.parse import quote

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


#: WaniKani's URL segment per subject type. Kana vocabulary lives under
#: /vocabulary/ with the rest.
_WANIKANI_PATHS = {
    "radical": "radicals",
    "kanji": "kanji",
    "vocabulary": "vocabulary",
    "kana_vocabulary": "vocabulary",
}


def wanikani_url(subject: Subject) -> str | None:
    """The item's own page on wanikani.com -- None for a hand-added item.

    Built from the slug rather than imported from WaniKani's ``document_url``,
    which would leave every collection imported before this existed without a
    link until the next import -- and the slug is what that URL is built from
    anyway: the characters for kanji and vocabulary, the name for radicals.

    It belongs to the detail and not to :func:`subject_summary`: that page
    names the meaning, so a queue item carrying the link would carry the
    answer.
    """
    if subject.wanikani_id is None or not subject.slug:
        return None
    path = _WANIKANI_PATHS.get(subject.object_type)
    return None if path is None else f"https://www.wanikani.com/{path}/{quote(subject.slug)}"


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
        wanikani_url=wanikani_url(subject),
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
