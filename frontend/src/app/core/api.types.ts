/**
 * Mirrors backend/app/models.py. Kept by hand rather than generated: the file
 * is small, and the one distinction worth reading — SubjectSummary has no
 * answers, SubjectDetail does — is clearer written out than inferred from a
 * generator.
 */

export type ObjectType = 'radical' | 'kanji' | 'vocabulary' | 'kana_vocabulary';
export type QuestionType = 'meaning' | 'reading';
export type ItemState = 'new' | 'learning' | 'known' | 'suspended';

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

export interface SubjectDetail extends SubjectSummary {
  meanings: Meaning[];
  readings: Reading[];
  parts_of_speech: string[];
  component_subject_ids: number[];
  meaning_mnemonic: string;
  meaning_hint: string | null;
  reading_mnemonic: string | null;
  reading_hint: string | null;
  /** The item's page on wanikani.com — null for a hand-added item. */
  wanikani_url: string | null;
}

export interface Progress {
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

export interface LessonItem {
  subject: SubjectDetail;
  srs_stage: number;
}

export interface LevelSummary {
  level: number;
  open_count: number;
}

export interface Lessons {
  items: LessonItem[];
  /** Which level this batch came from; null when nothing is left anywhere. */
  level: number | null;
  /** Open lessons in that level — the number worth showing. */
  total_in_level: number;
  /** Open lessons everywhere. Context, not a to-do list. */
  total_available: number;
  daily_limit: number;
  batch_size: number;
  levels: LevelSummary[];
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
}

export interface SettingsPatch {
  wanikani_api_token?: string;
  wanikani_known_srs_stage?: number;
  known_srs_stage?: number;
  srs_interval_hours?: string;
  daily_lesson_limit?: number;
  lesson_batch_size?: number;
  soft_answer_enabled?: boolean;
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
}
