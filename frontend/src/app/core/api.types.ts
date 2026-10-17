/**
 * Mirrors backend/app/models.py. Kept by hand rather than generated: the file
 * is small, and the one distinction worth reading — SubjectSummary has no
 * answers, SubjectDetail does — is clearer written out than inferred from a
 * generator.
 */

export type ObjectType = 'radical' | 'kanji' | 'vocabulary' | 'kana_vocabulary';
export type QuestionType = 'meaning' | 'reading';
type ItemState = 'new' | 'learning' | 'known' | 'suspended';

/** Mirrors backend/app/models.py REVIEW_ITEM_ORDERS / REVIEW_TYPE_ORDERS. */
export type ReviewItemOrder =
  'random' | 'oldest_first' | 'lowest_stage_first' | 'lowest_level_first';
export type ReviewTypeOrder = 'mixed' | 'grouped';

/** What the review queue is allowed to carry: the prompt, never the answer. */
export interface SubjectSummary {
  id: number;
  object_type: ObjectType;
  level: number;
  slug: string;
  characters: string | null;
  character_image_url: string | null;
}

export interface Meaning {
  meaning: string;
  primary?: boolean;
  accepted_answer?: boolean;
}

export interface Reading {
  reading: string;
  primary?: boolean;
  accepted_answer?: boolean;
  type?: string;
}

export interface ContextSentence {
  ja: string;
  en: string;
}

export interface SubjectDetail extends SubjectSummary {
  meanings: Meaning[];
  readings: Reading[];
  parts_of_speech: string[];
  component_subject_ids: number[];
  meaning_mnemonic: string;
  meaning_hint: string | null;
  reading_mnemonic: string | null;
  reading_hint: string | null;
  /** Vocabulary/kana_vocabulary only, empty elsewhere. Detail only — the
   *  English half names the word being asked about. */
  context_sentences: ContextSentence[];
  /** The learner's own synonyms (issue #33) — accepted answers for the
   *  *meaning*, never the reading. Detail only, same reasoning as above: a
   *  queue item must not carry its own accepted answers. */
  synonyms: string[];
  /** The item's page on wanikani.com — null for a hand-added item. */
  wanikani_url: string | null;
}

/** Never carries the SVG itself -- see `Api.illustration`. */
export interface Illustration {
  available: boolean;
  alt: string | null;
}

interface Progress {
  state: ItemState;
  srs_stage: number;
  stage_name: string;
  next_review_at: string | null;
  correct_count: number;
  incorrect_count: number;
  lapses: number;
  known_at: string | null;
}

export interface Item {
  subject: SubjectDetail;
  progress: Progress;
}

export interface ItemPage {
  items: Item[];
  total: number;
}

export interface QueueItem {
  subject: SubjectSummary;
  srs_stage: number;
  stage_name: string;
  questions: QuestionType[];
}

export interface Queue {
  items: QueueItem[];
  total_due: number;
  /** The sum of open questions over every due item, not just this page --
   *  the exact session total from the first fetch, before a multi-round
   *  backlog has been paged through (issue #54). */
  total_due_questions: number;
}

export interface AnswerResult {
  correct: boolean;
  /** Empty while the question is still open — see `held` and `retry`. */
  expected: string;
  secondary: boolean;
  /** Accepted despite a misspelling; the right spelling is in `expected`. */
  typo: boolean;
  hint: string | null;
  /** Would be wrong, but nothing has happened yet. One keypress to insist. */
  held: boolean;
  /** A real reading of the character, of the type not asked. Costs nothing. */
  retry: boolean;
  completed: boolean;
  remaining: QuestionType[];
  srs_stage_before: number;
  srs_stage_after: number;
  stage_name_after: string;
  next_review_at: string | null;
  /** Null while the question is still open, so the answer is not revealed. */
  subject: SubjectDetail | null;
}

/** A practice verdict (#95/#96, epic #85) -- mirrors backend/app/models.py's
 *  `PracticeOut`. No SRS fields (practice moves nothing) and no `held`:
 *  the soft-answer hold is simulated entirely in the frontend, see
 *  `pages/review/review-practice`. Unlike `AnswerResult`, `expected` and
 *  `subject` are never withheld -- there is no held verdict to protect them
 *  from, see `backend/app/api/study.py`'s `practice_answer` for why that is
 *  safe here. */
export interface PracticeOut {
  correct: boolean;
  expected: string;
  secondary: boolean;
  typo: boolean;
  /** A real reading of the character, of the type not asked. Costs nothing. */
  retry: boolean;
  hint: string | null;
  subject: SubjectDetail;
}

export interface LessonItem {
  subject: SubjectDetail;
  srs_stage: number;
}

export interface LevelSummary {
  level: number;
  open_count: number;
  /** Every subject in the level, learned or not. */
  total_count: number;
  /** Open counts by type -- vocabulary and kana_vocabulary combined. */
  open_radicals: number;
  open_kanji: number;
  open_vocabulary: number;
}

/** One item of the chosen level, for the picker. No answers — see `LessonTile`. */
export interface LessonTile {
  subject: SubjectSummary;
  state: ItemState;
  srs_stage: number;
}

export interface Lessons {
  /** Which level is shown; null when the collection has no subjects at all. */
  level: number | null;
  /** Open lessons in that level — the number worth showing. */
  total_in_level: number;
  /** Open lessons everywhere. Context, not a to-do list. */
  total_available: number;
  daily_limit: number;
  batch_size: number;
  /** Every level that has any subject, including fully learned ones. */
  levels: LevelSummary[];
  /** Every item of the chosen level, in every state. No answers. */
  tiles: LessonTile[];
  /** Lessons still allowed today under the daily limit; null when unlimited. */
  remaining_today: number | null;
}

/** A lesson-quiz verdict. Carries no SRS fields, because it moves nothing. */
export interface QuizResult {
  correct: boolean;
  expected: string;
  secondary: boolean;
  typo: boolean;
  retry: boolean;
  hint: string | null;
}

export interface Settings {
  wanikani_token_set: boolean;
  wanikani_token_hint: string;
  wanikani_token_from_env: boolean;
  wanikani_known_srs_stage: number;
  known_srs_stage: number;
  srs_interval_hours: string;
  daily_lesson_limit: number;
  lesson_batch_size: number;
  soft_answer_enabled: boolean;
  review_item_order: ReviewItemOrder;
  review_type_order: ReviewTypeOrder;
}

export interface SettingsPatch {
  wanikani_api_token?: string;
  wanikani_known_srs_stage?: number;
  known_srs_stage?: number;
  srs_interval_hours?: string;
  daily_lesson_limit?: number;
  lesson_batch_size?: number;
  soft_answer_enabled?: boolean;
  review_item_order?: ReviewItemOrder;
  review_type_order?: ReviewTypeOrder;
}

export interface WaniKaniAccount {
  username: string;
  level: number;
  max_level_granted: number;
  subscription_active: boolean;
}

export interface ImportRun {
  id: number;
  status: 'running' | 'succeeded' | 'failed';
  known_srs_stage: number;
  subjects_total: number;
  subjects_imported: number;
  assignments_total: number;
  marked_known: number;
  marked_learning: number;
  marked_new: number;
  message: string;
  started_at: string;
  finished_at: string | null;
}

export interface ForecastBucket {
  /** Start of the hour, UTC. Rendered in local time. */
  at: string;
  count: number;
  apprentice: number;
  guru: number;
  master: number;
  /** Everything due up to and including this hour, overdue items included. */
  cumulative: number;
}

export interface Forecast {
  now: string;
  due_now: number;
  hours: number;
  total: number;
  buckets: ForecastBucket[];
}

export interface Stats {
  total_subjects: number;
  new_count: number;
  learning_count: number;
  known_count: number;
  suspended_count: number;
  burned_count: number;
  apprentice_count: number;
  guru_count: number;
  master_count: number;
  enlightened_count: number;
  /** Lessons in the level currently being taught — what the badge shows. */
  lessons_available: number;
  current_level: number | null;
  due_now: number;
  due_next_hour: number;
  due_today: number;
  reviews_today: number;
  accuracy_today: number | null;
  /** Earliest `next_review_at` among scheduled items not due yet; null when
   *  nothing is scheduled. An item inside the grace window is due, not next. */
  next_review_at: string | null;
  /** How many scheduled items share `next_review_at`'s UTC hour -- "next 3
   *  at 14:00". 0 when `next_review_at` is null. */
  next_review_count: number;
  /** Distinct subjects with a review_log row crossing into Guru in the
   *  last/previous 7 rolling days -- each subject counts once. */
  guru_last_7_days: number;
  guru_previous_7_days: number;
}

/** One (level, object_type) bucket, by what the item is doing right now.
 *  `burned` counts only items earned by review -- a declared-known item at
 *  the same stage is `known` instead, same split `Stats.burned_count` uses. */
export interface StageCounts {
  new: number;
  apprentice_1: number;
  apprentice_2: number;
  apprentice_3: number;
  apprentice_4: number;
  guru_1: number;
  guru_2: number;
  master: number;
  enlightened: number;
  burned: number;
  known: number;
  suspended: number;
}

export interface LevelTypeCounts {
  level: number;
  object_type: ObjectType;
  counts: StageCounts;
}

export interface LevelStats {
  /** Same `lowest_open_level()` definition `Stats.current_level` uses. */
  current_level: number | null;
  /** Every level that has any subject, sorted by level then object_type. */
  levels: LevelTypeCounts[];
}

/** One (JLPT level, object_type) bucket -- `kana_vocabulary` is already
 *  folded into `vocabulary` server-side, unlike `LevelTypeCounts`. */
export interface JlptTypeCounts {
  jlpt: number;
  object_type: 'kanji' | 'vocabulary';
  counts: StageCounts;
}

export interface JlptStats {
  /** Across all of WaniKani, independent of any level frame -- every
   *  (jlpt, object_type) pair that has at least one subject. */
  levels: JlptTypeCounts[];
  /** All non-suspended vocabulary + kana_vocabulary subjects, and how many
   *  of those carry no JLPT estimate -- the figures the card's footnote
   *  quotes as a percentage. */
  vocabulary_total: number;
  vocabulary_without_level: number;
}

export interface ActivityDay {
  /** ISO date (`YYYY-MM-DD`), the local calendar day this entry covers. */
  date: string;
  answers: number;
  correct: number;
  lessons_started: number;
  /** Distinct subjects with a Guru crossing this local day. */
  reached_guru: number;
}

export interface Activity {
  /** One entry per local calendar day in the requested window, oldest
   *  first, zero-filled. */
  days: ActivityDay[];
  /** Consecutive local days with at least one answer, ending today or
   *  (if today has none yet) yesterday. */
  streak: number;
}

export interface GrowthDay {
  /** ISO date (`YYYY-MM-DD`), the local calendar day this entry covers. */
  date: string;
  /** Guru+ earned by review, not (yet) marked as known, at the end of this
   *  local day (today: as of now). */
  earned: number;
  /** Marked as known (import or "das kann ich"), at the end of this local
   *  day. */
  known: number;
}

export interface Growth {
  /** One entry per local calendar day, oldest first, zero-filled from the
   *  earliest available history (or from the requested `days` ago,
   *  whichever is later). */
  days: GrowthDay[];
  /** Local date of the first succeeded import, or null if there never was
   *  one. */
  imported_on: string | null;
  /** known count at the end of `imported_on`'s local day -- the Growth
   *  card's marker label. */
  known_on_import: number;
}
