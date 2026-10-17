import { describe, expect, it } from 'vitest';
import type { ObjectType } from './api.types';
import {
  buildPracticeQueue,
  EMPTY_PRACTICE_TALLY,
  recordPracticeAttempt,
  shakyPracticeItems,
  summarizePracticeRound,
  type PracticeSource,
  type PracticeTally,
} from './practice-queue';

function subject(id: number, object_type: ObjectType = 'kanji') {
  return {
    id,
    object_type,
    level: 1,
    slug: `subject-${id}`,
    characters: null,
    character_image_url: null,
  };
}

describe('buildPracticeQueue', () => {
  it('builds one entry per (item, question)', () => {
    const items: PracticeSource[] = [
      { subject: subject(1), questions: ['meaning', 'reading'] },
      { subject: subject(2, 'radical'), questions: ['meaning'] },
    ];
    const queue = buildPracticeQueue(items);

    expect(queue).toHaveLength(3);
    const keys = queue.map((e) => `${e.subject.id}:${e.question}`).sort();
    expect(keys).toEqual(['1:meaning', '1:reading', '2:meaning']);
  });

  it('contains exactly the given questions, never duplicated or dropped, regardless of shuffle', () => {
    const items: PracticeSource[] = [{ subject: subject(1), questions: ['meaning', 'reading'] }];
    for (let i = 0; i < 20; i++) {
      const queue = buildPracticeQueue(items);
      expect(queue.map((e) => e.question).sort()).toEqual(['meaning', 'reading']);
    }
  });

  it('returns an empty queue for no items', () => {
    expect(buildPracticeQueue([])).toEqual([]);
  });
});

describe('recordPracticeAttempt', () => {
  it('records the first attempt', () => {
    const tally = recordPracticeAttempt(EMPTY_PRACTICE_TALLY, 1, 'meaning', true);
    expect(tally.firstAttempts).toEqual({ '1:meaning': true });
  });

  it('does not overwrite an already-recorded first attempt', () => {
    let tally = recordPracticeAttempt(EMPTY_PRACTICE_TALLY, 1, 'meaning', false);
    tally = recordPracticeAttempt(tally, 1, 'meaning', true);
    expect(tally.firstAttempts).toEqual({ '1:meaning': false });
  });

  it('does not mutate the tally it was given', () => {
    const snapshot = structuredClone(EMPTY_PRACTICE_TALLY);
    recordPracticeAttempt(EMPTY_PRACTICE_TALLY, 1, 'meaning', true);
    expect(EMPTY_PRACTICE_TALLY).toEqual(snapshot);
  });

  it('tracks distinct (subject, question) pairs independently', () => {
    let tally = recordPracticeAttempt(EMPTY_PRACTICE_TALLY, 1, 'meaning', true);
    tally = recordPracticeAttempt(tally, 1, 'reading', false);
    tally = recordPracticeAttempt(tally, 2, 'meaning', true);
    expect(tally.firstAttempts).toEqual({
      '1:meaning': true,
      '1:reading': false,
      '2:meaning': true,
    });
  });
});

describe('summarizePracticeRound', () => {
  it('counts an item first-try only when every question it owes was first-try correct', () => {
    const items: PracticeSource[] = [{ subject: subject(1), questions: ['meaning', 'reading'] }];
    let tally: PracticeTally = EMPTY_PRACTICE_TALLY;
    tally = recordPracticeAttempt(tally, 1, 'meaning', true);
    tally = recordPracticeAttempt(tally, 1, 'reading', true);

    const summary = summarizePracticeRound(items, tally);
    expect(summary.items).toBe(1);
    expect(summary.firstTryItems).toBe(1);
    expect(summary.shaky).toEqual([]);
  });

  it('is shaky when one of two questions was wrong on the first try', () => {
    const s = subject(1);
    const items: PracticeSource[] = [{ subject: s, questions: ['meaning', 'reading'] }];
    let tally: PracticeTally = EMPTY_PRACTICE_TALLY;
    tally = recordPracticeAttempt(tally, 1, 'meaning', true);
    tally = recordPracticeAttempt(tally, 1, 'reading', false);
    // Eventually answered correctly on a later presentation -- first try
    // still stands as wrong, same reasoning as the review tally's wrongIds.
    tally = recordPracticeAttempt(tally, 1, 'reading', true); // no-op, already recorded

    const summary = summarizePracticeRound(items, tally);
    expect(summary.firstTryItems).toBe(0);
    expect(summary.shaky).toEqual([s]);
  });

  it('counts a question never attempted at all (an early End) as not first-try', () => {
    const s = subject(1);
    const items: PracticeSource[] = [{ subject: s, questions: ['meaning', 'reading'] }];
    const tally = recordPracticeAttempt(EMPTY_PRACTICE_TALLY, 1, 'meaning', true);
    // 'reading' was never asked before the round ended.

    const summary = summarizePracticeRound(items, tally);
    expect(summary.firstTryItems).toBe(0);
    expect(summary.shaky).toEqual([s]);
  });

  it('excludes skipped (409) items from both the item count and shaky', () => {
    const s1 = subject(1);
    const s2 = subject(2);
    const items: PracticeSource[] = [
      { subject: s1, questions: ['meaning'] },
      { subject: s2, questions: ['meaning'] },
    ];
    // Neither was ever answered -- without the skip, both would be shaky.
    const summary = summarizePracticeRound(items, EMPTY_PRACTICE_TALLY, new Set([2]));
    expect(summary.items).toBe(1);
    expect(summary.shaky).toEqual([s1]);
  });

  it('preserves first-seen order in shaky', () => {
    const a = subject(1);
    const b = subject(2);
    const items: PracticeSource[] = [
      { subject: a, questions: ['meaning'] },
      { subject: b, questions: ['meaning'] },
    ];
    const summary = summarizePracticeRound(items, EMPTY_PRACTICE_TALLY);
    expect(summary.shaky).toEqual([a, b]);
  });
});

describe('shakyPracticeItems', () => {
  it('returns only the questions that were not first-try correct', () => {
    const s = subject(1);
    const items: PracticeSource[] = [{ subject: s, questions: ['meaning', 'reading'] }];
    let tally: PracticeTally = EMPTY_PRACTICE_TALLY;
    tally = recordPracticeAttempt(tally, 1, 'meaning', true);
    tally = recordPracticeAttempt(tally, 1, 'reading', false);

    expect(shakyPracticeItems(items, tally)).toEqual([{ subject: s, questions: ['reading'] }]);
  });

  it('omits an item whose every question was first-try correct', () => {
    const s = subject(1);
    const items: PracticeSource[] = [{ subject: s, questions: ['meaning'] }];
    const tally = recordPracticeAttempt(EMPTY_PRACTICE_TALLY, 1, 'meaning', true);

    expect(shakyPracticeItems(items, tally)).toEqual([]);
  });

  it('excludes skipped items', () => {
    const s = subject(1);
    const items: PracticeSource[] = [{ subject: s, questions: ['meaning'] }];
    expect(shakyPracticeItems(items, EMPTY_PRACTICE_TALLY, new Set([1]))).toEqual([]);
  });
});
