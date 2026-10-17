import { Component, ElementRef, effect, input, output, viewChild, viewChildren } from '@angular/core';

/**
 * One tile's worth of presentation data, computed by `lessons.ts` from the
 * current batch's `LessonItem[]` -- kept as plain data rather than handing
 * this component `toneFor`/`typeLabel` functions, so it stays decoupled from
 * `api.types` and the page's own helpers (issue #87).
 */
export interface BatchTileData {
  readonly id: number;
  readonly characters: string | null;
  readonly imageUrl: string | null;
  /** Alt text for the image case -- the subject's slug, same as `app-item-tile`. */
  readonly alt: string;
  /** The type stripe's colour, `toneFor()`'s own `var(--radical|kanji|vocabulary)`. */
  readonly tone: string;
  /** Vocabulary sizes to the word instead of a uniform square. */
  readonly isWord: boolean;
  readonly ariaLabel: string;
}

/**
 * The batch's item tiles (issue #87, E3): one button per item, replacing
 * "i / N" with something clickable. Deliberately not `app-item-tile` --
 * that component encodes SRS-stage colours, which have no meaning for an
 * item still being read for the first time.
 *
 * Rendered twice by `lessons.html` (once above the item card below 900px,
 * once inside the sticky action bar at 900px+) and toggled with
 * `display: none` by a container query in `lessons.scss`, rather than one
 * copy moved by JS -- see that file's own comment.
 */
/** Scrolls only the strip itself, never the page: `scrollIntoView` would
 *  also scroll the window, and on a phone that yanked the page up to the
 *  strip above the item card on every Next. A hidden copy of the strip
 *  (`display: none`, see lessons.scss) has no width and is left alone. */
function keepInView(strip: HTMLElement, tile: HTMLElement): void {
  const left = tile.offsetLeft;
  const right = left + tile.offsetWidth;
  if (left < strip.scrollLeft) {
    strip.scrollLeft = left;
  } else if (right > strip.scrollLeft + strip.clientWidth) {
    strip.scrollLeft = right - strip.clientWidth;
  }
}

@Component({
  selector: 'app-batch-tiles',
  templateUrl: './batch-tiles.html',
  styleUrl: './batch-tiles.scss',
})
export class BatchTiles {
  readonly tiles = input.required<BatchTileData[]>();
  /** Index of the item currently on screen. */
  readonly current = input.required<number>();
  /** Subject ids visited this batch -- by id, not index, since "I know this
   *  one" removes an item and would leave an index-based set pointing at
   *  the wrong neighbour (see lessons.ts). */
  readonly visited = input.required<ReadonlySet<number>>();
  readonly jump = output<number>();

  private readonly tileEls = viewChildren<ElementRef<HTMLButtonElement>>('tileEl');
  private readonly strip = viewChild<ElementRef<HTMLElement>>('strip');

  constructor() {
    // Keep the current tile in view as `index` changes -- a batch can run to
    // 100 items, scrolled horizontally inside its own strip.
    effect(() => {
      const tile = this.tileEls()[this.current()]?.nativeElement;
      const strip = this.strip()?.nativeElement;
      if (tile && strip) {
        keepInView(strip, tile);
      }
    });
  }

  protected onClick(i: number): void {
    this.jump.emit(i);
  }
}
