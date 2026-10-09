import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  SumiBanner,
  SumiCard,
  SumiEmptyState,
  SumiErrorState,
  SumiPage,
} from 'sumi-ui/layout';
import {
  SumiBarChart,
  SumiSegmentedBar,
  SumiStatGrid,
  SumiStatTile,
  type SumiBar,
  type SumiSegment,
} from 'sumi-ui/charts';
import { SumiButtonDirective } from 'sumi-ui/forms';

import { Api } from '../../core/api';
import type { Forecast, ImportRun, Stats } from '../../core/api.types';
import { Counters } from '../../core/counters';

@Component({
  selector: 'app-dashboard',
  imports: [
    RouterLink,
    SumiPage,
    SumiCard,
    SumiBanner,
    SumiEmptyState,
    SumiErrorState,
    SumiStatGrid,
    SumiStatTile,
    SumiBarChart,
    SumiSegmentedBar,
    SumiButtonDirective,
  ],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard {
  private readonly api = inject(Api);
  private readonly counters = inject(Counters);

  protected readonly stats = signal<Stats | null>(null);
  protected readonly forecast = signal<Forecast | null>(null);
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
      this.counters.setLevel(stats.current_level);
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
  // One series, so no legend: the card's own heading says what is plotted.
  // The stage breakdown lives on the forecast page; a dashboard card that
  // tried to carry it would need a legend and a colour key for four
  // numbers.

  protected readonly comingUpBars = computed<SumiBar[]>(() =>
    (this.forecast()?.buckets ?? []).map((bucket, index) => {
      const at = new Date(bucket.at);
      return {
        label: at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }),
        value: bucket.count,
        // The first bucket is the current hour (see backend/app/api/forecast.py).
        highlight: index === 0,
      };
    }),
  );

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

  /**
   * The SRS-stage breakdown as segmented-bar segments.
   *
   * The first five keep the library's default sequential ramp colour (no
   * `color` given). "Marked as known" gets its own colour, clearly apart
   * from the ramp — the whole point of the footnote below is that it is
   * *not* "Burned" (see CLAUDE.md, the `state`/`srs_stage` invariant).
   * "Hidden" and "Unlearned" are both neutral, but "Unlearned" cannot use
   * `--sumi-sunken` itself: that token is the segmented-bar's own track
   * colour, so a segment filled with it would be invisible against the
   * track. `--sumi-line` is the next step up and stays visible.
   */
  protected readonly collectionSegments = computed<SumiSegment[]>(() => {
    const stats = this.stats();
    if (!stats) {
      return [];
    }
    return [
      { label: 'Apprentice', value: stats.apprentice_count },
      { label: 'Guru', value: stats.guru_count },
      { label: 'Master', value: stats.master_count },
      { label: 'Enlightened', value: stats.enlightened_count },
      { label: 'Burned', value: stats.burned_count },
      { label: 'Marked as known', value: stats.known_count, color: 'var(--sumi-correct)' },
      { label: 'Hidden', value: stats.suspended_count, color: 'var(--sumi-muted)' },
      { label: 'Unlearned', value: stats.new_count, color: 'var(--sumi-line)' },
    ];
  });
}
