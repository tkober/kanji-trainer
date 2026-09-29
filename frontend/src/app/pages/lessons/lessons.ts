import {
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';

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
import { glyphCount } from '../../core/glyphs';
import { HoldFocus } from '../../core/hold-focus';
import { absorbInput, finaliseKana, isKana, romajiToKana } from '../../core/kana';
import { Mnemonic } from '../../core/mnemonic';
import { type ReadingGroup, readingGroups } from '../../core/readings';
import { RadicalIllustration } from '../../shared/radical-illustration/radical-illustration';

/** One item in the quiz, with the questions it still owes. */
interface QuizCard {
  subject: SubjectDetail;
  questions: QuestionType[];
}

type Phase = 'select' | 'reading' | 'quiz';

/** One of the three tile groups the selection view is organised into. */
interface TileGroup {
  key: 'radical' | 'kanji' | 'vocabulary';
  label: string;
  tiles: LessonTile[];
}

const BATCHING_KEY = 'kt-lesson-batching';

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

function stageName(stage: number): string {
  return STAGE_LABELS[Math.max(0, Math.min(9, stage))];
}

type Band = 'lesson' | 'apprentice' | 'guru' | 'master' | 'enlightened' | 'burned';

function stageBand(stage: number): Band {
  if (stage <= 0) return 'lesson';
  if (stage <= 4) return 'apprentice';
  if (stage <= 6) return 'guru';
  if (stage === 7) return 'master';
  if (stage === 8) return 'enlightened';
  return 'burned';
}

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

/**
 * Lessons: pick items from a level's tiles, read them, then be quizzed, then
 * into the SRS.
 *
 * The picker is the point: WaniKani hands over a fixed batch in a fixed
 * order, and the one thing this app exists to add is the override. Letting
 * the learner choose exactly which items to spend a session on — including
 * skipping ones already known from elsewhere — is that override applied to
 * lessons instead of just to reviews.
 *
 * The quiz stays the gate it always was: nothing here goes to the SRS without
 * having been produced once. Failing it costs nothing — the card goes to the
 * back of the current batch's queue. `/api/lessons/quiz` has no side effects,
 * and only `/api/lessons/start` moves anything, one batch at a time.
 */
@Component({
  selector: 'app-lessons',
  imports: [HoldFocus, Mnemonic, RadicalIllustration, RouterLink],
  templateUrl: './lessons.html',
  styleUrl: './lessons.scss',
})
export class LessonsPage {
  private readonly api = inject(Api);
  private readonly counters = inject(Counters);
  private readonly field = viewChild<ElementRef<HTMLInputElement>>('answerField');

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

  protected readonly queue = signal<QuizCard[]>([]);
  protected readonly raw = signal('');
  protected readonly feedback = signal<QuizResult | null>(null);
  protected readonly passed = signal(0);

  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly current = computed(() => this.items()[this.index()] ?? null);
  protected readonly card = computed(() => this.queue()[0] ?? null);
  protected readonly question = computed<QuestionType | null>(
    () => this.card()?.questions[0] ?? null,
  );
  protected readonly onLastItem = computed(() => this.index() >= this.items().length - 1);

  /** Readings convert as they are typed; meanings are left as entered. */
  protected readonly display = computed(() => {
    const value = this.raw();
    if (this.question() !== 'reading' || isKana(value)) {
      return value;
    }
    return romajiToKana(value);
  });

  /** See the identical effect in review.ts — same reason, same shape, and the
   * same reason for holding the caret through the feedback: the quiz is typed
   * on a phone too. */
  private readonly keepFocus = effect(() => {
    this.question();
    this.feedback();
    this.field()?.nativeElement.focus();
  });

  constructor() {
    void this.load();
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
    } catch (err) {
      this.error.set((err as Error).message);
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
   * showing — see `Stats.lessons_available` on the backend. So the exact
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

  pickLevel(event: Event): void {
    const value = Number((event.target as HTMLSelectElement).value);
    void this.load(Number.isFinite(value) ? value : null);
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
    this.items.set(this.chunks()[i] ?? []);
    this.index.set(0);
    this.phase.set('reading');
    this.queue.set([]);
    this.feedback.set(null);
    this.raw.set('');
    this.passed.set(0);
  }

  /** Commit the current chunk, or wrap up the run if it was the last one. */
  private async advanceChunk(): Promise<void> {
    const next = this.chunkIndex() + 1;
    if (next < this.chunks().length) {
      this.chunkIndex.set(next);
      this.openChunk(next);
      return;
    }
    this.chunks.set([]);
    this.chunkIndex.set(0);
    await this.load();
  }

  /** Keep the level's own numbers roughly honest between full reloads,
   * without disturbing a run in progress. A full `load()` at the end of the
   * run (see `advanceChunk`) reconciles everything exactly, tiles included. */
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
  }

  next(): void {
    this.index.update((i) => Math.min(this.items().length - 1, i + 1));
  }

  /** Leave the reading phase and build the quiz queue for this batch. */
  startQuiz(): void {
    this.queue.set(
      this.items().map((item) => ({
        subject: item.subject,
        questions: questionsFor(item.subject.object_type),
      })),
    );
    this.passed.set(0);
    this.raw.set('');
    this.feedback.set(null);
    this.phase.set('quiz');
    this.focus();
  }

  // --- quiz phase -------------------------------------------------------

  onInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    // Frozen while the feedback is up, and not with `readonly` — see the same
    // handler in review.ts.
    if (this.feedback()) {
      input.value = this.display();
      return;
    }
    this.raw.set(absorbInput(input.value, this.display(), this.raw()));
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Enter') {
      return;
    }
    event.preventDefault();
    if (this.feedback()) {
      this.advance();
    } else {
      void this.submit();
    }
  }

  async submit(): Promise<void> {
    const card = this.card();
    const question = this.question();
    if (!card || !question || this.busy()) {
      return;
    }

    const answer = question === 'reading' ? finaliseKana(this.display()) : this.raw().trim();
    if (!answer) {
      return;
    }

    this.busy.set(true);
    try {
      this.feedback.set(await this.api.quiz(card.subject.id, question, answer));
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  /** Move past the feedback; a miss sends the card to the back of the queue. */
  advance(): void {
    const result = this.feedback();
    const card = this.card();
    if (!result || !card) {
      return;
    }

    const rest = this.queue().slice(1);
    if (result.correct) {
      const remaining = card.questions.slice(1);
      if (remaining.length > 0) {
        rest.push({ ...card, questions: remaining });
      } else {
        this.passed.update((n) => n + 1);
      }
    } else {
      rest.push(card);
    }

    this.queue.set(rest);
    this.feedback.set(null);
    this.raw.set('');

    if (rest.length === 0) {
      void this.commit();
    } else {
      this.focus();
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

  /** "I know this one" for the item on screen — skips the lesson entirely.
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

  protected typeLabel(type: ObjectType): string {
    return {
      radical: 'Radical',
      kanji: 'Kanji',
      vocabulary: 'Vocabulary',
      kana_vocabulary: 'Vocabulary (kana)',
    }[type];
  }

  /** Bound to `.characters` as `--glyphs`, so its font shrinks to fit the card. */
  protected glyphCount(text: string): number {
    return glyphCount(text);
  }

  protected meanings(subject: SubjectDetail): string {
    return subject.meanings
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  protected readings(subject: SubjectDetail): ReadingGroup[] {
    return readingGroups(subject.readings);
  }

  protected band(stage: number): Band {
    return stageBand(stage);
  }

  protected tileLabel(tile: LessonTile): string {
    const glyph = tile.subject.characters ?? tile.subject.slug;
    let label = `${glyph} — ${stageName(tile.srs_stage)}`;
    if (tile.state === 'known') {
      label += ' (marked known)';
    } else if (tile.state === 'suspended') {
      label += ' (suspended)';
    }
    return label;
  }

  private focus(): void {
    this.field()?.nativeElement.focus();
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
