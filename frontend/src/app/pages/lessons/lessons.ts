import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SUMI_KEYS, SumiHotkeys, SumiKeyboardVisibility, injectHotkey } from 'sumi-ui/core';
import {
  SumiBanner,
  SumiCard,
  SumiEmptyState,
  SumiErrorState,
  SumiFocusModeDirective,
  SumiHanko,
  SumiPage,
  SumiShellFocusActionsDirective,
} from 'sumi-ui/layout';
import { SumiButtonDirective, SumiCheckboxDirective } from 'sumi-ui/forms';
import { SumiLegend, type SumiLegendItem } from 'sumi-ui/charts';
import { SUMI_PRACTICE, type SumiSessionProgressSegment, type SumiVerdict } from 'sumi-ui/practice';

import { Api } from '../../core/api';
import type {
  LessonItem,
  LessonTile,
  Lessons,
  LevelSummary,
  ObjectType,
  QuestionType,
  QuizResult,
  SubjectDetail,
} from '../../core/api.types';
import { Counters } from '../../core/counters';
import { Mnemonic } from '../../core/mnemonic';
import { reinsert, shuffleQuestions } from '../../core/review-queue';
import { LevelPicker } from '../../shared/level-picker/level-picker';
import { ContextSentences } from '../../shared/context-sentences/context-sentences';
import { InfoPopover } from '../../shared/info-popover/info-popover';
import { ItemTile } from '../../shared/item-tile/item-tile';
import { RadicalIllustration } from '../../shared/radical-illustration/radical-illustration';
import { Readings } from '../../shared/readings/readings';
import { Synonyms } from '../../shared/synonyms/synonyms';
import { BatchTiles, type BatchTileData } from './batch-tiles';

/** One item in the quiz, with the questions it still owes. */
interface QuizCard {
  subject: SubjectDetail;
  questions: QuestionType[];
}

type Phase = 'select' | 'reading' | 'gate' | 'quiz' | 'ended';

/** One of the three tile groups the selection view is organised into. */
interface TileGroup {
  key: 'radical' | 'kanji' | 'vocabulary';
  label: string;
  tiles: LessonTile[];
}

const BATCHING_KEY = 'kt-lesson-batching';

/** The band legend, in `lessons.scss`'s own custom properties. "Lesson" uses
 * `--sumi-line` rather than `--sumi-surface` (what the tile itself shows) --
 * `sumi-legend`'s swatch has no border of its own, so the page-background
 * colour the tile uses would be invisible against the card behind it. */
const LEGEND_ITEMS: readonly SumiLegendItem[] = [
  { label: 'Lesson', color: 'var(--sumi-line)' },
  { label: 'Apprentice (1–4)', color: 'var(--band-apprentice)' },
  { label: 'Guru (5–6)', color: 'var(--band-guru)' },
  { label: 'Master (7)', color: 'var(--band-master)' },
  { label: 'Enlightened (8)', color: 'var(--band-enlightened)' },
  { label: 'Burned (9)', color: 'var(--band-burned)' },
];

function readBatchingPreference(): boolean {
  try {
    const stored = localStorage.getItem(BATCHING_KEY);
    return stored === null ? true : stored === '1';
  } catch {
    return true;
  }
}

function chunk<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

/** One batch's exact question size, and the correct answers given while it
 * was the active batch -- see `questionsFor`/`questionsCount` below for the
 * unit, same reasoning as `RoundProgress` in the review page. */
interface BatchProgress {
  size: number;
  done: number;
}

/** Sum of every item's own question count (`questionsFor`, the same mapping
 * `startQuiz()` builds its queue from) over a batch -- the unit `done`/
 * `runRemaining` and the progress segments all count in. */
function questionsCount(items: readonly { subject: { object_type: ObjectType } }[]): number {
  return items.reduce((sum, item) => sum + questionsFor(item.subject.object_type).length, 0);
}

/**
 * Lessons: pick items from a level's tiles, read them, then be quizzed, then
 * into the SRS.
 *
 * The picker is the point: WaniKani hands over a fixed batch in a fixed
 * order, and the one thing this app exists to add is the override. Letting
 * the learner choose exactly which items to spend a session on -- including
 * skipping ones already known from elsewhere -- is that override applied to
 * lessons instead of just to reviews.
 *
 * The quiz stays the gate it always was: nothing here goes to the SRS without
 * having been produced once. Failing it costs nothing -- the card is
 * reinserted later in the current batch's queue. `/api/lessons/quiz` has no
 * side effects, and only `/api/lessons/start` moves anything, one batch at a
 * time. A `gate` phase sits between reading a batch and quizzing it (issue
 * #38), and the whole run -- every batch, not just the last one -- ends in a
 * summary, the same shape reviews use.
 */
@Component({
  selector: 'app-lessons',
  imports: [
    BatchTiles,
    ContextSentences,
    InfoPopover,
    ItemTile,
    LevelPicker,
    Mnemonic,
    RadicalIllustration,
    Readings,
    RouterLink,
    Synonyms,
    SumiBanner,
    SumiButtonDirective,
    SumiCard,
    SumiCheckboxDirective,
    SumiEmptyState,
    SumiErrorState,
    SumiFocusModeDirective,
    SumiHanko,
    SumiLegend,
    SumiPage,
    SumiShellFocusActionsDirective,
    ...SUMI_PRACTICE,
  ],
  templateUrl: './lessons.html',
  styleUrl: './lessons.scss',
})
export class LessonsPage {
  private readonly api = inject(Api);
  protected readonly counters = inject(Counters);
  private readonly hotkeys = inject(SumiHotkeys);
  /** Hides the reading phase's own sticky action bar while the on-screen
   *  keyboard is open (issue #87) -- same signal the shell itself reads to
   *  hide its bottom tab bar, so the bar does not cover the synonym field. */
  protected readonly keyboard = inject(SumiKeyboardVisibility);

  protected readonly phase = signal<Phase>('select');

  // --- the selection view -------------------------------------------------

  protected readonly level = signal<number | null>(null);
  protected readonly levels = signal<LevelSummary[]>([]);
  protected readonly tiles = signal<LessonTile[]>([]);
  protected readonly totalInLevel = signal(0);
  protected readonly totalAvailable = signal(0);
  protected readonly dailyLimit = signal(0);
  protected readonly batchSize = signal(5);
  protected readonly remainingToday = signal<number | null>(null);

  protected readonly selectedIds = signal<Set<number>>(new Set());
  protected readonly batching = signal(readBatchingPreference());

  protected readonly selectedCount = computed(() => this.selectedIds().size);
  protected readonly capReached = computed(() => {
    const remaining = this.remainingToday();
    return remaining !== null && this.selectedCount() >= remaining;
  });

  protected readonly groups = computed<TileGroup[]>(() => {
    const tiles = this.tiles();
    return [
      { key: 'radical', label: 'Radicals', tiles: tiles.filter((t) => t.subject.object_type === 'radical') },
      { key: 'kanji', label: 'Kanji', tiles: tiles.filter((t) => t.subject.object_type === 'kanji') },
      {
        key: 'vocabulary',
        label: 'Vocabulary',
        tiles: tiles.filter(
          (t) => t.subject.object_type === 'vocabulary' || t.subject.object_type === 'kana_vocabulary',
        ),
      },
    ];
  });

  // --- the run: one or more batches, read then quizzed then committed ----

  protected readonly chunks = signal<LessonItem[][]>([]);
  protected readonly chunkIndex = signal(0);
  protected readonly totalChunks = computed(() => this.chunks().length);

  protected readonly items = signal<LessonItem[]>([]);
  protected readonly index = signal(0);

  /** Subject ids seen this batch (issue #87's item tiles) -- by id, not
   *  array index: `markCurrentKnown()` splices an item out of `items()`,
   *  which would leave an index-based set pointing at the wrong neighbour
   *  the moment that happens. Reset in `openChunk()`, below. */
  protected readonly visited = signal<ReadonlySet<number>>(new Set());

  /** `app-batch-tiles`' own view of the current batch -- plain data, not
   *  `toneFor`/`typeLabel` handed down as functions, so that component
   *  stays decoupled from `api.types` and this page's own helpers. */
  protected readonly batchTiles = computed<BatchTileData[]>(() => {
    const items = this.items();
    const total = items.length;
    return items.map((item, i) => {
      const subject = item.subject;
      return {
        id: subject.id,
        characters: subject.characters,
        imageUrl: subject.character_image_url,
        alt: subject.slug,
        tone: this.toneFor(subject.object_type),
        isWord: subject.object_type === 'vocabulary' || subject.object_type === 'kana_vocabulary',
        ariaLabel:
          `${subject.characters ?? subject.slug}, ${this.typeLabel(subject.object_type).toLowerCase()}, ` +
          `item ${i + 1} of ${total}`,
      };
    });
  });

  protected readonly queue = signal<QuizCard[]>([]);
  protected readonly value = signal('');
  protected readonly feedback = signal<QuizResult | null>(null);

  /** The whole run's answered/correct, across every batch -- what the quiz's
   * `sumi-session-bar` now shows for accuracy too (issue #54), and what the
   * summary has always used. */
  protected readonly runAnswered = signal(0);
  protected readonly runCorrect = signal(0);
  protected readonly runDurationMs = signal(0);
  /**
   * One entry per batch of the current run, in order -- `size` is each
   * batch's exact question count (`questionsFor`, the same mapping
   * `startQuiz()` builds its queue from), known up front since every
   * batch's items were already fetched in full by `startSelected()`, unlike
   * reviews' unloaded rounds. Set for every batch as soon as a selection is
   * chunked; `startQuiz()` then overwrites the current batch's `size` with
   * the post-"I know this" count, since an item marked known during reading
   * never reaches the quiz. `done` is the correct non-retry answers given
   * while that batch was being quizzed.
   */
  protected readonly batches = signal<BatchProgress[]>([]);
  /** `0` means "the run has not reached a quiz yet" -- set once, by the
   * first `startQuiz()` of the run, so the summary's duration is measured
   * from the first quiz rather than from picking items or reading them. */
  private runStartedAt = 0;
  /** `counters.level()` when the run started, for `levelUp`. */
  private runStartLevel: number | null = null;

  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  /** Whether the selection view has ever loaded successfully -- a failure
   * before that point is the T6 scene; one after it (e.g. picking another
   * level) stays a banner. */
  protected readonly loaded = signal(false);
  protected readonly loadFailed = signal(false);

  protected readonly legendItems = LEGEND_ITEMS;

  protected readonly current = computed(() => this.items()[this.index()] ?? null);
  protected readonly card = computed(() => this.queue()[0] ?? null);
  protected readonly question = computed<QuestionType | null>(
    () => this.card()?.questions[0] ?? null,
  );

  /** Open questions still due across the *whole run* -- what
   * `sumi-session-bar`'s `[remaining]` now wants (issue #54), not just the
   * current batch. `runCorrect` already advances in the same tick a correct
   * answer lands (`submitQuizAnswer`), so this needs no extra "verdict is
   * up" adjustment to keep `runCorrect() + runRemaining()` steady. */
  protected readonly runRemaining = computed(() => {
    const total = this.batches().reduce((sum, batch) => sum + batch.size, 0);
    return Math.max(0, total - this.runCorrect());
  });

  /** `sumi-session-progress`'s segments: one per batch at its exact question
   * size, labelled "Batch N" once batching produced more than one chunk --
   * a single chunk renders as one continuous, unlabelled bar instead, same
   * convention the review page uses for a single round. */
  protected readonly segments = computed<SumiSessionProgressSegment[]>(() => {
    const batches = this.batches();
    if (this.totalChunks() <= 1) {
      const batch = batches[0] ?? { size: 0, done: 0 };
      return [{ size: batch.size, done: batch.done }];
    }
    return batches.map((batch, i) => ({ size: batch.size, done: batch.done, label: `Batch ${i + 1}` }));
  });

  protected readonly onLastItem = computed(() => this.index() >= this.items().length - 1);

  /** A settled (non-retry) quiz verdict is on screen. */
  private readonly hasSettledFeedback = computed(() => {
    const result = this.feedback();
    return !!result && !result.retry;
  });

  /** The field's own one-line verdict. Unlike reviews, the quiz has no
   * `held` state at all -- a typo is already forgiven by the typo
   * tolerance, and nothing here is ever worth a second-chance prompt since
   * the quiz has no stake to protect. */
  protected readonly fieldVerdict = computed<SumiVerdict | null>(() => {
    const result = this.feedback();
    if (!result) {
      return null;
    }
    if (result.retry) {
      return { kind: 'retry', message: result.hint ?? undefined };
    }
    if (result.correct) {
      const message = result.typo
        ? `It is spelled ${result.expected}.`
        : result.secondary
          ? `The primary answer is ${result.expected}.`
          : undefined;
      return { kind: 'correct', message };
    }
    return { kind: 'wrong' };
  });

  /** `sumi-verdict` only ever appears for a settled wrong answer in the quiz
   * -- a retry is "doesn't count", conveyed by the field alone, and a
   * correct answer has nothing left to add beyond the field's own message. */
  protected readonly showVerdictCard = computed(
    () => this.hasSettledFeedback() && !this.feedback()!.correct,
  );

  protected readonly gateText = computed(
    () => {
      const n = this.items().length;
      return (
        `${n} ${n === 1 ? 'item' : 'items'}. Answer each question once; wrong answers ` +
        'come round again and cost nothing.'
      );
    },
  );

  protected readonly checkButtonLabel = computed(() => (this.feedback() ? 'Next' : 'Check'));

  private readonly runAccuracy = computed(() => {
    const answered = this.runAnswered();
    return answered > 0 ? this.runCorrect() / answered : 0;
  });

  /** Same hanko convention as reviews -- see docs/concept.md#tuschemotive. */
  protected readonly hankoCharacters = computed(() => (this.runAccuracy() >= 0.8 ? '合格' : '練習'));
  protected readonly hankoLabel = computed(() => (this.runAccuracy() >= 0.8 ? 'Passed' : 'Practice'));

  protected readonly levelUp = computed<string | undefined>(() => {
    const start = this.runStartLevel;
    const level = this.counters.level();
    if (start === null || level === null || level <= start) {
      return undefined;
    }
    return `Level ${level}`;
  });

  constructor() {
    void this.load();

    // `?` only becomes a hotkey once a settled quiz verdict is up -- same
    // reasoning as the review screen.
    injectHotkey({
      keys: SUMI_KEYS.help,
      label: 'Toggle this menu (after answering)',
      scope: 'feedback',
      allowInEditable: true,
      enabled: () => this.phase() === 'quiz' && this.hasSettledFeedback(),
      handler: () => this.hotkeys.toggleHelp(),
    });
  }

  async load(level?: number | null): Promise<void> {
    this.loading.set(true);
    try {
      const lessons = await this.api.lessons(level ?? this.level());
      this.applyLessons(lessons);
      this.selectedIds.set(new Set());
      this.chunks.set([]);
      this.chunkIndex.set(0);
      this.phase.set('select');
      this.error.set(null);
      this.loaded.set(true);
      this.loadFailed.set(false);
    } catch (err) {
      if (this.loaded()) {
        this.error.set((err as Error).message);
      } else {
        this.loadFailed.set(true);
      }
    } finally {
      this.loading.set(false);
    }
  }

  private applyLessons(lessons: Lessons): void {
    this.level.set(lessons.level);
    this.levels.set(lessons.levels);
    this.tiles.set(lessons.tiles);
    this.totalInLevel.set(lessons.total_in_level);
    this.totalAvailable.set(lessons.total_available);
    this.dailyLimit.set(lessons.daily_limit);
    this.batchSize.set(lessons.batch_size);
    this.remainingToday.set(lessons.remaining_today);
    this.syncCounters(lessons);
  }

  /**
   * The badge counts the *lowest open* level, not whatever the picker is
   * showing -- see `Stats.lessons_available` on the backend. So the exact
   * count from this response is only trustworthy for the header when the two
   * happen to be the same level; otherwise a full refresh is the honest move.
   */
  private syncCounters(lessons: Lessons): void {
    const lowestOpen = lessons.levels.find((entry) => entry.open_count > 0) ?? null;
    if (lowestOpen && lessons.level === lowestOpen.level) {
      this.counters.setLessons(lessons.total_in_level);
    } else {
      void this.counters.refresh();
    }
  }

  pickLevel(level: number): void {
    void this.load(level);
  }

  // --- selection ----------------------------------------------------------

  protected isSelected(id: number): boolean {
    return this.selectedIds().has(id);
  }

  toggleTile(tile: LessonTile): void {
    if (tile.state !== 'new') {
      return;
    }
    const ids = new Set(this.selectedIds());
    if (ids.has(tile.subject.id)) {
      ids.delete(tile.subject.id);
    } else {
      const remaining = this.remainingToday();
      if (remaining !== null && ids.size >= remaining) {
        return;
      }
      ids.add(tile.subject.id);
    }
    this.selectedIds.set(ids);
  }

  selectAllIn(tiles: LessonTile[]): void {
    const ids = new Set(this.selectedIds());
    const remaining = this.remainingToday();
    for (const tile of tiles) {
      if (tile.state !== 'new') {
        continue;
      }
      if (remaining !== null && ids.size >= remaining) {
        break;
      }
      ids.add(tile.subject.id);
    }
    this.selectedIds.set(ids);
  }

  noneIn(tiles: LessonTile[]): void {
    const drop = new Set(tiles.map((tile) => tile.subject.id));
    const ids = new Set(this.selectedIds());
    for (const id of drop) {
      ids.delete(id);
    }
    this.selectedIds.set(ids);
  }

  toggleBatching(event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    this.batching.set(checked);
    try {
      localStorage.setItem(BATCHING_KEY, checked ? '1' : '0');
    } catch {
      // A remembered preference is a convenience, not a requirement.
    }
  }

  /** Fetch detail for the selection, split it into batches, and start reading. */
  async startSelected(): Promise<void> {
    const ids = [...this.selectedIds()];
    if (ids.length === 0 || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      const detailed = await this.api.lessonItems(ids);
      if (detailed.length === 0) {
        // Everything picked has since left the lesson state (started
        // elsewhere, or declared known) -- nothing to read.
        this.error.set('Those items are no longer available as lessons.');
        await this.load();
        return;
      }
      const size = this.batching() ? Math.max(1, this.batchSize()) : detailed.length;
      this.chunks.set(chunk(detailed, size));
      this.chunkIndex.set(0);
      // Every batch's exact size, known up front -- `startQuiz()` corrects
      // the current one for any "I know this" that happened during reading.
      this.batches.set(this.chunks().map((items) => ({ size: questionsCount(items), done: 0 })));
      this.runAnswered.set(0);
      this.runCorrect.set(0);
      this.runStartedAt = 0;
      this.runStartLevel = this.counters.level();
      this.openChunk(0);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  /** "I already know these" for the whole current selection, from the picker. */
  async markSelectedKnown(): Promise<void> {
    const ids = [...this.selectedIds()];
    if (ids.length === 0 || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      await this.api.markKnown(ids);
      await this.load();
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  // --- the run --------------------------------------------------------

  private openChunk(i: number): void {
    const items = this.chunks()[i] ?? [];
    this.items.set(items);
    this.index.set(0);
    this.phase.set('reading');
    this.queue.set([]);
    this.feedback.set(null);
    this.value.set('');
    // Fresh batch, fresh tile strip -- only the first item starts visited.
    this.visited.set(items[0] ? new Set([items[0].subject.id]) : new Set());
  }

  /** Commit the current chunk, or wrap up the whole run if it was the last one. */
  private async advanceChunk(): Promise<void> {
    const next = this.chunkIndex() + 1;
    if (next < this.chunks().length) {
      this.chunkIndex.set(next);
      this.openChunk(next);
      return;
    }
    this.chunks.set([]);
    this.chunkIndex.set(0);
    this.finishRun();
  }

  /** The run's summary, in the same `ended` shape reviews end in. Reached
   * only once the last batch has actually been committed, so `runAnswered`
   * is always positive here. */
  private finishRun(): void {
    this.runDurationMs.set(Date.now() - this.runStartedAt);
    void this.counters.refresh();
    this.phase.set('ended');
  }

  /** "Practice again", or `Enter` on the summary -- back to the picker. */
  protected restartFromEnded(): void {
    void this.load();
  }

  /** Keep the level's own numbers roughly honest between full reloads,
   * without disturbing a run in progress. A full `load()` at the end of the
   * run reconciles everything exactly, tiles included. */
  private spendLocally(n: number): void {
    const lowestOpen = this.levels().find((entry) => entry.open_count > 0) ?? null;
    if (lowestOpen && this.level() === lowestOpen.level) {
      this.counters.spendLessons(n);
    }
    this.totalInLevel.update((value) => Math.max(0, value - n));
    this.totalAvailable.update((value) => Math.max(0, value - n));
    this.levels.update((entries) =>
      entries.map((entry) =>
        entry.level === this.level() ? { ...entry, open_count: Math.max(0, entry.open_count - n) } : entry,
      ),
    );
  }

  /** Abandon whatever has not been committed yet and go back to the picker.
   * Batches already committed (via `/lessons/start`) stay committed. */
  async backToSelection(): Promise<void> {
    this.chunks.set([]);
    this.chunkIndex.set(0);
    await this.load();
  }

  // --- reading phase ----------------------------------------------------

  previous(): void {
    this.index.update((i) => Math.max(0, i - 1));
    this.enterCurrentItem();
  }

  next(): void {
    this.index.update((i) => Math.min(this.items().length - 1, i + 1));
    this.enterCurrentItem();
  }

  /** A tile click (issue #87) -- same visited bookkeeping as stepping there
   *  one item at a time. */
  protected jumpTo(i: number): void {
    this.index.set(Math.max(0, Math.min(this.items().length - 1, i)));
    this.enterCurrentItem();
  }

  /** Every move to another item (Back, Next, a tile, "I know this one")
   *  starts it from the top, so it never opens halfway down the previous
   *  item's mnemonics -- with the sticky bar, Next is reachable from
   *  anywhere on the page (issue #87). */
  private enterCurrentItem(): void {
    if (typeof window !== 'undefined' && window.scrollY > 0) {
      window.scrollTo({ top: 0 });
    }
    const id = this.current()?.subject.id;
    if (id === undefined) {
      return;
    }
    this.visited.update((set) => (set.has(id) ? set : new Set(set).add(id)));
  }

  /** The last item's "Continue to quiz" button -- the new `gate` phase
   * (issue #38) sits between reading a batch and being quizzed on it. */
  protected goToQuizGate(): void {
    this.phase.set('gate');
  }

  protected backToReading(): void {
    this.phase.set('reading');
  }

  /** Leave the gate and build the quiz queue for this batch. */
  protected startQuiz(): void {
    if (this.runStartedAt === 0) {
      this.runStartedAt = Date.now();
    }
    const cards = this.items().map((item) =>
      shuffleQuestions({
        subject: item.subject,
        questions: questionsFor(item.subject.object_type),
      }),
    );
    this.queue.set(cards);
    // Corrects this batch's size for any "I know this" during reading --
    // an item declared known there never reaches `cards` above.
    const exactSize = cards.reduce((sum, c) => sum + c.questions.length, 0);
    const i = this.chunkIndex();
    this.batches.update((batches) =>
      batches.map((batch, j) => (j === i ? { size: exactSize, done: 0 } : batch)),
    );
    this.value.set('');
    this.feedback.set(null);
    this.phase.set('quiz');
  }

  // --- quiz phase -------------------------------------------------------

  protected onSubmitted(answer: string): void {
    void this.submitQuizAnswer(answer, false);
  }

  /** Alt+H: reveal the answer, scored as a plain miss -- the quiz still
   * moves nothing either way. */
  protected onGaveUp(): void {
    void this.submitQuizAnswer('', true);
  }

  /** Editing while a retry is up withdraws it -- the quiz has no `held`. */
  protected onEdited(): void {
    this.feedback.update((result) => (result?.retry ? null : result));
  }

  private async submitQuizAnswer(answer: string, gaveUp: boolean): Promise<void> {
    const card = this.card();
    const question = this.question();
    if (!card || !question || this.busy()) {
      return;
    }

    this.busy.set(true);
    try {
      const result = await this.api.quiz(card.subject.id, question, answer, gaveUp);
      this.feedback.set(result);
      if (!result.retry) {
        this.runAnswered.update((n) => n + 1);
        if (result.correct) {
          this.runCorrect.update((n) => n + 1);
          const i = this.chunkIndex();
          this.batches.update((batches) =>
            batches.map((batch, j) => (j === i ? { ...batch, done: batch.done + 1 } : batch)),
          );
        }
      }
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  /** Move past the feedback; a miss, a retry or a remaining half is
   * reinserted later in the queue, not sent to the back (see `reinsert` in
   * review-queue.ts -- same reasoning as the review screen's `next()`,
   * issue #27). A retry is treated exactly like a miss here -- it is
   * reinserted rather than re-asked immediately, unlike in reviews. */
  protected advance(): void {
    const result = this.feedback();
    const card = this.card();
    if (!result || !card) {
      return;
    }

    let rest = this.queue().slice(1);
    if (result.correct) {
      const remaining = card.questions.slice(1);
      if (remaining.length > 0) {
        rest = reinsert(rest, { ...card, questions: remaining });
      }
    } else {
      rest = reinsert(rest, card);
    }

    this.queue.set(rest);
    this.feedback.set(null);
    this.value.set('');

    if (rest.length === 0) {
      void this.commit();
    }
  }

  /** This chunk has been produced at least once; put it into the SRS and
   * move on to the next chunk, or wrap up the run. */
  private async commit(): Promise<void> {
    this.busy.set(true);
    try {
      await this.api.startLessons(this.items().map((item) => item.subject.id));
      await this.advanceChunk();
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  // --- the override -----------------------------------------------------

  /** "I know this one" for the item on screen -- skips the lesson entirely.
   *
   * Drops the item from the current chunk in place rather than reloading it,
   * so working through the rest of the chunk keeps the reader where they
   * were. If that empties the chunk, move on exactly as a commit would.
   */
  async markCurrentKnown(): Promise<void> {
    const item = this.current();
    if (!item) {
      return;
    }

    this.busy.set(true);
    try {
      await this.api.markKnown([item.subject.id]);
      this.spendLocally(1);

      const rest = this.items().filter((entry) => entry.subject.id !== item.subject.id);
      if (rest.length === 0) {
        await this.advanceChunk();
        return;
      }

      this.items.set(rest);
      // Clamp for the case where the declared item was the last one.
      this.index.update((i) => Math.min(i, rest.length - 1));
      this.enterCurrentItem();
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  /** The fast path through a chunk already known from elsewhere. */
  async markChunkKnown(): Promise<void> {
    const ids = this.items().map((item) => item.subject.id);
    if (ids.length === 0 || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      await this.api.markKnown(ids);
      this.spendLocally(ids.length);
      await this.advanceChunk();
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  // --- presentation -----------------------------------------------------

  /** The "I already know all N" info popover's button label (issue #87) --
   *  one constant, since the button is rendered twice (mobile and desktop
   *  bar, see lessons.html) but means the same thing either way. */
  protected readonly knowAllInfoLabel = 'What does "I already know all" do?';

  protected typeLabel(type: ObjectType): string {
    return {
      radical: 'Radical',
      kanji: 'Kanji',
      vocabulary: 'Vocabulary',
      kana_vocabulary: 'Vocabulary (kana)',
    }[type];
  }

  protected toneFor(type: ObjectType): string {
    if (type === 'radical') {
      return 'var(--radical)';
    }
    return type === 'kanji' ? 'var(--kanji)' : 'var(--vocabulary)';
  }

  /** Keep the current item's synonyms current after a save in the reading
   * phase -- same reasoning as `Review.onSynonymsChange`. */
  protected onSynonymsChange(subjectId: number, synonyms: string[]): void {
    this.items.update((items) =>
      items.map((item) =>
        item.subject.id === subjectId
          ? { ...item, subject: { ...item.subject, synonyms } }
          : item,
      ),
    );
  }

  protected meanings(subject: SubjectDetail): string {
    return subject.meanings
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

}

/**
 * Which questions the quiz asks, mirroring `REQUIRED_QUESTIONS` in srs.py.
 *
 * Duplicated rather than fetched: the backend rejects anything it did not ask
 * for, so a drift here shows up as a 409 rather than as a wrong item entering
 * the rotation.
 */
function questionsFor(type: ObjectType): QuestionType[] {
  return type === 'radical' || type === 'kana_vocabulary'
    ? ['meaning']
    : ['meaning', 'reading'];
}
