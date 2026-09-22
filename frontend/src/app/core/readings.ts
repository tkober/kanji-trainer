/**
 * Readings grouped by WaniKani's own on'yomi / kun'yomi distinction.
 *
 * The importer keeps `type` verbatim (`subject_row` in backend/app/wanikani.py)
 * and it reaches the browser untouched — it was only the rendering that joined
 * everything into one flat string, which is exactly the distinction a learner
 * needs when a kanji has two readings and the reviews ask for one of them.
 *
 * Only kanji carry a type. Vocabulary readings have none and come back as a
 * single unlabelled group; radicals and kana vocabulary have no readings at
 * all, so the caller renders nothing.
 */

import type { Reading } from './api.types';

export interface ReadingGroup {
  /** WaniKani's name for the type, or null when the readings carry none. */
  label: string | null;
  /** The readings themselves, ready to print. */
  readings: string;
}

/** The types WaniKani uses, in the order wanikani.com lists them. */
const LABELS: Record<string, string> = {
  onyomi: "On'yomi",
  kunyomi: "Kun'yomi",
  nanori: 'Nanori',
};

const ORDER = Object.keys(LABELS);

/**
 * Split accepted readings into groups, in `LABELS` order.
 *
 * Untyped readings form the trailing group. A type WaniKani might add later
 * is labelled with its raw name rather than dropped or silently merged into
 * that group.
 */
export function readingGroups(readings: Reading[]): ReadingGroup[] {
  const groups = new Map<string, string[]>();

  for (const reading of readings) {
    if (reading.accepted_answer === false) {
      continue;
    }
    const type = reading.type ?? '';
    const bucket = groups.get(type);
    if (bucket) {
      bucket.push(reading.reading);
    } else {
      groups.set(type, [reading.reading]);
    }
  }

  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([type, list]) => ({
      label: LABELS[type] ?? (type || null),
      readings: list.join('、'),
    }));
}

function rank(type: string): number {
  const index = ORDER.indexOf(type);
  return index === -1 ? ORDER.length : index;
}
