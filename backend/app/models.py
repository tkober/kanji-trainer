"""Pydantic schemas: the shapes that cross the HTTP boundary.

Kept separate from the ORM models in :mod:`app.db` because the two differ in
one way that matters: a review queue item must not carry the answer. Serving
the ORM row would ship every accepted meaning and reading to the browser
before the question is answered, and an SRS the learner can read ahead in is
not an SRS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .srs import QuestionType

# The allowed review-order values, defined once so runtime_config.py (falling
# back on a bad stored or env value) and the API schemas below (rejecting one
# with a 422) agree on what "valid" means. srs.py stays dependency-free on
# purpose, so these live here rather than there.
REVIEW_ITEM_ORDERS: tuple[str, ...] = (
    "random",
    "oldest_first",
    "lowest_stage_first",
    "lowest_level_first",
)
ReviewItemOrder = Literal["random", "oldest_first", "lowest_stage_first", "lowest_level_first"]

REVIEW_TYPE_ORDERS: tuple[str, ...] = ("mixed", "grouped")
ReviewTypeOrder = Literal["mixed", "grouped"]


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
    #: [{"en": "...", "ja": "..."}, ...] -- vocabulary/kana_vocabulary only,
    #: empty elsewhere. Detail only, like everything above: the English
    #: sentence names the word being asked about and would reveal the answer
    #: on a queue item. "Pattern of use" and "Common word combinations" are
    #: shown on wanikani.com but are not in the API, so they are not imported.
    context_sentences: list[dict] = Field(default_factory=list)
    #: The learner's own synonyms (issue #33) -- accepted answers for the
    #: *meaning*, never the reading. Detail only, like everything above: a
    #: queue item must not carry its own accepted answers.
    synonyms: list[str] = Field(default_factory=list)
    meaning_mnemonic: str = ""
    meaning_hint: str | None = None
    reading_mnemonic: str | None = None
    reading_hint: str | None = None
    #: The item's page on wanikani.com, empty for a hand-added item. Detail
    #: only -- that page names the meaning, so a queue item must not carry it.
    wanikani_url: str | None = None


class IllustrationOut(BaseModel):
    """Whether a radical's mnemonic illustration is there to show, and its
    alt text -- never the SVG itself, which has its own endpoint so it can be
    cached and referenced from an ``<img src>`` directly."""

    available: bool
    alt: str | None = None


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
    #: Set by the second Enter after a "that looks wrong" warning. Without
    #: it a wrong answer is held rather than applied -- see `held` below.
    confirm: bool = False
    #: Alt+H: give up and have the answer revealed on purpose. Counts as
    #: wrong whatever `answer` holds, skips both second chances (`held` and
    #: `retry` below) and commits exactly like a confirmed wrong answer --
    #: the one way to answer that is allowed to reveal anything.
    gave_up: bool = False


class AnswerOut(BaseModel):
    """The verdict on one answer.

    Three outcomes rather than two, and the extra two are both second chances:

    * ``held`` -- the answer would be wrong, and nothing has happened yet. The
      learner gets one keypress to insist or to correct a typo. **No** expected
      answer and no subject come back, or the warning would be a free reveal.
    * ``retry`` -- a real reading of the character, of the type that was not
      asked. Not counted, not logged, question still open. Charging for this
      would punish knowing more than was asked.

    ``expected`` and ``subject`` are empty exactly when one of those is set.
    """

    correct: bool
    #: The answer that was expected -- shown on a miss, and on a hit when the
    #: learner gave a secondary meaning and should see the primary one.
    expected: str = ""
    secondary: bool = False
    #: Accepted despite a misspelling; the UI shows the right spelling.
    typo: bool = False
    hint: str | None = None
    held: bool = False
    retry: bool = False
    completed: bool
    remaining: list[QuestionType]
    srs_stage_before: int
    srs_stage_after: int
    stage_name_after: str
    next_review_at: datetime | None = None
    #: Sent once the answer is in, so the review screen can show the mnemonic
    #: without a second request. None while the question is still open.
    subject: SubjectDetail | None = None


class LessonItem(BaseModel):
    subject: SubjectDetail
    srs_stage: int


class LevelSummary(BaseModel):
    level: int
    open_count: int
    #: Every subject in the level, learned or not -- what the picker needs to
    #: say "done" instead of just "0 left" for a level with nothing open.
    total_count: int
    #: Open counts split by type, for the level picker -- vocabulary and
    #: kana_vocabulary are combined under `open_vocabulary`, the same grouping
    #: `_review_order`'s "grouped" type order and the lessons tile groups use.
    open_radicals: int = 0
    open_kanji: int = 0
    open_vocabulary: int = 0


class LessonTile(BaseModel):
    """One item of the chosen level, for the learner to pick from.

    Carries no answers -- same reasoning as ``QueueItem``: a tile for an item
    already in the review rotation (``state == learning``) or known must not
    leak meanings or readings, so this wraps ``SubjectSummary``, never
    ``SubjectDetail``.
    """

    subject: SubjectSummary
    state: str
    srs_stage: int


class Lessons(BaseModel):
    """The selection view: every level, and every item of the chosen one.

    Scoped to a level on purpose. Without it ``total_available`` is "every
    unlearned item in all sixty levels", which after an import of a reset
    account is a five-figure number that means nothing and cannot be acted on.

    Unlike a queue, this is not a batch to work through in order -- it is the
    whole level, tiled, so the learner can select exactly the items they want
    and start a run with those. Batching by ``batch_size`` happens client-side
    once a selection is made, against ``GET /api/lessons/items``.
    """

    #: Which level is shown; None when the collection has no subjects at all.
    level: int | None = None
    #: Open lessons in that level -- the number worth showing.
    total_in_level: int = 0
    #: Open lessons everywhere. Context, not a to-do list.
    total_available: int
    #: 0 when no limit is configured.
    daily_limit: int
    batch_size: int
    #: Every level that has any subject, for the picker -- including levels
    #: fully learned, which report ``open_count == 0``.
    levels: list[LevelSummary] = Field(default_factory=list)
    #: Every item of the chosen level, in every state. No answers -- see
    #: `LessonTile`.
    tiles: list[LessonTile] = Field(default_factory=list)
    #: Lessons still allowed today under the daily limit; None when no limit
    #: is configured.
    remaining_today: int | None = None


class StartLessonsIn(BaseModel):
    subject_ids: list[int]


class QuizIn(BaseModel):
    subject_id: int
    question: QuestionType
    answer: str
    #: Alt+H in the lesson quiz. Counts as wrong and reveals `expected`, same
    #: as any other wrong quiz answer -- the quiz moves nothing either way.
    gave_up: bool = False


class QuizOut(BaseModel):
    """A lesson-quiz verdict. Carries no SRS fields, because it moves nothing.

    The quiz is a gate in front of the SRS, not a part of it -- exactly as in
    WaniKani, where failing the lesson quiz costs no stage because the item
    has no stage yet.
    """

    correct: bool
    expected: str
    secondary: bool = False
    typo: bool = False
    retry: bool = False
    hint: str | None = None


# --- overrides -------------------------------------------------------------


class MarkKnownIn(BaseModel):
    subject_ids: list[int]
    #: Overrides the configured stage for this call only -- the browse screen
    #: offers "I know these" (burned) and "fairly sure" (one check-up
    #: left) as separate actions.
    known_stage: int | None = None


class SubjectIdsIn(BaseModel):
    subject_ids: list[int]


# --- synonyms ----------------------------------------------------------------


class SynonymsIn(BaseModel):
    synonyms: list[str]


class SynonymsOut(BaseModel):
    """The normalised, stored list -- what the server actually kept, which
    may differ from what was sent (trimmed, deduped, reordered to first
    spelling wins)."""

    synonyms: list[str]


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
    lesson_batch_size: int
    soft_answer_enabled: bool
    review_item_order: ReviewItemOrder
    review_type_order: ReviewTypeOrder


class SettingsIn(BaseModel):
    # None means "leave alone"; "" means "clear it and fall back to the
    # environment". The two are different and the UI relies on it.
    wanikani_api_token: str | None = None
    wanikani_known_srs_stage: int | None = Field(default=None, ge=1, le=9)
    known_srs_stage: int | None = Field(default=None, ge=1, le=9)
    srs_interval_hours: str | None = None
    daily_lesson_limit: int | None = Field(default=None, ge=0)
    lesson_batch_size: int | None = Field(default=None, ge=1, le=100)
    soft_answer_enabled: bool | None = None
    review_item_order: ReviewItemOrder | None = None
    review_type_order: ReviewTypeOrder | None = None


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


class ForecastBucket(BaseModel):
    """One hour of the forecast."""

    #: Start of the hour, UTC. The browser renders it in local time.
    at: datetime
    count: int
    #: The same count split by where the items currently stand, so a bar says
    #: whether tomorrow's pile is new material or old material returning.
    apprentice: int = 0
    guru: int = 0
    master: int = 0
    #: Everything due from now up to and including this hour, overdue items
    #: included -- the line that answers "how deep is the hole by Friday".
    cumulative: int


class Forecast(BaseModel):
    now: datetime
    #: Already overdue. The opening value of the cumulative line rather than a
    #: bar of its own, because it is not arriving -- it is here.
    due_now: int
    hours: int
    #: Reviews arriving in the window, excluding what is already due.
    total: int
    buckets: list[ForecastBucket]


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
    #: Lessons in the level currently being taught -- what the badge shows.
    #: ``new_count`` stays the collection-wide figure for the breakdown.
    lessons_available: int
    current_level: int | None = None
    due_now: int
    due_next_hour: int
    due_today: int
    reviews_today: int
    accuracy_today: float | None = None
