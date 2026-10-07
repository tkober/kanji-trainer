import { Component, computed, input, signal } from '@angular/core';

import type { ObjectType, Reading } from '../../core/api.types';
import { HoldFocus } from '../../core/hold-focus';
import { type ReadingGroup, readingGroups } from '../../core/readings';

/** A flyout's worth of explanation for one reading type, plus the link every
 *  flyout ends with. English, short — this is a reminder, not the full
 *  explanation, which lives on `/readings`. */
const INFO: Record<string, string> = {
  onyomi:
    "The Chinese-derived “sound” reading. Mostly used when the kanji appears inside a " +
    'compound word of two or more kanji (jukugo) — e.g. 月曜日 げつようび. ' +
    'Dictionaries write it in katakana; tap or hover a reading below to see it in hiragana.',
  kunyomi:
    'The native Japanese reading. Mostly used when the kanji stands alone or carries okurigana ' +
    '(kana endings) — e.g. 月 つき, 出る でる. Written in hiragana; ' +
    'dictionaries often mark with a dot where the okurigana begins.',
  nanori: 'Used almost only in personal and place names — a reading you will rarely need anywhere else.',
};

const COMMON_INFO =
  'Reviews ask for the reading taught first; the others are shown dimmed as “not asked in ' +
  'reviews”. Answering with one of those is not counted as a mistake — you are simply asked again.';

let nextInstanceId = 0;

/**
 * One item's readings, grouped and rendered — shared by the review "Show
 * item" panel, the lessons reading phase and the browse detail dialog, which
 * used to carry three copies of the same `@for (group of readings(...))`
 * block built on `core/readings.ts:readingGroups()`.
 *
 * Renders its own `<dl>` — a page embeds it where it used to have the
 * readings portion of a larger `<dl>`, split out from the meanings/mnemonics
 * around it, which keeps every `<dt>`/`<dd>` pair inside a `<dl>` whose
 * content model it actually satisfies (a flyout's `div[popover]` is only
 * valid as a *child of* `<dt>`, not as a sibling of `<dt>`/`<dd>` inside the
 * wrapping `<div>` the old single-`<dl>` layout would have needed).
 *
 * Bold is reserved for a *kanji*'s accepted group(s) — vocabulary has exactly
 * one, untyped, group and bolding it would just be noise.
 */
@Component({
  selector: 'app-readings',
  imports: [HoldFocus],
  templateUrl: './readings.html',
  styleUrl: './readings.scss',
})
export class Readings {
  readonly readings = input.required<Reading[]>();
  readonly objectType = input.required<ObjectType>();

  private readonly instanceId = `readings-${++nextInstanceId}`;

  protected readonly groups = computed<ReadingGroup[]>(() => readingGroups(this.readings()));

  protected bold(group: ReadingGroup): boolean {
    return this.objectType() === 'kanji' && group.accepted;
  }

  protected info(type: string): string {
    return INFO[type] ?? '';
  }

  protected readonly commonInfo = COMMON_INFO;

  protected popoverId(type: string): string {
    return `${this.instanceId}-info-${type}`;
  }

  /** Which converted (on'yomi) readings currently show their hiragana because
   *  of a tap — hover is plain CSS (`@media (hover: hover)`) and needs none
   *  of this; a phone has no hover, so a tap has to leave state behind. Keyed
   *  by group + index rather than the reading itself, in case two groups ever
   *  share one spelling. */
  private readonly revealed = signal<ReadonlySet<string>>(new Set());

  protected isRevealed(group: ReadingGroup, index: number): boolean {
    return this.revealed().has(`${group.key}:${index}`);
  }

  protected toggleReveal(group: ReadingGroup, index: number): void {
    const key = `${group.key}:${index}`;
    this.revealed.update((set) => {
      const next = new Set(set);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }
}
