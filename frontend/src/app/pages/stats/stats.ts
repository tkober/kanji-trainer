import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { SumiCard, SumiEmptyState, SumiErrorState, SumiPage } from 'sumi-ui/layout';
import {
  SumiBarChart,
  SumiCalendarHeatmap,
  SumiDataTable,
  SumiLegend,
  SumiProgressRow,
  SumiSparkline,
  SumiStatGrid,
  SumiStatTile,
  SumiSunburst,
  SumiTableCellTemplate,
  type SumiBarSeries,
  type SumiLegendItem,
  type SumiSegment,
  type SumiStackedRow,
  type SumiSunburstNode,
  type SumiTableColumn,
  type SumiTableRow,
} from 'sumi-ui/charts';
import {
  SumiButtonDirective,
  SumiSegmentedControl,
  SumiToggle,
  type SumiSegmentedOption,
} from 'sumi-ui/forms';

import { Api } from '../../core/api';
import type {
  Activity,
  ActivityDay,
  Item,
  LevelStats,
  LevelTypeCounts,
  ObjectType,
  StageCounts,
  Stats,
} from '../../core/api.types';
import {
  apprenticeCount,
  combinedCounts,
  emptyStageCounts,
  frameTotal,
  guruPlusEarned,
  levelComplete,
  settledForToggle,
  typeRows,
  type TypeRow,
} from '../../core/level-progress';

/** "Compare against" -- the set of levels every section counts over (issue
 *  #60's "A"). `'mine'` falls back to every level when `current_level` is
 *  null (nothing left to learn, or an empty collection) -- there is no
 *  narrower "your levels" to show in that case anyway. */
type Frame = 'mine' | 'ten' | 'all';

/** The Levels card's own type filter (its segmented control, local to that
 *  card -- unlike the frame/toggle above, it never leaves object_type out of
 *  a stack entirely, it just narrows which rows feed it). */
type LevelTypeFilter = 'all' | 'radical' | 'kanji' | 'vocabulary';

interface TroubleRow extends SumiTableRow {
  id: number;
  label: string;
  item: Item;
  type: string;
  stage: string;
  lapses: number;
  accuracy: string;
}

function sumDays(days: readonly ActivityDay[], key: keyof ActivityDay): number {
  return days.reduce((total, day) => total + (day[key] as number), 0);
}

/** Same breakpoint Sumi UI's own `sumi-session-progress` uses for its narrow
 *  layout switch (`SUMI_SESSION_NARROW_BREAKPOINT`). Below it, the trouble
 *  items table drops its Type and Accuracy columns (see the class doc on
 *  `troubleColumns`) -- done by handing `sumi-data-table` a narrower
 *  `columns` array rather than hiding cells with CSS, since the table's own
 *  markup lives behind that component's view encapsulation and a page
 *  stylesheet cannot reach into it without `::ng-deep`. */
const TROUBLE_TABLE_NARROW_BREAKPOINT = 480;

@Component({
  selector: 'app-stats',
  imports: [
    RouterLink,
    SumiPage,
    SumiCard,
    SumiEmptyState,
    SumiErrorState,
    SumiStatGrid,
    SumiStatTile,
    SumiSunburst,
    SumiProgressRow,
    SumiBarChart,
    SumiCalendarHeatmap,
    SumiSparkline,
    SumiDataTable,
    SumiTableCellTemplate,
    SumiLegend,
    SumiSegmentedControl,
    SumiToggle,
    SumiButtonDirective,
  ],
  templateUrl: './stats.html',
  styleUrl: './stats.scss',
})
export class StatsPage {
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly stats = signal<Stats | null>(null);
  protected readonly levelStats = signal<LevelStats | null>(null);
  protected readonly activity = signal<Activity | null>(null);
  protected readonly troubleItems = signal<Item[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly loading = signal(true);

  protected readonly frame = signal<Frame>('mine');
  protected readonly countKnown = signal(true);
  protected readonly levelTypeFilter = signal<LevelTypeFilter>('all');

  private readonly narrowQuery =
    typeof matchMedia === 'function'
      ? matchMedia(`(max-width: ${TROUBLE_TABLE_NARROW_BREAKPOINT}px)`)
      : null;
  protected readonly narrowTroubleTable = signal(this.narrowQuery?.matches ?? false);

  constructor() {
    void this.load();

    if (this.narrowQuery) {
      const onChange = (event: MediaQueryListEvent) => this.narrowTroubleTable.set(event.matches);
      this.narrowQuery.addEventListener('change', onChange);
      this.destroyRef.onDestroy(() => this.narrowQuery?.removeEventListener('change', onChange));
    }
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      // Four independent requests -- the frame, the toggle and the Levels
      // card's own type filter are all pure client-side `computed`s below,
      // so switching any of them never re-fetches (issue #60's acceptance
      // criterion).
      const [stats, levelStats, activity, troubleItems] = await Promise.all([
        this.api.stats(tz),
        this.api.levelStats(),
        this.api.activity(182, tz),
        this.api.items({ sort: 'lapses', limit: 10 }),
      ]);
      this.stats.set(stats);
      this.levelStats.set(levelStats);
      this.activity.set(activity);
      this.troubleItems.set(troubleItems.items);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  protected readonly ready = computed(
    () => this.stats() !== null && this.levelStats() !== null && this.activity() !== null,
  );

  // --- A: the reference frame -----------------------------------------------

  protected readonly highestLevel = computed(() => {
    const levels = this.levelStats()?.levels ?? [];
    return levels.length > 0 ? Math.max(...levels.map((l) => l.level)) : 1;
  });

  /** Falls back to `highestLevel()` when nothing is currently being taught
   *  (everything done, or an empty collection) -- see the `Frame` doc. */
  protected readonly mineUpper = computed(() => this.stats()?.current_level ?? this.highestLevel());

  protected readonly frameOptions = computed<SumiSegmentedOption<Frame>[]>(() => [
    { value: 'mine', label: `My levels (1–${this.mineUpper()})` },
    { value: 'ten', label: 'Levels 1–10' },
    { value: 'all', label: `All ${this.highestLevel()} levels` },
  ]);

  protected readonly frameUpper = computed(() => {
    switch (this.frame()) {
      case 'mine':
        return this.mineUpper();
      case 'ten':
        return 10;
      case 'all':
        return this.highestLevel();
    }
  });

  private readonly frameEntries = computed<LevelTypeCounts[]>(() =>
    (this.levelStats()?.levels ?? []).filter((entry) => entry.level <= this.frameUpper()),
  );

  protected readonly frameRows = computed<TypeRow[]>(() => typeRows(this.frameEntries()));
  protected readonly frameCombined = computed(() => combinedCounts(this.frameRows()));
  protected readonly frameTotalItems = computed(() => frameTotal(this.frameCombined()));
  protected readonly frameSettled = computed(() =>
    settledForToggle(this.frameCombined(), this.countKnown()),
  );
  protected readonly frameSettledPercent = computed(() => {
    const total = this.frameTotalItems();
    return total > 0 ? Math.round((this.frameSettled() / total) * 100) : 0;
  });

  // --- tiles -----------------------------------------------------------------

  protected readonly itemsSettledHint = computed(
    () => `${this.frameSettledPercent()} % of levels 1–${this.frameUpper()}`,
  );

  private readonly kanjiCounts = computed(
    () => this.frameRows().find((row) => row.key === 'kanji')?.counts ?? emptyStageCounts(),
  );
  protected readonly kanjiSettled = computed(() =>
    settledForToggle(this.kanjiCounts(), this.countKnown()),
  );
  protected readonly kanjiTotal = computed(() => frameTotal(this.kanjiCounts()));
  protected readonly kanjiHint = computed(
    () => `of ${this.kanjiTotal()} in levels 1–${this.frameUpper()}`,
  );

  /** Every level in the frame that has any (non-suspended) item at all, and
   *  where every one of them has been started -- `levelComplete()`'s
   *  `frameTotal() > 0` guard keeps a level the collection simply doesn't
   *  have yet from counting as "complete". */
  protected readonly levelsCompleteCount = computed(() => {
    const byLevel = new Map<number, LevelTypeCounts[]>();
    for (const entry of this.frameEntries()) {
      const bucket = byLevel.get(entry.level) ?? [];
      bucket.push(entry);
      byLevel.set(entry.level, bucket);
    }
    let complete = 0;
    for (const entries of byLevel.values()) {
      if (levelComplete(combinedCounts(typeRows(entries)))) {
        complete++;
      }
    }
    return complete;
  });

  private readonly last30Days = computed(() => (this.activity()?.days ?? []).slice(-30));
  private readonly previous30Days = computed(() => (this.activity()?.days ?? []).slice(-60, -30));

  /** `null` when nothing was answered in the window -- the tile and the
   *  sparkline both render "–" rather than a misleading 0 %. */
  protected readonly accuracyPercent = computed<number | null>(() => {
    const days = this.last30Days();
    const answers = sumDays(days, 'answers');
    return answers > 0 ? Math.round((sumDays(days, 'correct') / answers) * 100) : null;
  });

  protected readonly accuracyDisplay = computed(() =>
    this.accuracyPercent() === null ? '–' : `${this.accuracyPercent()} %`,
  );

  protected readonly answers30 = computed(() => sumDays(this.last30Days(), 'answers'));
  protected readonly answersHint = computed(() =>
    this.answers30() > 0 ? `${this.answers30().toLocaleString('en')} answers` : undefined,
  );

  // --- B: stages (sunburst) ---------------------------------------------------

  protected readonly stagesAriaLabel = computed(
    () => `Items by SRS stage and sub-stage, levels 1–${this.frameUpper()}`,
  );

  protected readonly stageRoot = computed<SumiSunburstNode>(() => {
    const c = this.frameCombined();
    return {
      label: 'Stages',
      children: [
        {
          label: 'Apprentice',
          color: 'var(--band-apprentice)',
          children: [
            { label: 'Apprentice I', value: c.apprentice_1 },
            { label: 'Apprentice II', value: c.apprentice_2 },
            { label: 'Apprentice III', value: c.apprentice_3 },
            { label: 'Apprentice IV', value: c.apprentice_4 },
          ],
        },
        {
          label: 'Guru',
          color: 'var(--band-guru)',
          children: [
            { label: 'Guru I', value: c.guru_1 },
            { label: 'Guru II', value: c.guru_2 },
          ],
        },
        { label: 'Master', value: c.master, color: 'var(--band-master)' },
        { label: 'Enlightened', value: c.enlightened, color: 'var(--band-enlightened)' },
        { label: 'Burned', value: c.burned, color: 'var(--band-burned)' },
        { label: 'Marked as known', value: c.known, color: 'var(--sumi-correct)' },
        { label: 'Lessons open', value: c.new, color: 'var(--sumi-line)' },
      ],
    };
  });

  /** Exposed for the template -- `stage-body`'s `@if (frameCombined(); as c)`
   *  only has the raw `StageCounts`, and the "Apprentice 45" headline needs
   *  the same sum `rowSegments`/`rowSettled` use internally. */
  protected apprenticeCountOf(counts: StageCounts): number {
    return apprenticeCount(counts);
  }

  // --- C: by type (progress rows) ---------------------------------------------

  protected rowSegments(counts: StageCounts): SumiSegment[] {
    return [
      { label: 'Guru+ earned', value: guruPlusEarned(counts), color: 'var(--sumi-seq-5)' },
      { label: 'Marked as known', value: counts.known, color: 'var(--sumi-correct)' },
      { label: 'Apprentice', value: apprenticeCount(counts), color: 'var(--sumi-seq-2)' },
      { label: 'Open', value: counts.new, color: 'var(--sumi-sunken)' },
    ];
  }

  protected rowSettled(counts: StageCounts): number {
    return settledForToggle(counts, this.countKnown());
  }

  protected rowTotal(counts: StageCounts): number {
    return frameTotal(counts);
  }

  // --- D: levels (stacked bar chart) -------------------------------------------

  protected readonly levelTypeOptions: SumiSegmentedOption<LevelTypeFilter>[] = [
    { value: 'all', label: 'All types' },
    { value: 'radical', label: 'Radicals' },
    { value: 'kanji', label: 'Kanji' },
    { value: 'vocabulary', label: 'Vocab' },
  ];

  protected readonly levelSeries: readonly SumiBarSeries[] = [
    { key: 'guru', label: 'Guru+', color: 'var(--sumi-seq-5)' },
    { key: 'known', label: 'Marked as known', color: 'var(--sumi-correct)' },
    { key: 'apprentice', label: 'Apprentice', color: 'var(--sumi-seq-2)' },
    { key: 'notStarted', label: 'Not started', color: 'var(--sumi-line)' },
  ];

  protected readonly levelLegendItems: readonly SumiLegendItem[] = this.levelSeries.map((s) => ({
    label: s.label,
    color: s.color!,
  }));

  protected readonly levelRows = computed<SumiStackedRow[]>(() => {
    const filter = this.levelTypeFilter();
    const byLevel = new Map<number, LevelTypeCounts[]>();
    for (const entry of this.frameEntries()) {
      const bucket = byLevel.get(entry.level) ?? [];
      bucket.push(entry);
      byLevel.set(entry.level, bucket);
    }
    const upper = this.frameUpper();
    const rows: SumiStackedRow[] = [];
    for (let level = 1; level <= upper; level++) {
      const rowsForLevel = typeRows(byLevel.get(level) ?? []);
      const counts =
        filter === 'all'
          ? combinedCounts(rowsForLevel)
          : (rowsForLevel.find((r) => r.key === filter)?.counts ?? emptyStageCounts());
      rows.push({
        label: String(level),
        values: {
          guru: guruPlusEarned(counts),
          known: counts.known,
          apprentice: apprenticeCount(counts),
          notStarted: counts.new,
        },
      });
    }
    return rows;
  });

  protected readonly levelsAriaLabel = computed(
    () => `Every level from 1 to ${this.frameUpper()}, by stage`,
  );

  // --- F: activity -------------------------------------------------------------

  protected readonly heatmapDays = computed(() =>
    (this.activity()?.days ?? []).map((day) => ({ date: day.date, value: day.answers })),
  );
  protected readonly streak = computed(() => this.activity()?.streak ?? 0);

  /** Days without an answer are skipped (not carried forward, not shown as
   *  0 %) -- a day nothing was answered on has no accuracy to plot, and
   *  carrying the previous day's figure into it would read as if it had
   *  been measured that day too. */
  protected readonly accuracySparkPoints = computed<number[]>(() =>
    this.last30Days()
      .filter((day) => day.answers > 0)
      .map((day) => Math.round((day.correct / day.answers) * 100)),
  );

  protected readonly accuracyDelta = computed<number | null>(() => {
    const current = this.accuracyPercent();
    const previous = this.previous30Days();
    const previousAnswers = sumDays(previous, 'answers');
    if (current === null || previousAnswers === 0) {
      return null;
    }
    const previousPercent = Math.round((sumDays(previous, 'correct') / previousAnswers) * 100);
    return current - previousPercent;
  });

  protected readonly lessonsSparkPoints = computed<number[]>(() =>
    this.last30Days().map((day) => day.lessons_started),
  );
  protected readonly lessonsSum = computed(() => sumDays(this.last30Days(), 'lessons_started'));
  protected readonly lessonsDisplay = computed(() => `${this.lessonsSum()} started`);

  // --- H: trouble items ----------------------------------------------------

  /** Type and Accuracy drop out below `TROUBLE_TABLE_NARROW_BREAKPOINT` --
   *  Item, Stage and Lapses already say which item is in trouble and how
   *  often, and this is what keeps the table from overflowing sideways on a
   *  phone instead of just hiding cells underneath it (see that constant's
   *  own comment). */
  protected readonly troubleColumns = computed<SumiTableColumn[]>(() => {
    const columns: SumiTableColumn[] = [
      { key: 'item', label: 'Item' },
      { key: 'type', label: 'Type' },
      { key: 'stage', label: 'Stage' },
      { key: 'lapses', label: 'Lapses', align: 'end' },
      { key: 'accuracy', label: 'Accuracy', align: 'end' },
    ];
    return this.narrowTroubleTable()
      ? columns.filter((c) => c.key !== 'type' && c.key !== 'accuracy')
      : columns;
  });

  protected readonly troubleRows = computed<TroubleRow[]>(() =>
    this.troubleItems()
      .filter((item) => item.progress.lapses > 0)
      .map((item) => ({
        id: item.subject.id,
        label: item.subject.characters ?? item.subject.slug,
        item,
        type: this.typeLabel(item.subject.object_type),
        stage: item.progress.stage_name,
        lapses: item.progress.lapses,
        accuracy: this.itemAccuracy(item),
      })),
  );

  protected openTroubleItem(row: SumiTableRow): void {
    void this.router.navigate(['/browse'], { queryParams: { item: (row as TroubleRow).id } });
  }

  private typeLabel(type: ObjectType): string {
    return (
      {
        radical: 'Radical',
        kanji: 'Kanji',
        vocabulary: 'Vocabulary',
        kana_vocabulary: 'Vocabulary',
      } as const
    )[type];
  }

  private itemAccuracy(item: Item): string {
    const total = item.progress.correct_count + item.progress.incorrect_count;
    return total > 0 ? `${Math.round((item.progress.correct_count / total) * 100)} %` : '–';
  }
}
