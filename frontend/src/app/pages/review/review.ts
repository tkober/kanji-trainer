import { DatePipe } from '@angular/common';
import { Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api } from '../../core/api';
import type { AnswerResult, ObjectType, QuestionType, QueueItem } from '../../core/api.types';
import { finaliseKana, isKana, romajiToKana } from '../../core/kana';

/** A queue entry plus what it still owes. */
type Card = QueueItem;

@Component({
  selector: 'app-review',
  imports: [DatePipe, RouterLink],
  templateUrl: './review.html',
  styleUrl: './review.scss',
})
export class Review {
  private readonly api = inject(Api);
  private readonly field = viewChild<ElementRef<HTMLInputElement>>('answerField');

  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly queue = signal<Card[]>([]);
  protected readonly totalDue = signal(0);
  protected readonly feedback = signal<AnswerResult | null>(null);
  protected readonly raw = signal('');
  protected readonly submitting = signal(false);
  /**
   * An answer that would be wrong, held back for one keypress.
   *
   * The server has not applied anything and has deliberately not said what the
   * right answer is — the question is still open, and a warning that revealed
   * it would be a reveal button with extra steps.
   */
  protected readonly held = signal(false);
  protected readonly shake = signal(false);

  protected readonly answered = signal(0);
  protected readonly correct = signal(0);

  protected readonly current = computed(() => this.queue()[0] ?? null);
  protected readonly question = computed<QuestionType | null>(
    () => this.current()?.questions[0] ?? null,
  );

  /**
   * What goes in the box. A reading is converted as it is typed — seeing かん
   * appear while typing "kan" is half the feedback of a reading question — a
   * meaning is left exactly as entered.
   */
  protected readonly display = computed(() => {
    const value = this.raw();
    if (this.question() !== 'reading' || isKana(value)) {
      return value;
    }
    return romajiToKana(value);
  });

  protected readonly done = computed(() => !this.loading() && this.queue().length === 0);

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const queue = await this.api.reviewQueue(100);
      this.queue.set(queue.items);
      this.totalDue.set(queue.total_due);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
      this.focus();
    }
  }

  onInput(event: Event): void {
    this.raw.set((event.target as HTMLInputElement).value);
    // Editing withdraws the answer that was warned about; the next Enter is
    // checked afresh rather than submitting the old text.
    if (this.held()) {
      this.held.set(false);
    }
  }

  /** Enter submits, and once there is feedback on screen, Enter moves on. */
  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && this.held()) {
      // Hand the input back with the text selected, so correcting a slip is
      // one keystroke rather than a clear-and-retype.
      event.preventDefault();
      this.held.set(false);
      const input = this.field()?.nativeElement;
      input?.focus();
      input?.select();
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      if (this.feedback()) {
        this.next();
      } else {
        void this.submit();
      }
      return;
    }
    // Alt rather than a bare key: the field has focus the whole time, so any
    // unmodified shortcut would be swallowed by the answer.
    if (event.altKey && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      void this.markKnown();
    }
  }

  async submit(): Promise<void> {
    const card = this.current();
    const question = this.question();
    if (!card || !question || this.submitting()) {
      return;
    }

    const answer = question === 'reading' ? finaliseKana(this.display()) : this.raw().trim();
    if (!answer) {
      return;
    }

    this.submitting.set(true);
    try {
      // The second Enter after a warning is what confirms it.
      const result = await this.api.answer(card.subject.id, question, answer, this.held());

      if (result.held) {
        this.held.set(true);
        this.nudge();
        return;
      }
      this.held.set(false);

      this.feedback.set(result);

      // A retry is not an answer: the learner produced a real reading of the
      // character, just not the one asked for. It counts for nothing in
      // either direction, so it stays out of the session tally too.
      if (!result.retry) {
        this.answered.update((n) => n + 1);
        if (result.correct) {
          this.correct.update((n) => n + 1);
        }
      }
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.submitting.set(false);
    }
  }

  /** Briefly shake the field, so a held answer is felt as well as read. */
  private nudge(): void {
    this.shake.set(true);
    setTimeout(() => this.shake.set(false), 400);
    queueMicrotask(() => this.field()?.nativeElement.focus());
  }

  /** Advance past the feedback, re-queueing the item if it still owes a half. */
  next(): void {
    const result = this.feedback();
    const card = this.current();
    if (!result || !card) {
      return;
    }

    if (result.retry) {
      // Same question, same item, nothing consumed — just ask again.
      this.feedback.set(null);
      this.raw.set('');
      this.focus();
      return;
    }

    const rest = this.queue().slice(1);
    if (result.remaining.length > 0) {
      // To the back rather than straight round again: answering the reading
      // immediately after being shown the meaning tests nothing.
      rest.push({ ...card, questions: result.remaining });
    }

    this.queue.set(rest);
    this.feedback.set(null);
    this.raw.set('');
    this.held.set(false);
    this.focus();
  }

  /**
   * "Das kann ich", mid-session.
   *
   * Takes the item out of the queue entirely rather than marking the current
   * question right: the point is that the whole item is known, and asking for
   * its other half would be the exact friction this button removes.
   */
  async markKnown(): Promise<void> {
    const card = this.current();
    if (!card) {
      return;
    }
    try {
      await this.api.markKnown([card.subject.id]);
      this.queue.set(this.queue().slice(1));
      this.feedback.set(null);
      this.raw.set('');
      this.held.set(false);
      this.totalDue.update((n) => Math.max(0, n - 1));
      this.focus();
    } catch (err) {
      this.error.set((err as Error).message);
    }
  }

  /** Put a wrongly-answered item back to Apprentice I and move on. */
  async resetCurrent(): Promise<void> {
    const card = this.current();
    if (!card) {
      return;
    }
    try {
      await this.api.resetItems([card.subject.id]);
      this.queue.set(this.queue().slice(1));
      this.feedback.set(null);
      this.raw.set('');
      this.held.set(false);
      this.focus();
    } catch (err) {
      this.error.set((err as Error).message);
    }
  }

  protected accuracy(): number | null {
    return this.answered() === 0 ? null : Math.round((this.correct() / this.answered()) * 100);
  }

  protected typeLabel(type: ObjectType): string {
    return {
      radical: 'Radikal',
      kanji: 'Kanji',
      vocabulary: 'Vokabel',
      kana_vocabulary: 'Vokabel (Kana)',
    }[type];
  }

  protected primaryReadings(result: AnswerResult): string {
    return (result.subject?.readings ?? [])
      .filter((reading) => reading.accepted_answer !== false)
      .map((reading) => reading.reading)
      .join('、');
  }

  protected primaryMeanings(result: AnswerResult): string {
    return (result.subject?.meanings ?? [])
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  private focus(): void {
    // Deferred: the input is inside a @if that has not rendered yet when this
    // runs straight after a state change.
    queueMicrotask(() => this.field()?.nativeElement.focus());
  }
}
