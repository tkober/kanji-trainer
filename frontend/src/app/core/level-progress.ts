/**
 * Pure helpers for aggregating `LevelTypeCounts`/`StageCounts` into the
 * numbers the dashboard and the statistics page both show. Extracted out of
 * `pages/dashboard/dashboard.ts` (issue #60) so the two pages count exactly
 * the same way — radical/kanji/vocabulary grouping, what counts as
 * "settled", what a level's own total is — rather than keeping two copies
 * that could quietly drift apart.
 *
 * No Angular here on purpose: these are plain functions over the API's own
 * types, easy to reuse from a `computed()` on either page.
 */

import type { LevelTypeCounts, StageCounts } from './api.types';

/** One (combined) type's stage counts for a scope -- a single level (the
 *  dashboard's level card) or a whole frame of levels (the dashboard's
 *  progress card, the statistics page's "By type" card). Vocabulary and
 *  kana_vocabulary are combined, same convention `pages/lessons`' own
 *  `groups()` and the backend's `LevelSummary.open_vocabulary` both use. */
export interface TypeRow {
  key: 'radical' | 'kanji' | 'vocabulary';
  label: string;
  counts: StageCounts;
}

export function emptyStageCounts(): StageCounts {
  return {
    new: 0,
    apprentice_1: 0,
    apprentice_2: 0,
    apprentice_3: 0,
    apprentice_4: 0,
    guru_1: 0,
    guru_2: 0,
    master: 0,
    enlightened: 0,
    burned: 0,
    known: 0,
    suspended: 0,
  };
}

export function addStageCounts(a: StageCounts, b: StageCounts): StageCounts {
  return {
    new: a.new + b.new,
    apprentice_1: a.apprentice_1 + b.apprentice_1,
    apprentice_2: a.apprentice_2 + b.apprentice_2,
    apprentice_3: a.apprentice_3 + b.apprentice_3,
    apprentice_4: a.apprentice_4 + b.apprentice_4,
    guru_1: a.guru_1 + b.guru_1,
    guru_2: a.guru_2 + b.guru_2,
    master: a.master + b.master,
    enlightened: a.enlightened + b.enlightened,
    burned: a.burned + b.burned,
    known: a.known + b.known,
    suspended: a.suspended + b.suspended,
  };
}

/** Earned by review, Guru or past it -- "Marked as known" is its own count,
 *  kept apart (see CLAUDE.md, the `state`/`srs_stage` invariant: stage 9 is
 *  reachable two ways, and folding them back together here would undo the
 *  whole point of keeping them separate). */
export function guruPlusEarned(c: StageCounts): number {
  return c.guru_1 + c.guru_2 + c.master + c.enlightened + c.burned;
}

export function apprenticeCount(c: StageCounts): number {
  return c.apprentice_1 + c.apprentice_2 + c.apprentice_3 + c.apprentice_4;
}

/** Guru+ earned or marked as known -- out of the rotation for good. */
export function settledCount(c: StageCounts): number {
  return guruPlusEarned(c) + c.known;
}

/** Guru+ earned, and "marked as known" only when `countKnown` is true -- the
 *  statistics page's toggle ("Count marked-as-known as settled"). `known` is
 *  always its own segment wherever it is *shown*; this only changes what
 *  counts toward a "settled"/percentage figure. */
export function settledForToggle(c: StageCounts, countKnown: boolean): number {
  return guruPlusEarned(c) + (countKnown ? c.known : 0);
}

/** The reference frame this dashboard counts against: everything started or
 *  open, Guru+ earned, known, in apprentice, or still a lesson. Suspended
 *  (hidden) items are deliberately left out -- they were set aside on
 *  purpose and neither a level's nor the overall progress should count them
 *  one way or the other. */
export function frameTotal(c: StageCounts): number {
  return guruPlusEarned(c) + c.known + apprenticeCount(c) + c.new;
}

/** A level is complete once every non-suspended item in it has been
 *  started -- `new === 0` -- but a level with no items at all (not in the
 *  collection yet) does not count as "complete" just because it has
 *  nothing left to start. */
export function levelComplete(c: StageCounts): boolean {
  return frameTotal(c) > 0 && c.new === 0;
}

export function typeRows(entries: readonly LevelTypeCounts[]): TypeRow[] {
  let radical = emptyStageCounts();
  let kanji = emptyStageCounts();
  let vocabulary = emptyStageCounts();
  for (const entry of entries) {
    if (entry.object_type === 'radical') {
      radical = addStageCounts(radical, entry.counts);
    } else if (entry.object_type === 'kanji') {
      kanji = addStageCounts(kanji, entry.counts);
    } else {
      // vocabulary + kana_vocabulary combined.
      vocabulary = addStageCounts(vocabulary, entry.counts);
    }
  }
  return [
    { key: 'radical', label: 'Radicals', counts: radical },
    { key: 'kanji', label: 'Kanji', counts: kanji },
    { key: 'vocabulary', label: 'Vocabulary', counts: vocabulary },
  ];
}

export function combinedCounts(rows: readonly TypeRow[]): StageCounts {
  return rows.reduce((acc, row) => addStageCounts(acc, row.counts), emptyStageCounts());
}
