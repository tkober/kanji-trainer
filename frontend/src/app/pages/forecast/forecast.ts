import { Component, computed, inject, signal } from '@angular/core';
import { SumiBanner, SumiCard, SumiEmptyState, SumiPage } from 'sumi-ui/layout';
import {
  SumiBarChart,
  SumiDataTable,
  SumiLegend,
  SumiSparkline,
  SumiStatGrid,
  SumiStatTile,
  type SumiBarSeries,
  type SumiLegendItem,
  type SumiPoint,
  type SumiStackedRow,
  type SumiTableColumn,
  type SumiTableRow,
} from 'sumi-ui/charts';
import { SumiButtonDirective, SumiSegmentedControl, type SumiSegmentedOption } from 'sumi-ui/forms';

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

const RANGES = [
  { hours: 24, label: '24 hours', bucket: 1 },
  { hours: 48, label: '48 hours', bucket: 3 },
  { hours: 168, label: '7 days', bucket: 24 },
] as const;

@Component({
  selector: 'app-forecast',
  imports: [
    SumiPage,
    SumiBanner,
    SumiCard,
    SumiEmptyState,
    SumiStatGrid,
    SumiStatTile,
    SumiBarChart,
    SumiSparkline,
    SumiLegend,
    SumiDataTable,
    SumiSegmentedControl,
    SumiButtonDirective,
  ],
  templateUrl: './forecast.html',
  styleUrl: './forecast.scss',
})
export class ForecastPage {
  private readonly api = inject(Api);

  protected readonly ranges = RANGES;
  protected readonly rangeOptions: SumiSegmentedOption<number>[] = RANGES.map((r) => ({
    value: r.hours,
    label: r.label,
  }));
  protected readonly range = signal<(typeof RANGES)[number]>(RANGES[0]);
  protected readonly data = signal<Forecast | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly showTable = signal(false);

  // The three bands' validated, `light-dark()` colours (see CLAUDE.md, "The
  // forecast") stay app tokens, defined on the page host in forecast.scss
  // so they inherit into the chart via plain CSS custom-property
  // inheritance — `sumi-bar-chart` and `sumi-legend` just read `color`.
  protected readonly series: readonly SumiBarSeries[] = [
    { key: 'apprentice', label: 'Apprentice', color: 'var(--band-apprentice)' },
    { key: 'guru', label: 'Guru', color: 'var(--band-guru)' },
    { key: 'master', label: 'Master+', color: 'var(--band-master)' },
  ];

  protected readonly legendItems: readonly SumiLegendItem[] = this.series.map((s) => ({
    label: s.label,
    color: s.color!,
  }));

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

  pick(hours: number): void {
    const range = RANGES.find((r) => r.hours === hours);
    if (!range) {
      return;
    }
    this.range.set(range);
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

  protected readonly stackedRows = computed<SumiStackedRow[]>(() =>
    this.slots().map((slot) => ({
      label: slot.label,
      values: { apprentice: slot.apprentice, guru: slot.guru, master: slot.master },
    })),
  );

  protected readonly cumulativePoints = computed<SumiPoint[]>(() =>
    this.slots().map((slot, index) => ({ x: index, y: slot.cumulative })),
  );

  protected readonly cumulativeMax = computed(() =>
    Math.max(1, ...this.slots().map((slot) => slot.cumulative)),
  );

  /** At most eight labels per range; `sumi-bar-chart` thins further on
   *  its own when the card is narrower than that needs (sumi-ui#61). */
  protected readonly tickEvery = computed(() => {
    const count = this.slots().length;
    return count <= 8 ? 1 : Math.ceil(count / 8);
  });

  protected readonly numbersColumns: readonly SumiTableColumn[] = [
    { key: 'label', label: 'Slot' },
    { key: 'apprentice', label: 'Apprentice', align: 'end' },
    { key: 'guru', label: 'Guru', align: 'end' },
    { key: 'master', label: 'Master+', align: 'end' },
    { key: 'count', label: 'Arriving', align: 'end' },
    { key: 'cumulative', label: 'Waiting', align: 'end' },
  ];

  protected readonly numbersRows = computed<SumiTableRow[]>(() =>
    this.slots().map((slot) => ({
      label: this.fullLabel(slot),
      apprentice: slot.apprentice,
      guru: slot.guru,
      master: slot.master,
      count: slot.count,
      cumulative: slot.cumulative,
    })),
  );

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
