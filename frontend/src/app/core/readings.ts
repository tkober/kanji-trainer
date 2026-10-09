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
 *
 * Readings are kept as a list of entries rather than a joined string — see
 * `shared/readings/readings.ts`, which renders each one on its own so it can
 * carry its own hover/tap state. Dictionaries (jisho, WaniKani's own printed
 * material) write on'yomi in katakana and kun'yomi/nanori in hiragana even
 * though WaniKani stores everything in hiragana; `toDisplay` applies that
 * convention here, once, rather than in three page components. The backend
 * answer check (`answers.py`) already folds katakana to hiragana, so this is
 * display-only and never touches what counts as a correct answer.
 */

import { toKatakana } from 'wanakana';

import type { Reading } from './api.types';

/** One reading, ready to print — `display` is what the dictionary convention
 *  shows, `reading` is always the stored (hiragana) form underneath it. */
interface ReadingEntry {
  /** The reading as WaniKani stores it — always kana, always hiragana. */
  reading: string;
  /** What is actually shown: katakana for on'yomi, `reading` unchanged for
   *  everything else. */
  display: string;
  /** True when `display` differs from `reading` — on'yomi only — so the
   *  caller knows which readings get the hover/tap-to-reveal treatment. */
  converted: boolean;
}

export interface ReadingGroup {
  /** Identity for `@for` tracking — a label alone is not unique any more. */
  key: string;
  /** WaniKani's name for the type, or null when the readings carry none. */
  label: string | null;
  /** The raw type (`onyomi` / `kunyomi` / `nanori` / `''` for untyped) — which
   *  type, if any, the info-flyout content is picked by. */
  type: string;
  /** The readings themselves, ready to print. */
  readings: ReadingEntry[];
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
  const groups = new Map<string, { type: string; accepted: boolean; list: ReadingEntry[] }>();

  for (const reading of readings) {
    const type = reading.type ?? '';
    const accepted = reading.accepted_answer !== false;
    const key = `${type}:${accepted}`;
    const bucket = groups.get(key);
    const entry = toDisplay(type, reading.reading);
    if (bucket) {
      bucket.list.push(entry);
    } else {
      groups.set(key, { type, accepted, list: [entry] });
    }
  }

  return [...groups.values()]
    .sort((a, b) => rank(a.type) - rank(b.type) || Number(b.accepted) - Number(a.accepted))
    .map((group) => ({
      key: `${group.type}:${group.accepted}`,
      label: LABELS[group.type] ?? (group.type || null),
      type: group.type,
      readings: group.list,
      accepted: group.accepted,
    }));
}

/** On'yomi in katakana (the dictionary convention), everything else as stored. */
function toDisplay(type: string, reading: string): ReadingEntry {
  if (type !== 'onyomi') {
    return { reading, display: reading, converted: false };
  }
  const display = toKatakana(reading);
  return { reading, display, converted: display !== reading };
}

function rank(type: string): number {
  const index = ORDER.indexOf(type);
  return index === -1 ? ORDER.length : index;
}
