import { describe, expect, it } from 'vitest';
import type { AnswerResult, ObjectType, SubjectSummary } from './api.types';
import {
  EMPTY_TALLY,
  recordAnswer,
  recordKnown,
  recordReset,
  type ReviewTally,
  stageGroup,
  summarize,
  tallyType,
} from './review-tally';

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

/** A settled, non-retry, non-held `AnswerResult` by default -- individual
 * tests override exactly the fields the scenario cares about. */
function answer(overrides: Partial<AnswerResult> = {}): AnswerResult {
  return {
    correct: true,
    expected: '',
    secondary: false,
    typo: false,
    hint: null,
    held: false,
    retry: false,
    completed: false,
    remaining: [],
    srs_stage_before: 1,
    srs_stage_after: 1,
    stage_name_after: '',
    next_review_at: null,
    subject: null,
    ...overrides,
  };
}

describe('stageGroup', () => {
  it('has no group for stage 0 (unlearned)', () => {
    expect(stageGroup(0)).toBeNull();
  });

  it('is apprentice across 1-4', () => {
    expect(stageGroup(1)).toBe('apprentice');
    expect(stageGroup(4)).toBe('apprentice');
  });

  it('is guru across 5-6', () => {
    expect(stageGroup(5)).toBe('guru');
    expect(stageGroup(6)).toBe('guru');
  });

  it('is master at 7', () => {
    expect(stageGroup(7)).toBe('master');
  });

  it('is enlightened at 8', () => {
    expect(stageGroup(8)).toBe('enlightened');
  });

  it('is burned at 9', () => {
    expect(stageGroup(9)).toBe('burned');
  });

  it('clamps anything below 0 to null and above 9 to burned', () => {
    expect(stageGroup(-1)).toBeNull();
    expect(stageGroup(42)).toBe('burned');
  });
});

describe('tallyType', () => {
  it('folds kana_vocabulary into vocabulary', () => {
    expect(tallyType('kana_vocabulary')).toBe('vocabulary');
  });

  it('passes every other type through unchanged', () => {
    expect(tallyType('radical')).toBe('radical');
    expect(tallyType('kanji')).toBe('kanji');
    expect(tallyType('vocabulary')).toBe('vocabulary');
  });
});

describe('recordAnswer', () => {
  it('ignores a held result entirely', () => {
    const tally = recordAnswer(EMPTY_TALLY, subject(1), 'meaning', answer({ held: true }));
    expect(tally).toEqual(EMPTY_TALLY);
  });

  it('ignores a retry result entirely', () => {
    const tally = recordAnswer(
      EMPTY_TALLY,
      subject(1),
      'reading',
      answer({ retry: true, correct: true }),
    );
    expect(tally).toEqual(EMPTY_TALLY);
  });

  it('a kanji with both halves correct rises Apprentice IV -> Guru I, counted under guru and kanji', () => {
    const s = subject(1, 'kanji');
    let tally = EMPTY_TALLY;
    // Meaning first -- the item is not done yet, so nothing has moved.
    tally = recordAnswer(
      tally,
      s,
      'meaning',
      answer({
        correct: true,
        completed: false,
        srs_stage_before: 4,
        srs_stage_after: 4,
        remaining: ['reading'],
      }),
    );
    // Reading settles it: one correct review always rises by one stage.
    tally = recordAnswer(
      tally,
      s,
      'reading',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 4,
        srs_stage_after: 5,
        remaining: [],
      }),
    );

    expect(tally.completed).toEqual([{ subject: s, stageBefore: 4, stageAfter: 5 }]);

    const summary = summarize(tally);
    expect(summary.items).toBe(1);
    expect(summary.correctItems).toBe(1);
    expect(summary.newStage.guru).toEqual([s]);
    expect(summary.newStageCount).toBe(1);
    expect(summary.withinStageUp).toBe(0);
    expect(summary.byType.kanji).toEqual({ items: 1, correct: 1, newStage: 1 });
    expect(summary.answers.meaning).toEqual({ answered: 1, correct: 1 });
    expect(summary.answers.reading).toEqual({ answered: 1, correct: 1 });
  });

  it('a kanji with a wrong reading then both correct does not rise, lands in mistakeIds and flow guru->apprentice', () => {
    const s = subject(2, 'kanji');
    let tally = EMPTY_TALLY;
    // Meaning correct, reading still outstanding.
    tally = recordAnswer(
      tally,
      s,
      'meaning',
      answer({
        correct: true,
        completed: false,
        srs_stage_before: 5,
        srs_stage_after: 5,
        remaining: ['reading'],
      }),
    );
    // Reading wrong -- stays outstanding, asked again, nothing moves yet.
    tally = recordAnswer(
      tally,
      s,
      'reading',
      answer({
        correct: false,
        completed: false,
        srs_stage_before: 5,
        srs_stage_after: 5,
        remaining: ['reading'],
      }),
    );
    // Reading correct the second time -- now everything is answered, and
    // the accumulated mistake (penalty doubled at Guru) throws it down to
    // Apprentice III.
    tally = recordAnswer(
      tally,
      s,
      'reading',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 5,
        srs_stage_after: 3,
        remaining: [],
      }),
    );

    expect(tally.wrongIds).toEqual([s.id]);
    expect(tally.answers.reading).toEqual({ answered: 2, correct: 1 });

    const summary = summarize(tally);
    expect(summary.correctItems).toBe(0);
    expect(summary.flow).toEqual([{ from: 'guru', to: 'apprentice', count: 1 }]);
    expect(summary.mistakeIds).toEqual([s.id]);
    expect(summary.answers.reading).toEqual({ answered: 2, correct: 1 });
  });

  it('Apprentice I with a mistake never drops below it, and is not correct', () => {
    const s = subject(3, 'radical');
    let tally = EMPTY_TALLY;
    // Wrong meaning -- a radical has only one question, so it stays
    // outstanding rather than completing on the spot.
    tally = recordAnswer(
      tally,
      s,
      'meaning',
      answer({
        correct: false,
        completed: false,
        srs_stage_before: 1,
        srs_stage_after: 1,
        remaining: ['meaning'],
      }),
    );
    // Answered right the second time: the accumulated mistake would demote
    // by one step, but Apprentice I is the floor.
    tally = recordAnswer(
      tally,
      s,
      'meaning',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 1,
        srs_stage_after: 1,
        remaining: [],
      }),
    );

    const summary = summarize(tally);
    expect(summary.items).toBe(1);
    expect(summary.correctItems).toBe(0);
    expect(summary.withinStageUp).toBe(0);
    expect(summary.newStageCount).toBe(0);
    expect(summary.mistakeIds).toEqual([s.id]);
  });

  it('counts a within-stage rise (Apprentice II -> III) without a group change', () => {
    const s = subject(4, 'radical');
    const tally = recordAnswer(
      EMPTY_TALLY,
      s,
      'meaning',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 2,
        srs_stage_after: 3,
        remaining: [],
      }),
    );

    const summary = summarize(tally);
    expect(summary.correctItems).toBe(1);
    expect(summary.withinStageUp).toBe(1);
    expect(summary.newStageCount).toBe(0);
    expect(summary.flow).toEqual([{ from: 'apprentice', to: 'apprentice', count: 1 }]);
  });

  it('counts a rise from Enlightened to Burned', () => {
    const s = subject(5, 'vocabulary');
    const tally = recordAnswer(
      EMPTY_TALLY,
      s,
      'meaning',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 8,
        srs_stage_after: 9,
        remaining: [],
      }),
    );

    const summary = summarize(tally);
    expect(summary.newStage.burned).toEqual([s]);
    expect(summary.newStageCount).toBe(1);
  });

  it('folds a kana_vocabulary subject into the vocabulary byType bucket', () => {
    const s = subject(6, 'kana_vocabulary');
    const tally = recordAnswer(
      EMPTY_TALLY,
      s,
      'meaning',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 1,
        srs_stage_after: 2,
        remaining: [],
      }),
    );

    const summary = summarize(tally);
    expect(summary.byType.vocabulary.items).toBe(1);
    expect(summary.byType.vocabulary.correct).toBe(1);
  });

  it('does not mutate the tally it was given', () => {
    const snapshot = structuredClone(EMPTY_TALLY);
    recordAnswer(
      EMPTY_TALLY,
      subject(7),
      'meaning',
      answer({ correct: true, completed: true, srs_stage_before: 1, srs_stage_after: 2 }),
    );
    expect(EMPTY_TALLY).toEqual(snapshot);
  });
});

describe('recordKnown', () => {
  it('counts "I know this" after one answered half as markedKnown, not completed, and leaves nothing open', () => {
    const s = subject(10, 'kanji');
    let tally = EMPTY_TALLY;
    // Meaning answered first; the item is still mid-review when it is
    // declared known.
    tally = recordAnswer(
      tally,
      s,
      'meaning',
      answer({
        correct: true,
        completed: false,
        srs_stage_before: 3,
        srs_stage_after: 3,
        remaining: ['reading'],
      }),
    );
    tally = recordKnown(tally, s);

    const summary = summarize(tally);
    expect(summary.items).toBe(0);
    expect(summary.markedKnown).toBe(1);
    expect(summary.open).toBe(0);
  });

  it('does not mutate the tally it was given', () => {
    const snapshot = structuredClone(EMPTY_TALLY);
    recordKnown(EMPTY_TALLY, subject(11));
    expect(EMPTY_TALLY).toEqual(snapshot);
  });
});

describe('recordReset', () => {
  it('counts a reset separately, and leaves nothing open', () => {
    const s = subject(12, 'kanji');
    let tally = EMPTY_TALLY;
    tally = recordAnswer(
      tally,
      s,
      'meaning',
      answer({
        correct: true,
        completed: false,
        srs_stage_before: 3,
        srs_stage_after: 3,
        remaining: ['reading'],
      }),
    );
    tally = recordReset(tally, s);

    const summary = summarize(tally);
    expect(summary.items).toBe(0);
    expect(summary.reset).toBe(1);
    expect(summary.markedKnown).toBe(0);
    expect(summary.open).toBe(0);
  });

  it('does not mutate the tally it was given', () => {
    const snapshot = structuredClone(EMPTY_TALLY);
    recordReset(EMPTY_TALLY, subject(13));
    expect(EMPTY_TALLY).toEqual(snapshot);
  });
});

describe('summarize: open items', () => {
  it('counts a half-answered item left over by an early end as open', () => {
    const s = subject(20, 'kanji');
    const tally = recordAnswer(
      EMPTY_TALLY,
      s,
      'meaning',
      answer({
        correct: true,
        completed: false,
        srs_stage_before: 3,
        srs_stage_after: 3,
        remaining: ['reading'],
      }),
    );

    const summary = summarize(tally);
    expect(summary.open).toBe(1);
  });
});

describe('summarize: flow ordering', () => {
  it('orders flow entries by from then to along the ladder, regardless of completion order', () => {
    const a = subject(30, 'kanji'); // completes first: guru -> master
    const b = subject(31, 'radical'); // completes second: apprentice -> guru
    let tally: ReviewTally = EMPTY_TALLY;
    tally = recordAnswer(
      tally,
      a,
      'meaning',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 6,
        srs_stage_after: 7,
        remaining: [],
      }),
    );
    tally = recordAnswer(
      tally,
      b,
      'meaning',
      answer({
        correct: true,
        completed: true,
        srs_stage_before: 4,
        srs_stage_after: 5,
        remaining: [],
      }),
    );

    const summary = summarize(tally);
    expect(summary.flow).toEqual([
      { from: 'apprentice', to: 'guru', count: 1 },
      { from: 'guru', to: 'master', count: 1 },
    ]);
  });
});
