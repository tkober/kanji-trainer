"""Pydantic schemas: the shapes that cross the HTTP boundary.

Kept separate from the ORM models in :mod:`app.db` because the two differ in
one way that matters: a review queue item must not carry the answer. Serving
the ORM row would ship every accepted meaning and reading to the browser
before the question is answered, and an SRS the learner can read ahead in is
not an SRS.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .srs import QuestionType


class Health(BaseModel):
    status: str
    database: bool


# --- subjects --------------------------------------------------------------


class SubjectSummary(BaseModel):
    """Enough to render an item in a list or a queue. Carries no answers."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    object_type: str
    level: int
    slug: str
    characters: str | None = None
    character_image_url: str | None = None


class SubjectDetail(SubjectSummary):
    """The full item, including its answers. Only ever sent *after* an answer."""

    meanings: list[dict] = Field(default_factory=list)
    readings: list[dict] = Field(default_factory=list)
    parts_of_speech: list[str] = Field(default_factory=list)
    component_subject_ids: list[int] = Field(default_factory=list)
    meaning_mnemonic: str = ""
    meaning_hint: str | None = None
    reading_mnemonic: str | None = None
    reading_hint: str | None = None


class ProgressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    state: str
    srs_stage: int
    stage_name: str
    next_review_at: datetime | None = None
    correct_count: int = 0
    incorrect_count: int = 0
    lapses: int = 0
    known_at: datetime | None = None


class ItemOut(BaseModel):
    """A subject with its progress, for the browse screen."""

    subject: SubjectDetail
    progress: ProgressOut


class ItemPage(BaseModel):
    items: list[ItemOut]
    total: int


# --- studying --------------------------------------------------------------


class QueueItem(BaseModel):
    """One item waiting to be answered."""

    subject: SubjectSummary
    srs_stage: int
    stage_name: str
    #: Questions still outstanding, in asking order. A kanji answered half way
    #: before the tab was closed comes back with only the half it still owes.
    questions: list[QuestionType]


class Queue(BaseModel):
    items: list[QueueItem]
    #: Everything currently due, not just what this page returned.
    total_due: int


class AnswerIn(BaseModel):
    subject_id: int
    question: QuestionType
    answer: str


class AnswerOut(BaseModel):
    correct: bool
    #: The answer that was expected -- shown on a miss, and on a hit when the
    #: learner gave a secondary meaning and should see the primary one.
    expected: str
    secondary: bool = False
    hint: str | None = None
    completed: bool
    remaining: list[QuestionType]
    srs_stage_before: int
    srs_stage_after: int
    stage_name_after: str
    next_review_at: datetime | None = None
    #: Sent once the answer is in, so the review screen can show the mnemonic
    #: without a second request.
    subject: SubjectDetail


class LessonItem(BaseModel):
    subject: SubjectDetail
    srs_stage: int


class Lessons(BaseModel):
    items: list[LessonItem]
    total_available: int
    #: 0 when no limit is configured.
    daily_limit: int


class StartLessonsIn(BaseModel):
    subject_ids: list[int]


# --- overrides -------------------------------------------------------------


class MarkKnownIn(BaseModel):
    subject_ids: list[int]
    #: Overrides the configured stage for this call only -- the browse screen
    #: offers "kenne ich sicher" (burned) and "ziemlich sicher" (one check-up
    #: left) as separate actions.
    known_stage: int | None = None


class SubjectIdsIn(BaseModel):
    subject_ids: list[int]


class OverrideResult(BaseModel):
    changed: int


# --- settings --------------------------------------------------------------


class SettingsOut(BaseModel):
    """Never carries the token itself -- see :mod:`app.api.settings`."""

    wanikani_token_set: bool
    wanikani_token_hint: str
    wanikani_token_from_env: bool
    wanikani_known_srs_stage: int
    known_srs_stage: int
    srs_interval_hours: str
    daily_lesson_limit: int


class SettingsIn(BaseModel):
    # None means "leave alone"; "" means "clear it and fall back to the
    # environment". The two are different and the UI relies on it.
    wanikani_api_token: str | None = None
    wanikani_known_srs_stage: int | None = Field(default=None, ge=1, le=9)
    known_srs_stage: int | None = Field(default=None, ge=1, le=9)
    srs_interval_hours: str | None = None
    daily_lesson_limit: int | None = Field(default=None, ge=0)


class WaniKaniAccount(BaseModel):
    username: str
    level: int
    max_level_granted: int
    subscription_active: bool


# --- import ----------------------------------------------------------------


class ImportStartIn(BaseModel):
    known_srs_stage: int | None = Field(default=None, ge=1, le=9)
    #: Re-apply the WaniKani mapping to items that already have local
    #: progress. Off by default, because the usual second import is to pick up
    #: new WaniKani content without undoing reviews done since the first one.
    remap_existing: bool = False


class ImportRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    known_srs_stage: int
    subjects_total: int
    subjects_imported: int
    assignments_total: int
    marked_known: int
    marked_learning: int
    marked_new: int
    message: str
    started_at: datetime
    finished_at: datetime | None = None


# --- dashboard -------------------------------------------------------------


class Stats(BaseModel):
    total_subjects: int
    new_count: int
    learning_count: int
    known_count: int
    suspended_count: int
    burned_count: int
    apprentice_count: int
    guru_count: int
    master_count: int
    enlightened_count: int
    due_now: int
    due_next_hour: int
    due_today: int
    reviews_today: int
    accuracy_today: float | None = None
