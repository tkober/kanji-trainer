import type { QuestionType } from './api.types';

/**
 * Randomise the order of an item's own questions.
 *
 * The backend always builds `questions` as `['meaning', 'reading']`
 * (`srs.required_questions` / `srs.outstanding`) — a fixed order the server
 * itself does not care about (`submit_answer` accepts any outstanding
 * question). Left as-is, every item's first sighting is its meaning, so a
 * shuffled round of items still asks every meaning before any reading can
 * appear. Fisher–Yates on two elements is just a coin flip, but the same
 * function also covers an item with more than two outstanding questions.
 */
export function shuffleQuestions<T extends { questions: QuestionType[] }>(item: T): T {
  const questions = [...item.questions];
  for (let i = questions.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [questions[i], questions[j]] = [questions[j], questions[i]];
  }
  return { ...item, questions };
}

/**
 * Re-insert a half-answered item somewhere later in the round, never right
 * at the back.
 *
 * A straight `push` to the back is what caused issue #27: with rounds of a
 * hundred items, the second half of every item lands behind the first half
 * of every other item, so a correctly-shuffled round degenerates into "all
 * meanings, then all readings". A straight re-insert at the front is no
 * better the other way — answering the reading immediately after the
 * meaning tests nothing, since the answer is still on screen. So `item`
 * goes at a uniformly random index at least `minGap` slots away (or at the
 * end, if the queue is too short for that gap to mean anything).
 */
export function reinsert<T>(rest: T[], item: T, minGap = 3): T[] {
  const start = Math.min(minGap, rest.length);
  const index = start + Math.floor(Math.random() * (rest.length - start + 1));
  const next = [...rest];
  next.splice(index, 0, item);
  return next;
}
