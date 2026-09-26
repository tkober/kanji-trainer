import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api } from '../../core/api';
import type { Forecast, ImportRun, Stats } from '../../core/api.types';
import { Counters } from '../../core/counters';

@Component({
  selector: 'app-dashboard',
  imports: [RouterLink],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard {
  private readonly api = inject(Api);
  private readonly counters = inject(Counters);

  protected readonly stats = signal<Stats | null>(null);
  protected readonly forecast = signal<Forecast | null>(null);
  protected readonly hovered = signal<Bar | null>(null);
  protected readonly lastImport = signal<ImportRun | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly loading = signal(true);

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const [stats, lastImport, forecast] = await Promise.all([
        this.api.stats(),
        this.api.latestImport(),
        this.api.forecast(24),
      ]);
      this.stats.set(stats);
      // This screen fetches the badge's numbers anyway; handing them over
      // costs nothing and spares the badge a poll's worth of staleness.
      this.counters.setDue(stats.due_now);
      this.counters.setLessons(stats.lessons_available);
      this.lastImport.set(lastImport);
      this.forecast.set(forecast);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  // --- the next 24 hours, in one glance ---------------------------------
  //
  // One series, so no legend: the heading says what is plotted. The stage
  // breakdown lives on the forecast page; a dashboard card that tried to
  // carry it would need a legend and a colour key for four numbers.

  protected readonly plot = PLOT;

  protected readonly peak = computed(() =>
    Math.max(1, ...(this.forecast()?.buckets ?? []).map((bucket) => bucket.count)),
  );

  protected readonly bars = computed<Bar[]>(() => {
    const buckets = this.forecast()?.buckets ?? [];
    if (buckets.length === 0) {
      return [];
    }
    const inner = PLOT.width - PLOT.left - PLOT.right;
    const band = inner / buckets.length;
    const width = Math.min(MAX_BAR, Math.max(2, band - GAP));
    const height = PLOT.height - PLOT.top - PLOT.bottom;
    const max = this.peak();

    return buckets.map((bucket, index) => {
      const bandX = PLOT.left + band * index;
      const barHeight = (bucket.count / max) * height;
      const at = new Date(bucket.at);
      return {
        at,
        label: at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
        count: bucket.count,
        cumulative: bucket.cumulative,
        x: bandX + (band - width) / 2,
        y: PLOT.height - PLOT.bottom - barHeight,
        width,
        height: barHeight,
        path:
          barHeight > 0
            ? roundedTop(
                bandX + (band - width) / 2,
                PLOT.height - PLOT.bottom - barHeight,
                width,
                barHeight,
                Math.min(4, barHeight),
              )
            : null,
        hitX: bandX,
        hitWidth: band,
      };
    });
  });

  /** Label a handful of hours, never all 24. */
  protected readonly tickEvery = 6;

  protected readonly arriving = computed(() =>
    (this.forecast()?.buckets ?? []).reduce((total, bucket) => total + bucket.count, 0),
  );

  protected readonly waitingAfter = computed(() => {
    const buckets = this.forecast()?.buckets ?? [];
    return buckets.length > 0 ? buckets[buckets.length - 1].cumulative : 0;
  });

  protected accuracy(stats: Stats): string {
    return stats.accuracy_today === null
      ? '—'
      : `${Math.round(stats.accuracy_today * 100)} %`;
  }

  /** Share of the collection that is out of the rotation for good. */
  protected settled(stats: Stats): number {
    if (stats.total_subjects === 0) {
      return 0;
    }
    return Math.round(((stats.known_count + stats.burned_count) / stats.total_subjects) * 100);
  }
}


/** One hour of the dashboard's compact forecast. */
interface Bar {
  at: Date;
  label: string;
  count: number;
  cumulative: number;
  x: number;
  y: number;
  width: number;
  height: number;
  path: string | null;
  hitX: number;
  hitWidth: number;
}

const PLOT = { width: 720, height: 104, left: 30, right: 8, top: 10, bottom: 20 };
const MAX_BAR = 24;
const GAP = 2;

/** Rounded data-end, square foot on the baseline. */
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
