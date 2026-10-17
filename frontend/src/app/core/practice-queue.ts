import type { QuestionType, SubjectSummary } from './api.types';
import { shuffle } from './review-queue';

/** What `buildPracticeQueue` and the first-try tally below both take --
 * `PracticeItem` from `review-tally.ts` already has this exact shape, but
 * this module stays independent of it (it is pure queue/scoring logic, not
 * a consumer of the review tally). */
export interface PracticeSource {
  subject: SubjectSummary;
  questions: readonly QuestionType[];
}

export interface PracticeQueueEntry {
  subject: SubjectSummary;
  question: QuestionType;
}

/**
 * One entry per (item, question), for a practice round (#96).
 *
 * Each item's own questions are shuffled the same way `shuffleQuestions`
 * shuffles a review item's (meaning/reading order), then the whole
 * flattened queue is shuffled again, so the round does not ask every
 * item's questions back to back the way a plain per-item flatten would.
 */
export function buildPracticeQueue(items: readonly PracticeSource[]): PracticeQueueEntry[] {
  const entries: PracticeQueueEntry[] = [];
  for (const item of items) {
    for (const question of shuffle(item.questions)) {
      entries.push({ subject: item.subject, question });
    }
  }
  return shuffle(entries);
}

function entryKey(subjectId: number, question: QuestionType): string {
  return `${subjectId}:${question}`;
}

/**
 * Whether the *first* attempt at each (item, question) in this round was
 * correct -- written once per key and never overwritten, so a later retry
 * after a wrong answer (the entry comes back via `reinsert`, same as a
 * review's own E4) does not change what "first try" means.
 *
 * A "retry" verdict (a real reading of the wrong question, per
 * `answers.py`/CLAUDE.md) and the soft-answer hold are not attempts and
 * must not be recorded here at all -- same rule as a review's own tally.
 */
export interface PracticeTally {
  firstAttempts: Readonly<Record<string, boolean>>;
}

export const EMPTY_PRACTICE_TALLY: PracticeTally = { firstAttempts: {} };

export function recordPracticeAttempt(
  tally: PracticeTally,
  subjectId: number,
  question: QuestionType,
  correct: boolean,
): PracticeTally {
  const key = entryKey(subjectId, question);
  if (key in tally.firstAttempts) {
    return tally;
  }
  return { firstAttempts: { ...tally.firstAttempts, [key]: correct } };
}

export interface PracticeRoundSummary {
  /** Items actually counted this round -- `items.length` minus any
   *  `skippedIds` (a 409, the item became due mid-round). */
  items: number;
  /** Items where every question asked this round was correct on its very
   *  first attempt. */
  firstTryItems: number;
  /** Items that needed more than one try for at least one question, or
   *  were never answered at all (an early "End") -- first-seen order,
   *  `skippedIds` excluded. */
  shaky: readonly SubjectSummary[];
}

/**
 * The practice end screen's numbers (#96). An item counts as "first try"
 * only if *every* question it was asked this round shows up correct in
 * `tally` -- a question never attempted at all (an early "End") is not in
 * `tally.firstAttempts` either, so it reads the same as a wrong one:
 * "not answered correctly on the first try".
 */
export function summarizePracticeRound(
  items: readonly PracticeSource[],
  tally: PracticeTally,
  skippedIds: ReadonlySet<number> = new Set(),
): PracticeRoundSummary {
  const counted = items.filter((item) => !skippedIds.has(item.subject.id));
  let firstTryItems = 0;
  const shaky: SubjectSummary[] = [];
  for (const item of counted) {
    const allFirstTry = item.questions.every(
      (question) => tally.firstAttempts[entryKey(item.subject.id, question)] === true,
    );
    if (allFirstTry) {
      firstTryItems += 1;
    } else {
      shaky.push(item.subject);
    }
  }
  return { items: counted.length, firstTryItems, shaky };
}

/**
 * The shaky items' own wrong questions, for "Practice again" (E4: a next
 * round drills only the wobbly ones, and only the halves that were actually
 * wrong, same E1 reasoning as the first round). Falls back to every
 * question the item owes if none of them come back wrong somehow -- should
 * not happen for a genuinely shaky item, but it keeps this total rather
 * than handing back an item with nothing to ask.
 */
export function shakyPracticeItems(
  items: readonly PracticeSource[],
  tally: PracticeTally,
  skippedIds: ReadonlySet<number> = new Set(),
): PracticeSource[] {
  const result: PracticeSource[] = [];
  for (const item of items) {
    if (skippedIds.has(item.subject.id)) {
      continue;
    }
    const wrong = item.questions.filter(
      (question) => tally.firstAttempts[entryKey(item.subject.id, question)] !== true,
    );
    if (wrong.length > 0) {
      result.push({ subject: item.subject, questions: wrong });
    }
  }
  return result;
}
