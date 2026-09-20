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
  expected: string;
  secondary: boolean;
  hint: string | null;
  completed: boolean;
  remaining: QuestionType[];
  srs_stage_before: number;
  srs_stage_after: number;
  stage_name_after: string;
  next_review_at: string | null;
  subject: SubjectDetail;
}

export interface LessonItem {
  subject: SubjectDetail;
  srs_stage: number;
}

export interface Lessons {
  items: LessonItem[];
  total_available: number;
  daily_limit: number;
}

export interface Settings {
  wanikani_token_set: boolean;
  wanikani_token_hint: string;
  wanikani_token_from_env: boolean;
  wanikani_known_srs_stage: number;
  known_srs_stage: number;
  srs_interval_hours: string;
  daily_lesson_limit: number;
}

export interface SettingsPatch {
  wanikani_api_token?: string;
  wanikani_known_srs_stage?: number;
  known_srs_stage?: number;
  srs_interval_hours?: string;
  daily_lesson_limit?: number;
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
  due_now: number;
  due_next_hour: number;
  due_today: number;
  reviews_today: number;
  accuracy_today: number | null;
}
