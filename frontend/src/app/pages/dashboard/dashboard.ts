import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SumiCard, SumiEmptyState, SumiErrorState, SumiPage } from 'sumi-ui/layout';
import {
  SumiProgressRow,
  SumiSegmentedBar,
  SumiSparkline,
  SumiStatGrid,
  SumiStatTile,
  type SumiSegment,
} from 'sumi-ui/charts';
import { SumiButtonDirective } from 'sumi-ui/forms';

import { Api } from '../../core/api';
import type {
  Activity,
  Forecast,
  LessonTile,
  LevelStats,
  StageCounts,
  Stats,
} from '../../core/api.types';
import { Counters } from '../../core/counters';
import {
  apprenticeCount,
  combinedCounts,
  frameTotal,
  guruPlusEarned,
  settledCount,
  typeRows,
  type TypeRow,
} from '../../core/level-progress';
import { ItemTile } from '../../shared/item-tile/item-tile';

/** A row of the list below "Upcoming": either an hour with reviews, or the
 *  "Tomorrow" divider inserted before the first one on the next local day. */
interface UpcomingRow {
  kind: 'divider' | 'hour';
  label?: string;
  time?: string;
  count?: number;
  total?: number;
}

/** `'2 h'`, `'35 min'`, `'3 d'` -- one unit, for a tile's own hint line
 *  (kept to one number, see the "reviews due" hint). */
function relativeShort(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / 60000));
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} h`;
  }
  return `${Math.round(hours / 24)} d`;
}

/** `'2 h 10 min'` -- the Upcoming card's own headline, which has room for
 *  the extra precision a tile's hint does not. */
function relativeLong(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / 60000));
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours < 24) {
    return mins === 0 ? `${hours} h` : `${hours} h ${mins} min`;
  }
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours === 0 ? `${days} d` : `${days} d ${remHours} h`;
}

function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

@Component({
  selector: 'app-dashboard',
  imports: [
    RouterLink,
    SumiPage,
    SumiCard,
    SumiEmptyState,
    SumiErrorState,
    SumiStatGrid,
    SumiStatTile,
    SumiSegmentedBar,
    SumiSparkline,
    SumiProgressRow,
    SumiButtonDirective,
    ItemTile,
  ],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard {
  private readonly api = inject(Api);
  private readonly counters = inject(Counters);

  protected readonly stats = signal<Stats | null>(null);
  protected readonly forecast = signal<Forecast | null>(null);
  protected readonly levelStats = signal<LevelStats | null>(null);
  protected readonly activity = signal<Activity | null>(null);
  protected readonly levelTiles = signal<LessonTile[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly loading = signal(true);

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      // Independent requests, not a waterfall: `lessons()` (omitted `level`)
      // and `stats()`/`levelStats()` all resolve "current level" the same
      // way on the backend (`lowest_open_level()`), so there is nothing to
      // wait on here.
      const [stats, forecast, levelStats, activity, lessons] = await Promise.all([
        this.api.stats(tz),
        this.api.forecast(24),
        this.api.levelStats(),
        this.api.activity(30, tz),
        this.api.lessons(),
      ]);
      this.stats.set(stats);
      // This screen fetches the badge's numbers anyway; handing them over
      // costs nothing and spares the badge a poll's worth of staleness.
      this.counters.setDue(stats.due_now);
      this.counters.setLessons(stats.lessons_available);
      this.counters.setLevel(stats.current_level);
      this.forecast.set(forecast);
      this.levelStats.set(levelStats);
      this.activity.set(activity);
      this.levelTiles.set(lessons.tiles);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  // --- tiles --------------------------------------------------------------

  protected accuracy(stats: Stats): string {
    return stats.accuracy_today === null ? '—' : `${Math.round(stats.accuracy_today * 100)} %`;
  }

  protected readonly accuracyHint = computed<string | undefined>(() => {
    const stats = this.stats();
    if (!stats || stats.accuracy_today === null) {
      return undefined;
    }
    return `${Math.round(stats.accuracy_today * 100)} % correct`;
  });

  /** Only while nothing is due -- once something is, the number itself is
   *  the call to action and a hint would just be noise. */
  protected readonly dueHint = computed<string | undefined>(() => {
    const stats = this.stats();
    if (!stats || stats.due_now > 0 || !stats.next_review_at) {
      return undefined;
    }
    const ms = new Date(stats.next_review_at).getTime() - Date.now();
    return `next ${stats.next_review_count} at ${clockTime(stats.next_review_at)} · in ${relativeShort(ms)}`;
  });

  // --- the current level ---------------------------------------------------

  protected readonly levelTypeRows = computed<TypeRow[]>(() => {
    const data = this.levelStats();
    const level = this.stats()?.current_level;
    if (!data || level === null || level === undefined) {
      return [];
    }
    return typeRows(data.levels.filter((entry) => entry.level === level));
  });

  protected readonly levelCombined = computed(() => combinedCounts(this.levelTypeRows()));

  protected readonly levelTotal = computed(() => frameTotal(this.levelCombined()));
  protected readonly levelStarted = computed(() => this.levelTotal() - this.levelCombined().new);
  protected readonly levelSettled = computed(() => settledCount(this.levelCombined()));

  /** "{k} kanji · {v} vocabulary" -- radicals are left out on purpose, same
   *  as the mockup: by the time a level has kanji/vocabulary lessons open,
   *  its radicals are usually long since picked up. */
  protected readonly lessonsHint = computed<string | undefined>(() => {
    const rows = this.levelTypeRows();
    const kanji = rows.find((r) => r.key === 'kanji')?.counts.new ?? 0;
    const vocabulary = rows.find((r) => r.key === 'vocabulary')?.counts.new ?? 0;
    const parts: string[] = [];
    if (kanji > 0) {
      parts.push(`${kanji} kanji`);
    }
    if (vocabulary > 0) {
      parts.push(`${vocabulary} vocabulary`);
    }
    return parts.length > 0 ? parts.join(' · ') : undefined;
  });

  protected readonly levelKanjiTiles = computed(() =>
    this.levelTiles().filter((tile) => tile.subject.object_type === 'kanji'),
  );

  /** A level card / progress row's own small bar: three real segments plus a
   *  fourth, coloured to match the track itself, so what is still open shows
   *  as empty space rather than a labelled slice -- no legend here, the "x /
   *  y" beside the bar already says it in text. */
  protected rowSegments(counts: StageCounts): SumiSegment[] {
    return [
      { label: 'Guru+ earned', value: guruPlusEarned(counts), color: 'var(--sumi-seq-5)' },
      { label: 'Marked as known', value: counts.known, color: 'var(--sumi-correct)' },
      { label: 'Apprentice', value: apprenticeCount(counts), color: 'var(--sumi-seq-2)' },
      { label: 'Open', value: counts.new, color: 'var(--sumi-sunken)' },
    ];
  }

  protected rowSettled(counts: StageCounts): number {
    return settledCount(counts);
  }

  protected rowTotal(counts: StageCounts): number {
    return frameTotal(counts);
  }

  // --- upcoming -------------------------------------------------------------

  protected readonly upcomingHeadline = computed<string | undefined>(() => {
    const stats = this.stats();
    if (!stats) {
      return undefined;
    }
    if (stats.due_now > 0) {
      return `${stats.due_now} waiting now`;
    }
    if (!stats.next_review_at) {
      return undefined;
    }
    const ms = new Date(stats.next_review_at).getTime() - Date.now();
    return `Next reviews in ${relativeLong(ms)}`;
  });

  /** Only the hours something actually arrives in, each with its running
   *  total -- `cumulative` already includes `due_now`, same as the full
   *  forecast page's own chart. A "Tomorrow" divider goes in front of the
   *  first one that falls on the next local day. */
  protected readonly upcomingRows = computed<UpcomingRow[]>(() => {
    const forecast = this.forecast();
    if (!forecast) {
      return [];
    }
    const today = new Date().toDateString();
    const rows: UpcomingRow[] = [];
    let dividerAdded = false;
    for (const bucket of forecast.buckets) {
      if (bucket.count <= 0) {
        continue;
      }
      const at = new Date(bucket.at);
      if (!dividerAdded && at.toDateString() !== today) {
        rows.push({ kind: 'divider', label: 'Tomorrow' });
        dividerAdded = true;
      }
      rows.push({
        kind: 'hour',
        time: clockTime(bucket.at),
        count: bucket.count,
        total: bucket.cumulative,
      });
    }
    return rows;
  });

  // --- your progress ---------------------------------------------------------

  protected readonly frameTypeRows = computed<TypeRow[]>(() => {
    const data = this.levelStats();
    const level = this.stats()?.current_level;
    if (!data || level === null || level === undefined) {
      return [];
    }
    return typeRows(data.levels.filter((entry) => entry.level <= level));
  });

  protected readonly frameCombined = computed(() => combinedCounts(this.frameTypeRows()));

  protected readonly progressTotal = computed(() => frameTotal(this.frameCombined()));
  protected readonly progressSettled = computed(() => settledCount(this.frameCombined()));
  protected readonly progressPercent = computed(() => {
    const total = this.progressTotal();
    return total > 0 ? Math.round((this.progressSettled() / total) * 100) : 0;
  });

  /** Same three segments as a level row, plus a fourth that -- unlike the
   *  row's own track-coloured one -- is a real, named, legend-visible
   *  segment: "Lessons open" is worth naming at this scale. */
  protected readonly progressSegments = computed<SumiSegment[]>(() => {
    const counts = this.frameCombined();
    return [
      { label: 'Guru+ earned', value: guruPlusEarned(counts), color: 'var(--sumi-seq-5)' },
      { label: 'Marked as known', value: counts.known, color: 'var(--sumi-correct)' },
      { label: 'Apprentice', value: apprenticeCount(counts), color: 'var(--sumi-seq-2)' },
      { label: 'Lessons open', value: counts.new, color: 'var(--sumi-line)' },
    ];
  });

  /** Cumulative `reached_guru` over the window -- the sparkline's own point
   *  series, oldest day first (`Api.activity`'s own order). */
  protected readonly guruSparklinePoints = computed<number[]>(() => {
    const days = this.activity()?.days ?? [];
    let running = 0;
    return days.map((day) => (running += day.reached_guru));
  });

  protected readonly guruSparklineTotal = computed(() => {
    const points = this.guruSparklinePoints();
    return points.length > 0 ? points[points.length - 1] : 0;
  });
}
