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
  LevelSummary,
  ObjectType,
  QuestionType,
  QuizResult,
  SubjectDetail,
} from '../../core/api.types';
import { Counters } from '../../core/counters';
import { absorbInput, finaliseKana, isKana, romajiToKana } from '../../core/kana';
import { Mnemonic } from '../../core/mnemonic';
import { type ReadingGroup, readingGroups } from '../../core/readings';

/** One item in the quiz, with the questions it still owes. */
interface QuizCard {
  subject: SubjectDetail;
  questions: QuestionType[];
}

type Phase = 'reading' | 'quiz';

/**
 * Lessons: read the batch, then be quizzed on it, then into the SRS.
 *
 * The quiz is the point. Without it a batch went straight to Apprentice I on
 * a button press, so items entered the rotation that the learner had read but
 * never once produced — and the first actual retrieval happened four hours
 * later, with no mnemonic in sight.
 *
 * Failing costs nothing: the card goes to the back of the queue and comes
 * round until it is answered. Nothing here touches the SRS — `/api/lessons/quiz`
 * has no side effects, and only the commit at the end moves anything.
 */
@Component({
  selector: 'app-lessons',
  imports: [Mnemonic, RouterLink],
  templateUrl: './lessons.html',
  styleUrl: './lessons.scss',
})
export class LessonsPage {
  private readonly api = inject(Api);
  private readonly counters = inject(Counters);
  private readonly field = viewChild<ElementRef<HTMLInputElement>>('answerField');

  protected readonly phase = signal<Phase>('reading');
  protected readonly items = signal<LessonItem[]>([]);
  protected readonly index = signal(0);

  protected readonly level = signal<number | null>(null);
  protected readonly totalInLevel = signal(0);
  protected readonly totalAvailable = signal(0);
  protected readonly levels = signal<LevelSummary[]>([]);
  protected readonly dailyLimit = signal(0);

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

  /** See the identical effect in review.ts — same reason, same shape. */
  private readonly keepFocus = effect(() => {
    const input = this.field()?.nativeElement;
    this.question();
    const answered = this.feedback() !== null;
    if (input && !answered) {
      input.focus();
    }
  });

  constructor() {
    void this.load();
  }

  async load(level?: number | null): Promise<void> {
    this.loading.set(true);
    try {
      const lessons = await this.api.lessons(level ?? this.level());
      this.items.set(lessons.items);
      this.level.set(lessons.level);
      this.totalInLevel.set(lessons.total_in_level);
      // What the badge counts, and the one number here that is already it.
      this.counters.setLessons(lessons.total_in_level);
      this.totalAvailable.set(lessons.total_available);
      this.levels.set(lessons.levels);
      this.dailyLimit.set(lessons.daily_limit);
      this.index.set(0);
      this.phase.set('reading');
      this.queue.set([]);
      this.feedback.set(null);
      this.raw.set('');
      this.passed.set(0);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  pickLevel(event: Event): void {
    const value = Number((event.target as HTMLSelectElement).value);
    void this.load(Number.isFinite(value) ? value : null);
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
    this.raw.set(
      absorbInput((event.target as HTMLInputElement).value, this.display(), this.raw()),
    );
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

  /** The whole batch has been produced at least once; put it into the SRS. */
  private async commit(): Promise<void> {
    this.busy.set(true);
    try {
      await this.api.startLessons(this.items().map((item) => item.subject.id));
      await this.load();
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  // --- the override -----------------------------------------------------

  /** "I know this one" for the item on screen — skips the lesson entirely.
   *
   * Drops the item from the batch in place rather than reloading it. A reload
   * refetches the whole batch *and* resets the index to 0, so declaring the
   * second of five sent you back to the first — every time, which made
   * working through a batch impossible. Keeping the index means the slot now
   * holds whatever came next, which is where the reader already was.
   */
  async markCurrentKnown(): Promise<void> {
    const item = this.current();
    if (!item) {
      return;
    }

    this.busy.set(true);
    try {
      await this.api.markKnown([item.subject.id]);

      const rest = this.items().filter((entry) => entry.subject.id !== item.subject.id);
      if (rest.length === 0) {
        // Nothing left to read; fetching the next batch is the only move.
        await this.load();
        return;
      }

      this.items.set(rest);
      // Clamp for the case where the declared item was the last one.
      this.index.update((i) => Math.min(i, rest.length - 1));
      this.countDeclared(1);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  /** Keep the open-lesson counters honest without a round trip. */
  private countDeclared(n: number): void {
    this.counters.spendLessons(n);
    this.totalInLevel.update((value) => Math.max(0, value - n));
    this.totalAvailable.update((value) => Math.max(0, value - n));
    this.levels.update((entries) =>
      entries
        .map((entry) =>
          entry.level === this.level()
            ? { ...entry, open_count: Math.max(0, entry.open_count - n) }
            : entry,
        )
        .filter((entry) => entry.open_count > 0),
    );
  }

  /** The fast path through levels already finished once. */
  async markBatchKnown(): Promise<void> {
    await this.apply(() => this.api.markKnown(this.items().map((item) => item.subject.id)));
  }

  private async apply(action: () => Promise<unknown>): Promise<void> {
    this.busy.set(true);
    try {
      await action();
      await this.load();
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

  protected meanings(subject: SubjectDetail): string {
    return subject.meanings
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  protected readings(subject: SubjectDetail): ReadingGroup[] {
    return readingGroups(subject.readings);
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
