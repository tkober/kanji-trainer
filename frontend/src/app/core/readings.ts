/**
 * Readings grouped by WaniKani's own on'yomi / kun'yomi distinction.
 *
 * The importer keeps `type` verbatim (`subject_row` in backend/app/wanikani.py)
 * and it reaches the browser untouched — it was only the rendering that joined
 * everything into one flat string, which is exactly the distinction a learner
 * needs when a kanji has two readings and the reviews ask for one of them.
 *
 * Readings WaniKani does *not* accept as an answer are shown too, in their own
 * group. Two thirds of all kanji have one — 女 is taught by its on'yomi, so
 * おんな and め come back as `accepted_answer: false` — and dropping them meant
 * the app claimed the kanji had no kun'yomi at all. wanikani.com lists them,
 * merely dimmed, and the reviews already treat one as a *retry* rather than a
 * mistake; a learner who never sees them cannot make sense of that verdict.
 * The acceptance is part of the group, not of the single reading, so the
 * rendering can say plainly which readings are never the answer.
 *
 * Only kanji carry a type. Vocabulary readings have none and come back as a
 * single unlabelled group; radicals and kana vocabulary have no readings at
 * all, so the caller renders nothing.
 */

import type { Reading } from './api.types';

export interface ReadingGroup {
  /** Identity for `@for` tracking — a label alone is not unique any more. */
  key: string;
  /** WaniKani's name for the type, or null when the readings carry none. */
  label: string | null;
  /** The readings themselves, ready to print. */
  readings: string;
  /** False for readings WaniKani knows but never asks for on this item. */
  accepted: boolean;
}

/** The types WaniKani uses, in the order wanikani.com lists them. */
const LABELS: Record<string, string> = {
  onyomi: "On'yomi",
  kunyomi: "Kun'yomi",
  nanori: 'Nanori',
};

const ORDER = Object.keys(LABELS);

/**
 * Split readings into groups of one type and one acceptance, in `LABELS`
 * order, accepted first where a type has both.
 *
 * Untyped readings form the trailing group. A type WaniKani might add later
 * is labelled with its raw name rather than dropped or silently merged into
 * that group.
 */
export function readingGroups(readings: Reading[]): ReadingGroup[] {
  const groups = new Map<string, { type: string; accepted: boolean; list: string[] }>();

  for (const reading of readings) {
    const type = reading.type ?? '';
    const accepted = reading.accepted_answer !== false;
    const key = `${type}:${accepted}`;
    const bucket = groups.get(key);
    if (bucket) {
      bucket.list.push(reading.reading);
    } else {
      groups.set(key, { type, accepted, list: [reading.reading] });
    }
  }

  return [...groups.values()]
    .sort((a, b) => rank(a.type) - rank(b.type) || Number(b.accepted) - Number(a.accepted))
    .map((group) => ({
      key: `${group.type}:${group.accepted}`,
      label: LABELS[group.type] ?? (group.type || null),
      readings: group.list.join('、'),
      accepted: group.accepted,
    }));
}

function rank(type: string): number {
  const index = ORDER.indexOf(type);
  return index === -1 ? ORDER.length : index;
}
