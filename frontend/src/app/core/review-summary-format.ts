import type { QuestionType } from './api.types';
import type { RiseGroup, TallySummary, TallyType } from './review-tally';

/** Fixed display order and label for the four rise targets (#83) --
 *  `RiseGroup` itself has no "Apprentice" member (see its doc comment), and
 *  this is the ladder order every card grid renders in, zero-count cards
 *  included. */
const STAGE_CARD_ORDER: readonly { key: RiseGroup; label: string }[] = [
  { key: 'guru', label: 'Guru' },
  { key: 'master', label: 'Master' },
  { key: 'enlightened', label: 'Enlightened' },
  { key: 'burned', label: 'Burned' },
];

/** First 6 glyph chips shown per card, the rest folded into a "+n" chip --
 *  see the issue brief's mockup (desktop: 6). */
const MAX_CHIPS = 6;

export interface StageCard {
  key: RiseGroup;
  label: string;
  count: number;
  visible: TallySummary['newStage'][RiseGroup];
  overflow: number;
}

/** One card per rise target, always in ladder order and always present --
 *  a target nothing rose into this session still gets a card showing 0 (the
 *  template is what skips the whole section when nothing happened at all). */
export function stageCards(newStage: TallySummary['newStage']): readonly StageCard[] {
  return STAGE_CARD_ORDER.map(({ key, label }) => {
    const subjects = newStage[key];
    return {
      key,
      label,
      count: subjects.length,
      visible: subjects.slice(0, MAX_CHIPS),
      overflow: Math.max(0, subjects.length - MAX_CHIPS),
    };
  });
}

/** "moved up within its/their stage" -- grammatical number only, the exact
 *  stage pair in the sentence (e.g. "Apprentice II → III") is illustrative
 *  text, not derived from `summary`: a within-stage rise is tracked only as
 *  a count (`TallySummary.withinStageUp`), with no record of which stage
 *  numbers it actually moved between. */
export function withinStagePossessive(count: number): 'its' | 'their' {
  return count === 1 ? 'its' : 'their';
}

const TYPE_ROW_ORDER: readonly { key: TallyType; label: string }[] = [
  { key: 'radical', label: 'Radicals' },
  { key: 'kanji', label: 'Kanji' },
  { key: 'vocabulary', label: 'Vocabulary' },
];

export interface ByTypeRow {
  key: TallyType;
  label: string;
  items: number;
  correctPercent: number;
  newStage: number;
}

/** "By type" table rows, fixed radical/kanji/vocabulary order, types with no
 *  items this session dropped entirely (a radicals-only session shows no
 *  Kanji/Vocabulary row). */
export function byTypeRows(byType: TallySummary['byType']): readonly ByTypeRow[] {
  return TYPE_ROW_ORDER.map(({ key, label }) => {
    const row = byType[key];
    return {
      key,
      label,
      items: row.items,
      correctPercent: row.items > 0 ? Math.round((row.correct / row.items) * 100) : 0,
      newStage: row.newStage,
    };
  }).filter((row) => row.items > 0);
}

const ANSWER_ROW_ORDER: readonly { key: QuestionType; label: string }[] = [
  { key: 'meaning', label: 'Meaning' },
  { key: 'reading', label: 'Reading' },
];

export interface AnswerRow {
  key: QuestionType;
  label: string;
  answered: number;
  correct: number;
}

/** "Answers" rows, Meaning then Reading, dropped when nothing of that
 *  question type was answered (a radicals-only session has no Reading row --
 *  radicals have no reading question at all). */
export function answerRows(answers: TallySummary['answers']): readonly AnswerRow[] {
  return ANSWER_ROW_ORDER.map(({ key, label }) => ({
    key,
    label,
    answered: answers[key].answered,
    correct: answers[key].correct,
  })).filter((row) => row.answered > 0);
}

function pluralize(n: number, singular: string): string {
  return n === 1 ? singular : `${singular}s`;
}

/**
 * "Also this session: …" -- the non-zero parts of `markedKnown`/`reset`/
 * `open`, joined with " · ". `null` when every part is 0, which the
 * template reads as "hide the whole line".
 */
export function footerLine(markedKnown: number, reset: number, open: number): string | null {
  const parts: string[] = [];
  if (markedKnown > 0) {
    parts.push(`${markedKnown} ${pluralize(markedKnown, 'item')} marked as known`);
  }
  if (reset > 0) {
    parts.push(`${reset} reset to Apprentice`);
  }
  if (open > 0) {
    parts.push(`${open} still open — they stay due`);
  }
  return parts.length > 0 ? `Also this session: ${parts.join(' · ')}` : null;
}
