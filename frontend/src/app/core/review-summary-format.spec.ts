import { describe, expect, it } from 'vitest';
import type { ObjectType, SubjectSummary } from './api.types';
import {
  answerRows,
  byTypeRows,
  footerLine,
  stageCards,
  withinStagePossessive,
} from './review-summary-format';
import { EMPTY_TALLY, recordAnswer, summarize } from './review-tally';

function subject(id: number, object_type: ObjectType = 'kanji'): SubjectSummary {
  return {
    id,
    object_type,
    level: 1,
    slug: `subject-${id}`,
    characters: null,
    character_image_url: null,
  };
}

describe('stageCards', () => {
  it('returns all four rise targets in ladder order, even when every count is 0', () => {
    const cards = stageCards(summarize(EMPTY_TALLY).newStage);
    expect(cards.map((c) => c.key)).toEqual(['guru', 'master', 'enlightened', 'burned']);
    expect(cards.every((c) => c.count === 0 && c.visible.length === 0 && c.overflow === 0)).toBe(
      true,
    );
  });

  it('caps visible chips at 6 and folds the rest into overflow', () => {
    const subjects = Array.from({ length: 9 }, (_, i) => subject(i));
    const cards = stageCards({ guru: subjects, master: [], enlightened: [], burned: [] });
    const guru = cards.find((c) => c.key === 'guru')!;
    expect(guru.count).toBe(9);
    expect(guru.visible).toHaveLength(6);
    expect(guru.overflow).toBe(3);
  });
});

describe('withinStagePossessive', () => {
  it('is singular for exactly 1', () => {
    expect(withinStagePossessive(1)).toBe('its');
  });

  it('is plural for 0 or more than 1', () => {
    expect(withinStagePossessive(0)).toBe('their');
    expect(withinStagePossessive(2)).toBe('their');
  });
});

describe('byTypeRows', () => {
  it('drops types with no items this session, keeping radical/kanji/vocabulary order', () => {
    let tally = EMPTY_TALLY;
    tally = recordAnswer(tally, subject(1, 'kanji'), 'meaning', {
      correct: true,
      expected: '',
      secondary: false,
      typo: false,
      hint: null,
      held: false,
      retry: false,
      completed: true,
      remaining: [],
      srs_stage_before: 4,
      srs_stage_after: 5,
      stage_name_after: '',
      next_review_at: null,
      subject: null,
    });
    tally = recordAnswer(tally, subject(2, 'vocabulary'), 'meaning', {
      correct: false,
      expected: '',
      secondary: false,
      typo: false,
      hint: null,
      held: false,
      retry: false,
      completed: true,
      remaining: [],
      srs_stage_before: 2,
      srs_stage_after: 2,
      stage_name_after: '',
      next_review_at: null,
      subject: null,
    });

    const rows = byTypeRows(summarize(tally).byType);
    expect(rows.map((r) => r.key)).toEqual(['kanji', 'vocabulary']);
    expect(rows[0]).toMatchObject({ items: 1, correctPercent: 100, newStage: 1 });
    expect(rows[1]).toMatchObject({ items: 1, correctPercent: 0, newStage: 0 });
  });

  it('is empty when nothing completed', () => {
    expect(byTypeRows(summarize(EMPTY_TALLY).byType)).toEqual([]);
  });
});

describe('answerRows', () => {
  it('drops a question type with nothing answered -- e.g. a radicals-only session has no Reading row', () => {
    const rows = answerRows({
      meaning: { answered: 10, correct: 9 },
      reading: { answered: 0, correct: 0 },
    });
    expect(rows).toEqual([{ key: 'meaning', label: 'Meaning', answered: 10, correct: 9 }]);
  });

  it('is empty when nothing was answered at all', () => {
    expect(answerRows(summarize(EMPTY_TALLY).answers)).toEqual([]);
  });
});

describe('footerLine', () => {
  it('is null when every part is 0', () => {
    expect(footerLine(0, 0, 0)).toBeNull();
  });

  it('singularises exactly one marked-known item', () => {
    expect(footerLine(1, 0, 0)).toBe('Also this session: 1 item marked as known');
  });

  it('joins every non-zero part with middle dots, in markedKnown/reset/open order', () => {
    expect(footerLine(2, 1, 3)).toBe(
      'Also this session: 2 items marked as known · 1 reset to Apprentice · 3 still open — they stay due',
    );
  });

  it('skips a zero part without leaving a stray separator', () => {
    expect(footerLine(0, 1, 0)).toBe('Also this session: 1 reset to Apprentice');
  });
});
