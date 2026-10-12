import { Component, computed, input, output } from '@angular/core';

import type { LessonTile } from '../../core/api.types';

/** Ordinal SRS-stage grouping a tile is coloured by (lesson < apprentice <
 *  guru < master < enlightened < burned) -- see `stageBand`. */
export type Band = 'lesson' | 'apprentice' | 'guru' | 'master' | 'enlightened' | 'burned';

export function stageBand(stage: number): Band {
  if (stage <= 0) return 'lesson';
  if (stage <= 4) return 'apprentice';
  if (stage <= 6) return 'guru';
  if (stage === 7) return 'master';
  if (stage === 8) return 'enlightened';
  return 'burned';
}

/** WaniKani's stage names, with stage 0 relabelled: on a tile it names a
 * lesson waiting to be picked, never "Unlearned" -- there is nothing to have
 * unlearned yet. See `srs.STAGE_NAMES` in the backend for the reviewed item's
 * own wording, which this deliberately does not reuse. */
const STAGE_LABELS = [
  'Lesson',
  'Apprentice I',
  'Apprentice II',
  'Apprentice III',
  'Apprentice IV',
  'Guru I',
  'Guru II',
  'Master',
  'Enlightened',
  'Burned',
];

export function stageName(stage: number): string {
  return STAGE_LABELS[Math.max(0, Math.min(9, stage))];
}

export type ItemTileSize = 'md' | 'sm';

/**
 * One radical/kanji/vocabulary tile, coloured by SRS-stage band (issue #59 --
 * extracted from `pages/lessons`, the one place a tile used to be drawn, so
 * every screen that shows one keeps the exact same look and `band()`
 * colouring instead of two copies drifting apart).
 *
 * `selectable` renders a `<button>` -- the lessons picker's own behaviour,
 * unchanged: toggling selection, a selected outline, a disabled state once
 * the daily cap is reached. Anything else renders a plain, non-interactive
 * `<span>` -- a read-only display like the dashboard's level card, which
 * also uses it for an *open* lesson (`state === 'new'`), a combination the
 * picker itself never produces (there, a `new` tile is always its own
 * selectable button) -- shown as a hollow, dashed box rather than a filled
 * one, since nothing has been earned there yet.
 *
 * `size` (issue #59): `'md'`, the default, is the picker's own
 * tile unchanged. `'sm'` is a whole level's worth of kanji shown at once on
 * the dashboard -- a much smaller box, a glyph sized to still read at that
 * box (not just scaled down with it), and no checkmark badge, which at that size
 * would cover more of the glyph than it annotates.
 */
@Component({
  selector: 'app-item-tile',
  imports: [],
  templateUrl: './item-tile.html',
  styleUrl: './item-tile.scss',
})
export class ItemTile {
  readonly tile = input.required<LessonTile>();
  readonly selectable = input(false);
  readonly selected = input(false);
  readonly disabled = input(false);
  readonly size = input<ItemTileSize>('md');
  readonly toggle = output<void>();

  protected readonly band = computed(() => stageBand(this.tile().srs_stage));

  protected readonly label = computed(() => {
    const tile = this.tile();
    const glyph = tile.subject.characters ?? tile.subject.slug;
    let label = `${glyph} — ${stageName(tile.srs_stage)}`;
    if (tile.state === 'known') {
      label += ' (marked known)';
    } else if (tile.state === 'suspended') {
      label += ' (suspended)';
    }
    return label;
  });
}
