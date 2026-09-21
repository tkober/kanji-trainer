import { Component, computed, inject, signal } from '@angular/core';

import { Api } from '../../core/api';
import type { Forecast, ForecastBucket } from '../../core/api.types';

/** One column of the chart: a time slot and what arrives in it. */
interface Slot {
  at: Date;
  label: string;
  apprentice: number;
  guru: number;
  master: number;
  count: number;
  cumulative: number;
}

/** A drawn segment of a stacked column. */
interface Segment {
  band: 'apprentice' | 'guru' | 'master';
  x: number;
  y: number;
  width: number;
  height: number;
  /** Set on the topmost segment, which carries the rounded data-end. */
  path: string | null;
}

interface Column {
  slot: Slot;
  segments: Segment[];
  /** Full-height hover target, wider than the mark itself. */
  hitX: number;
  hitWidth: number;
}

const RANGES = [
  { hours: 24, label: '24 hours', bucket: 1 },
  { hours: 48, label: '48 hours', bucket: 3 },
  { hours: 168, label: '7 days', bucket: 24 },
] as const;

const PLOT = { width: 960, height: 220, left: 44, right: 12, top: 12, bottom: 28 };
const LINE = { width: 960, height: 120, left: 44, right: 12, top: 12, bottom: 28 };
/** Mark specs: bars capped so the band keeps air, 2px of surface between marks. */
const MAX_BAR = 24;
const GAP = 2;
const RADIUS = 4;

@Component({
  selector: 'app-forecast',
  templateUrl: './forecast.html',
  styleUrl: './forecast.scss',
})
export class ForecastPage {
  private readonly api = inject(Api);

  protected readonly ranges = RANGES;
  protected readonly range = signal<(typeof RANGES)[number]>(RANGES[0]);
  protected readonly data = signal<Forecast | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly hovered = signal<Slot | null>(null);
  protected readonly showTable = signal(false);

  protected readonly plot = PLOT;
  protected readonly linePlot = LINE;

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      this.data.set(await this.api.forecast(this.range().hours));
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  pick(range: (typeof RANGES)[number]): void {
    this.range.set(range);
    this.hovered.set(null);
    void this.load();
  }

  /**
   * Hourly buckets folded to the range's resolution.
   *
   * 168 hourly columns is unreadable at any sensible width, so a week is shown
   * in days. The cumulative of a folded slot is the *last* hour's, not a sum:
   * it is already a running total.
   */
  protected readonly slots = computed<Slot[]>(() => {
    const forecast = this.data();
    if (!forecast) {
      return [];
    }
    const size = this.range().bucket;
    const out: Slot[] = [];

    for (let i = 0; i < forecast.buckets.length; i += size) {
      const group = forecast.buckets.slice(i, i + size);
      const first = group[0];
      const last = group[group.length - 1];
      out.push({
        at: new Date(first.at),
        label: this.labelFor(new Date(first.at), size),
        apprentice: sum(group, 'apprentice'),
        guru: sum(group, 'guru'),
        master: sum(group, 'master'),
        count: sum(group, 'count'),
        cumulative: last.cumulative,
      });
    }
    return out;
  });

  protected readonly peak = computed(() =>
    Math.max(1, ...this.slots().map((slot) => slot.count)),
  );

  /** Round the axis top to something that reads as a number, not a maximum. */
  protected readonly yMax = computed(() => niceCeiling(this.peak()));

  protected readonly yTicks = computed(() => {
    const max = this.yMax();
    const step = max / 4;
    return [0, step, step * 2, step * 3, max].map((value) => ({
      value,
      y: this.barY(value),
    }));
  });

  protected readonly columns = computed<Column[]>(() => {
    const slots = this.slots();
    if (slots.length === 0) {
      return [];
    }
    const inner = PLOT.width - PLOT.left - PLOT.right;
    const band = inner / slots.length;
    const width = Math.min(MAX_BAR, Math.max(2, band - GAP));

    return slots.map((slot, index) => {
      const bandX = PLOT.left + band * index;
      const x = bandX + (band - width) / 2;
      const segments: Segment[] = [];

      // Stacked from the baseline up, so the order of the bands is the order
      // of the legend and never changes with the data.
      const stack: Array<[Segment['band'], number]> = [
        ['apprentice', slot.apprentice],
        ['guru', slot.guru],
        ['master', slot.master],
      ];
      const topBand = [...stack].reverse().find(([, value]) => value > 0)?.[0];

      let running = 0;
      for (const [band_, value] of stack) {
        if (value <= 0) {
          continue;
        }
        const yBottom = this.barY(running);
        const yTop = this.barY(running + value);
        // The 2px surface gap between touching segments comes out of the
        // segment's own height, never from a stroke around it.
        const height = Math.max(1, yBottom - yTop - (running > 0 ? GAP : 0));
        segments.push({
          band: band_,
          x,
          y: yTop,
          width,
          height,
          path:
            band_ === topBand
              ? roundedTop(x, yTop, width, height, Math.min(RADIUS, height))
              : null,
        });
        running += value;
      }

      return { slot, segments, hitX: bandX, hitWidth: band };
    });
  });

  /** The cumulative line, its own chart rather than a second y-axis. */
  protected readonly cumulativePath = computed(() => {
    const slots = this.slots();
    if (slots.length === 0) {
      return '';
    }
    const max = Math.max(1, ...slots.map((slot) => slot.cumulative));
    const inner = LINE.width - LINE.left - LINE.right;
    const step = slots.length > 1 ? inner / (slots.length - 1) : 0;
    return slots
      .map((slot, index) => {
        const x = LINE.left + step * index;
        const y =
          LINE.height - LINE.bottom - (slot.cumulative / max) * (LINE.height - LINE.top - LINE.bottom);
        return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  });

  protected readonly cumulativeMax = computed(() =>
    Math.max(1, ...this.slots().map((slot) => slot.cumulative)),
  );

  /** Label every nth column, so the axis stays readable at any range. */
  protected readonly tickEvery = computed(() => {
    const count = this.slots().length;
    return count <= 8 ? 1 : Math.ceil(count / 8);
  });

  protected barY(value: number): number {
    const inner = PLOT.height - PLOT.top - PLOT.bottom;
    return PLOT.height - PLOT.bottom - (value / this.yMax()) * inner;
  }

  protected bandLabel(band: Segment['band']): string {
    return { apprentice: 'Apprentice', guru: 'Guru', master: 'Master+' }[band];
  }

  private labelFor(at: Date, size: number): string {
    if (size >= 24) {
      return at.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
    }
    return at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }

  protected fullLabel(slot: Slot): string {
    return this.range().bucket >= 24
      ? slot.at.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })
      : slot.at.toLocaleString(undefined, {
          weekday: 'short',
          hour: '2-digit',
          minute: '2-digit',
        });
  }

  /**
   * The deepest the pile gets if nothing is answered.
   *
   * Deliberately not "arriving in 24 hours": at the 24-hour range that is the
   * same number as the tile beside it, and two tiles showing one figure is
   * worse than one tile.
   */
  protected readonly peakWaiting = computed(() => this.cumulativeMax());
}

function sum(buckets: ForecastBucket[], key: keyof ForecastBucket): number {
  return buckets.reduce((total, bucket) => total + (bucket[key] as number), 0);
}

/** A column with its top two corners rounded and its foot square on the axis. */
function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  const radius = Math.min(r, w / 2, h);
  return [
    `M${x},${y + h}`,
    `L${x},${y + radius}`,
    `Q${x},${y} ${x + radius},${y}`,
    `L${x + w - radius},${y}`,
    `Q${x + w},${y} ${x + w},${y + radius}`,
    `L${x + w},${y + h}`,
    'Z',
  ].join(' ');
}

/**
 * A round axis top that is not miles above the data.
 *
 * The factor list is fine-grained on purpose: with only [1, 2, 5, 10] a peak
 * of 12 drew an axis to 20 and left the chart looking half empty.
 */
function niceCeiling(value: number): number {
  if (value <= 4) {
    return 4;
  }
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const factor of [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    const candidate = magnitude * factor;
    if (candidate >= value) {
      // Divisible by four, so the four gridlines land on whole numbers.
      return Math.ceil(candidate / 4) * 4;
    }
  }
  return Math.ceil(value / 4) * 4;
}
