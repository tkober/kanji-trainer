import type { AnswerResult, ObjectType, QuestionType, SubjectSummary } from './api.types';

/** WaniKani's stage groups. 0 (unlearned) has none. */
export type StageGroup = 'apprentice' | 'guru' | 'master' | 'enlightened' | 'burned';
/** The groups a review can *rise into* -- Apprentice is never a rise target:
 * every completed review starts already at Apprentice I or above. */
export type RiseGroup = Exclude<StageGroup, 'apprentice'>;
/** Summary types: `kana_vocabulary` counts as vocabulary, same folding
 * `app.jlpt.fold_object_type` does on the backend. */
export type TallyType = 'radical' | 'kanji' | 'vocabulary';

/** The ladder, in rise order -- used both to resolve a stage and to order
 * `flow` below ("from then to along the ladder"). */
const LADDER: readonly StageGroup[] = ['apprentice', 'guru', 'master', 'enlightened', 'burned'];

/** WaniKani's stage groups (`backend/app/srs.py`'s `STAGE_*` constants):
 * Apprentice I-IV, Guru I-II, Master, Enlightened, Burned. Stage 0 has not
 * been learned yet (`null`); anything past Burned clamps to it rather than
 * going out of range, the same way `srs.stage_name` clamps for display. */
export function stageGroup(stage: number): StageGroup | null {
  if (stage <= 0) {
    return null;
  }
  if (stage <= 4) {
    return 'apprentice';
  }
  if (stage <= 6) {
    return 'guru';
  }
  if (stage === 7) {
    return 'master';
  }
  if (stage === 8) {
    return 'enlightened';
  }
  return 'burned';
}

/** Every completed item has already started -- `GET /api/reviews` never
 * serves a stage-0 subject -- so `stageGroup` is never actually `null` here.
 * Falling back to `'apprentice'` keeps `flow`'s `from`/`to` non-nullable
 * without an unchecked assertion. */
function groupOf(stage: number): StageGroup {
  return stageGroup(stage) ?? 'apprentice';
}

function ladderRank(group: StageGroup): number {
  return LADDER.indexOf(group);
}

/** `kana_vocabulary` counts as vocabulary everywhere this summary cares --
 * it is spelled in kana already, and the learner never sees it as a
 * separate bucket. */
export function tallyType(type: ObjectType): TallyType {
  return type === 'kana_vocabulary' ? 'vocabulary' : type;
}

/** Mirrors backend/app/srs.py's `REQUIRED_QUESTIONS` -- what one review (or
 * practice round) of this object type asks. Exported for `summarize`'s
 * "no wrong answer recorded this session" fallback (#96) and for anything
 * else that needs the same mapping without re-deriving it. */
const REQUIRED_QUESTIONS: Readonly<Record<ObjectType, readonly QuestionType[]>> = {
  radical: ['meaning'],
  kanji: ['meaning', 'reading'],
  vocabulary: ['meaning', 'reading'],
  kana_vocabulary: ['meaning'],
};

export function requiredQuestions(type: ObjectType): readonly QuestionType[] {
  return REQUIRED_QUESTIONS[type];
}

export interface CompletedItem {
  subject: SubjectSummary;
  stageBefore: number;
  stageAfter: number;
}

/** Raw events of one session, appended to immutably. */
export interface ReviewTally {
  completed: readonly CompletedItem[]; // in completion order
  answers: Readonly<Record<QuestionType, { answered: number; correct: number }>>;
  /** Subject ids with at least one counted wrong answer this session. */
  wrongIds: readonly number[]; // unique
  /** Subject ids that received at least one counted answer. */
  touchedIds: readonly number[]; // unique
  knownIds: readonly number[]; // "I know this"
  resetIds: readonly number[]; // "Relearn" / reset to Apprentice I
  /** Per subject, which question types got at least one counted *wrong*
   *  answer this session (#96, "Review mistakes") -- monotonic, like
   *  `wrongIds`: once a question is wrong it stays in this set even if a
   *  later presentation of the same half (same item, re-asked) is answered
   *  correctly, since the question was still wrong *at some point*. */
  wrongQuestions: Readonly<Record<number, readonly QuestionType[]>>;
}

export const EMPTY_TALLY: ReviewTally = {
  completed: [],
  answers: {
    meaning: { answered: 0, correct: 0 },
    reading: { answered: 0, correct: 0 },
  },
  wrongIds: [],
  touchedIds: [],
  knownIds: [],
  resetIds: [],
  wrongQuestions: {},
};

function withWrongQuestion(
  record: Readonly<Record<number, readonly QuestionType[]>>,
  subjectId: number,
  question: QuestionType,
): Readonly<Record<number, readonly QuestionType[]>> {
  const existing = record[subjectId] ?? [];
  if (existing.includes(question)) {
    return record;
  }
  return { ...record, [subjectId]: [...existing, question] };
}

function withId(ids: readonly number[], id: number): readonly number[] {
  return ids.includes(id) ? ids : [...ids, id];
}

/**
 * A settled verdict. Ignores `held` and `retry` results entirely (they
 * count for nothing, same as the page's own `answered`/`correct` counters):
 * `held` has not happened yet (one keypress from being withdrawn), and
 * `retry` is a real answer of the wrong question, not a verdict on this one.
 */
export function recordAnswer(
  tally: ReviewTally,
  subject: SubjectSummary,
  question: QuestionType,
  result: AnswerResult,
): ReviewTally {
  if (result.held || result.retry) {
    return tally;
  }

  const before = tally.answers[question];
  const answers: ReviewTally['answers'] = {
    ...tally.answers,
    [question]: {
      answered: before.answered + 1,
      correct: before.correct + (result.correct ? 1 : 0),
    },
  };

  return {
    ...tally,
    answers,
    touchedIds: withId(tally.touchedIds, subject.id),
    wrongIds: result.correct ? tally.wrongIds : withId(tally.wrongIds, subject.id),
    wrongQuestions: result.correct
      ? tally.wrongQuestions
      : withWrongQuestion(tally.wrongQuestions, subject.id, question),
    completed: result.completed
      ? [
          ...tally.completed,
          { subject, stageBefore: result.srs_stage_before, stageAfter: result.srs_stage_after },
        ]
      : tally.completed,
  };
}

/** "I know this" -- never a rise, never a completed item, only `markedKnown`. */
export function recordKnown(tally: ReviewTally, subject: SubjectSummary): ReviewTally {
  return {
    ...tally,
    touchedIds: withId(tally.touchedIds, subject.id),
    knownIds: withId(tally.knownIds, subject.id),
  };
}

/** "Relearn from scratch" -- the item's open questions leave the session
 * without ever completing, same as `review.ts`'s own `shrinkOpen`. */
export function recordReset(tally: ReviewTally, subject: SubjectSummary): ReviewTally {
  return {
    ...tally,
    touchedIds: withId(tally.touchedIds, subject.id),
    resetIds: withId(tally.resetIds, subject.id),
  };
}

/** One item to practise (#96, "Review mistakes"): the prompt the practice
 *  round needs plus only the questions it actually owes this round -- see
 *  `summarize`'s doc comment on how `questions` is chosen. */
export interface PracticeItem {
  subject: SubjectSummary;
  questions: readonly QuestionType[];
}

export interface TallySummary {
  items: number; // completed.length
  correctItems: number; // completed with stageAfter > stageBefore
  /** Rises into a new stage group, per target group, subjects in completion order. */
  newStage: Readonly<Record<RiseGroup, readonly SubjectSummary[]>>;
  newStageCount: number;
  /** Same group, higher stage (e.g. Apprentice II -> III). */
  withinStageUp: number;
  /** Non-empty (from, to) group pairs over completed items, ordered by from then to along the ladder. */
  flow: readonly { from: StageGroup; to: StageGroup; count: number }[];
  byType: Readonly<Record<TallyType, { items: number; correct: number; newStage: number }>>;
  answers: Readonly<Record<QuestionType, { answered: number; correct: number }>>;
  markedKnown: number;
  reset: number;
  /** Touched, but neither completed, known nor reset -- only non-zero when the session was ended early. */
  open: number;
  /** For "Review mistakes" (#85): wrongIds union completed items that did
   * not rise (stageAfter <= stageBefore), unique, first-seen order.
   * Kept for whatever else might read it; `practiceItems` below is what
   * actually powers the "Review mistakes" button (#96) -- nothing else in
   * this app reads `mistakeIds` today. */
  mistakeIds: readonly number[];
  /** The practice round's items (#96): completed items that did not rise,
   *  excluding ones marked known or reset in this session (a deliberate
   *  action, not a mistake to drill), first-seen order. Each carries only
   *  the questions that were wrong *this session*; a completed item that
   *  did not rise but has no wrong answer recorded this session (its
   *  mistake came from an earlier, half-answered session -- this tally
   *  only ever covers one) practises every question its type owes, since
   *  which half was actually wrong is lost to that earlier session. */
  practiceItems: readonly PracticeItem[];
}

export function summarize(tally: ReviewTally): TallySummary {
  const newStage: Record<RiseGroup, SubjectSummary[]> = {
    guru: [],
    master: [],
    enlightened: [],
    burned: [],
  };
  const byType: Record<TallyType, { items: number; correct: number; newStage: number }> = {
    radical: { items: 0, correct: 0, newStage: 0 },
    kanji: { items: 0, correct: 0, newStage: 0 },
    vocabulary: { items: 0, correct: 0, newStage: 0 },
  };
  // One row per `from` group, counting every `to` group reached from it --
  // built as a map first since most (from, to) pairs never occur in a given
  // session, and `flow` only reports the ones that did.
  const flowCounts = new Map<StageGroup, Map<StageGroup, number>>();

  let correctItems = 0;
  let newStageCount = 0;
  let withinStageUp = 0;

  for (const item of tally.completed) {
    const from = groupOf(item.stageBefore);
    const to = groupOf(item.stageAfter);
    // A correct review always rises by exactly one stage; any mistake holds
    // or lowers it (srs.py's `stage_after_correct`/`stage_after_incorrect`),
    // so comparing stages alone is exactly "correct", Apprentice I included.
    const correct = item.stageAfter > item.stageBefore;
    const type = tallyType(item.subject.object_type);

    byType[type].items += 1;
    if (correct) {
      byType[type].correct += 1;
      correctItems += 1;
      if (ladderRank(to) > ladderRank(from)) {
        newStage[to as RiseGroup].push(item.subject);
        newStageCount += 1;
        byType[type].newStage += 1;
      } else {
        withinStageUp += 1;
      }
    }

    const row = flowCounts.get(from) ?? new Map<StageGroup, number>();
    row.set(to, (row.get(to) ?? 0) + 1);
    flowCounts.set(from, row);
  }

  const flow: { from: StageGroup; to: StageGroup; count: number }[] = [];
  for (const from of LADDER) {
    const row = flowCounts.get(from);
    if (!row) {
      continue;
    }
    for (const to of LADDER) {
      const count = row.get(to);
      if (count) {
        flow.push({ from, to, count });
      }
    }
  }

  const settled = new Set<number>([
    ...tally.completed.map((item) => item.subject.id),
    ...tally.knownIds,
    ...tally.resetIds,
  ]);
  const open = tally.touchedIds.filter((id) => !settled.has(id)).length;

  const mistakeIds: number[] = [];
  const seenMistakes = new Set<number>();
  for (const id of tally.wrongIds) {
    seenMistakes.add(id);
    mistakeIds.push(id);
  }
  for (const item of tally.completed) {
    if (item.stageAfter <= item.stageBefore && !seenMistakes.has(item.subject.id)) {
      seenMistakes.add(item.subject.id);
      mistakeIds.push(item.subject.id);
    }
  }

  // "Review mistakes" (#96): completed, did not rise, and not a deliberate
  // "I know this" / reset action elsewhere in the session -- those never
  // actually land in `completed` (see `recordKnown`/`recordReset`), so this
  // exclusion is defensive rather than reachable today, but it is exactly
  // what the issue asks for if that ever changes.
  const deliberatelySettled = new Set<number>([...tally.knownIds, ...tally.resetIds]);
  const practiceItems: PracticeItem[] = [];
  const seenPractice = new Set<number>();
  for (const item of tally.completed) {
    const id = item.subject.id;
    if (item.stageAfter > item.stageBefore || deliberatelySettled.has(id) || seenPractice.has(id)) {
      continue;
    }
    seenPractice.add(id);
    const wrong = tally.wrongQuestions[id];
    practiceItems.push({
      subject: item.subject,
      questions: wrong && wrong.length > 0 ? wrong : requiredQuestions(item.subject.object_type),
    });
  }

  return {
    items: tally.completed.length,
    correctItems,
    newStage,
    newStageCount,
    withinStageUp,
    flow,
    byType,
    answers: { ...tally.answers },
    markedKnown: tally.knownIds.length,
    reset: tally.resetIds.length,
    open,
    mistakeIds,
    practiceItems,
  };
}
