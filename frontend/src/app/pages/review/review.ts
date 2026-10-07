import { DatePipe } from '@angular/common';
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
import type { AnswerResult, ObjectType, QuestionType, QueueItem } from '../../core/api.types';
import { Counters } from '../../core/counters';
import { glyphCount } from '../../core/glyphs';
import { HoldFocus } from '../../core/hold-focus';
import { absorbInput, finaliseKana, isKana, romajiToKana } from '../../core/kana';
import { Mnemonic } from '../../core/mnemonic';
import { reinsert, shuffleQuestions } from '../../core/review-queue';
import { type Hotkey, Hotkeys } from '../../shared/hotkeys/hotkeys';
import { ContextSentences } from '../../shared/context-sentences/context-sentences';
import { RadicalIllustration } from '../../shared/radical-illustration/radical-illustration';
import { Readings } from '../../shared/readings/readings';

/** A queue entry plus what it still owes. */
type Card = QueueItem;

@Component({
  selector: 'app-review',
  imports: [
    ContextSentences,
    DatePipe,
    HoldFocus,
    Hotkeys,
    Mnemonic,
    RadicalIllustration,
    Readings,
    RouterLink,
  ],
  templateUrl: './review.html',
  styleUrl: './review.scss',
})
export class Review {
  private readonly api = inject(Api);
  private readonly counters = inject(Counters);
  private readonly field = viewChild<ElementRef<HTMLInputElement>>('answerField');

  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly queue = signal<Card[]>([]);
  /**
   * The same number the header badge shows, because it is the same number.
   *
   * Kept as its own signal here it drifted from the badge within one
   * answer: "37 due in total" next to a badge reading 10.
   */
  protected readonly totalDue = this.counters.due;
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

  /**
   * "I know this" was pressed while the item's other half is still open.
   *
   * `mark_known` settles meaning and reading together, which is invisible from
   * whichever half happens to be on screen — set once, by `requestKnown()`,
   * to ask before it is irreversible rather than after.
   */
  protected readonly confirmKnown = signal(false);

  /**
   * Whether the "Show item" details block is expanded.
   *
   * Controlled rather than left to the `<details>` element itself, because
   * `F` needs to toggle it from `onKeydown` and the element must start
   * collapsed again on the next item — a plain uncontrolled `<details>` has
   * no signal to reset.
   */
  protected readonly showItem = signal(false);
  protected readonly hotkeysOpen = signal(false);

  /** Rows for the `app-hotkeys` flyout, in the order they read best. */
  protected readonly hotkeys: Hotkey[] = [
    { keys: ['Enter'], label: 'Submit answer / next item' },
    { keys: ['Esc'], label: 'Edit a held answer' },
    { keys: ['Alt', 'K'], label: 'I know this (press twice for the whole item)' },
    { keys: ['F'], label: 'Show item info (after answering)' },
    { keys: ['?'], label: 'Toggle this menu (after answering)' },
  ];

  protected readonly answered = signal(0);
  protected readonly correct = signal(0);

  protected readonly current = computed(() => this.queue()[0] ?? null);
  protected readonly question = computed<QuestionType | null>(
    () => this.current()?.questions[0] ?? null,
  );

  /**
   * Whether the item's *other* question is still outstanding.
   *
   * `card.questions` is the order fixed when the item was served and goes
   * stale the moment an answer lands, so once feedback is up the true
   * picture is `remaining` from that answer instead — except after a `retry`,
   * which leaves the current question open too (nothing was consumed), so it
   * reads exactly like no feedback at all.
   */
  protected readonly otherHalfOpen = computed(() => {
    const card = this.current();
    if (!card) {
      return false;
    }
    const result = this.feedback();
    if (result && !result.retry) {
      return result.remaining.length > 0;
    }
    return card.questions.length > 1;
  });

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

  /**
   * Keep the caret in the answer field, always.
   *
   * An `effect` rather than a call after each state change: the field lives
   * inside `@if` blocks, so right after `loading` flips or the queue advances
   * it is not in the DOM yet and a focus call lands on nothing. The viewChild
   * signal updates once it *is* rendered, and reading the question here makes
   * the effect re-run for every new prompt.
   *
   * Feedback no longer hands the caret back. On a desktop that was invisible;
   * on a phone the caret is the keyboard, and one that closes on every answer
   * and has to be re-opened by tapping the field is the difference between
   * reviewing on the sofa and not reviewing at all. Focus is cheapest to keep,
   * so the field holds it from the first item to the last — see `HoldFocus`
   * for the other half, and `onInput` for what stops a locked field being
   * typed into.
   */
  private readonly keepFocus = effect(() => {
    this.question();
    this.feedback();
    this.field()?.nativeElement.focus();
  });

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const queue = await this.api.reviewQueue(100);
      // The backend always hands back `['meaning', 'reading']` — shuffle each
      // item's own half-order here, or the first sighting of every item is
      // always its meaning. A no-op for an item already mid-review (it
      // arrives with only its outstanding half left).
      this.queue.set(queue.items.map(shuffleQuestions));
      this.counters.setDue(queue.total_due);
      this.confirmKnown.set(false);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
      this.focus();
    }
  }

  onInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    // The answer is frozen while its feedback is up, and frozen here rather
    // than with `readonly`: a read-only field is one the on-screen keyboard
    // retracts from, which is exactly the blur this screen is built to avoid.
    // The keystroke is dropped and the box put back the way it was — the
    // binding cannot do it, since the value it holds has not changed.
    if (this.feedback()) {
      input.value = this.display();
      return;
    }

    this.raw.set(absorbInput(input.value, this.display(), this.raw()));
    // Editing withdraws the answer that was warned about; the next Enter is
    // checked afresh rather than submitting the old text.
    if (this.held()) {
      this.held.set(false);
    }
    // Same reasoning as `held`: typing again means the learner changed their
    // mind, and a pending "mark the whole item known?" must not survive that
    // — or they would be stuck re-reading a panel for an answer they are now
    // busy typing.
    if (this.confirmKnown()) {
      this.confirmKnown.set(false);
    }
  }

  /** Enter submits, and once there is feedback on screen, Enter moves on. */
  onKeydown(event: KeyboardEvent): void {
    // The confirmation panel is the most recently raised prompt on screen, so
    // Escape clears it before anything else gets a turn — including a held
    // answer, which can be showing at the same time (nothing about pressing
    // Alt+K requires the previous answer to not be held).
    if (event.key === 'Escape' && this.confirmKnown()) {
      event.preventDefault();
      this.confirmKnown.set(false);
      return;
    }

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

    // A held answer takes priority over the flyout: correcting a slip is the
    // more urgent thing Escape can do, so the flyout only closes on its own
    // once nothing is held.
    if (event.key === 'Escape' && this.hotkeysOpen()) {
      event.preventDefault();
      this.hotkeysOpen.set(false);
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      // Confirming takes priority over both of Enter's usual jobs — it must
      // not submit the answer underneath the panel, and the panel can be up
      // with no feedback showing at all.
      if (this.confirmKnown()) {
        void this.markKnown();
      } else if (this.feedback()) {
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
      this.requestKnown();
      return;
    }

    // Bare-key shortcuts below fire only once feedback is on screen. Before
    // that the field has focus and every keystroke is part of the answer —
    // `onInput` only starts discarding keystrokes once feedback is set, so a
    // bare key here would otherwise land in the answer, not trigger anything.
    const result = this.feedback();
    if (!result || event.ctrlKey || event.altKey || event.metaKey || event.isComposing) {
      return;
    }

    if (event.key.toLowerCase() === 'f' && result.subject) {
      event.preventDefault();
      this.showItem.update((open) => !open);
      return;
    }

    if (event.key === '?') {
      event.preventDefault();
      this.hotkeysOpen.update((open) => !open);
    }
  }

  /** Keeps `showItem` in sync when the `<summary>` is clicked directly. */
  protected onShowItemToggle(event: Event): void {
    this.showItem.set((event.target as HTMLDetailsElement).open);
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

      // Completed means the item has moved and its next review is hours
      // away: it has left the due set, and the badge should say so now
      // rather than at the next poll. A half-answered item has not moved.
      if (result.completed) {
        this.counters.spendDue();
      }

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
      this.showItem.set(false);
      this.raw.set('');
      this.focus();
      return;
    }

    let rest = this.queue().slice(1);
    if (result.remaining.length > 0) {
      // Somewhere later in the round, not straight back round again and not
      // at the very back either: a straight re-insert tests nothing (the
      // meaning just answered is still on screen), and a straight push to
      // the back regroups every reading into one block at the end of the
      // round once enough items have gone through this once (issue #27).
      rest = reinsert(rest, { ...card, questions: result.remaining });
    }

    this.queue.set(rest);
    this.feedback.set(null);
    this.showItem.set(false);
    this.raw.set('');
    this.held.set(false);
    this.confirmKnown.set(false);
    this.continueRound();
  }

  /**
   * Hand the caret back, or fetch the next round.
   *
   * A round is one page of the queue, and a long backlog has several. Running
   * a page out used to end the session on "No reviews due" while the badge
   * went on counting the rest, and the reload it wanted was a button the
   * learner had to notice and press.
   */
  private continueRound(): void {
    if (this.queue().length === 0 && this.totalDue() > 0) {
      void this.load();
      return;
    }
    this.focus();
  }

  /**
   * Entry point for the "I know this" button and `Alt+K`.
   *
   * Goes straight to `markKnown()` when there is nothing left to clarify (a
   * radical, or the other half already answered); otherwise the first press
   * only raises the confirmation panel, and a second press — same button,
   * same shortcut, or `Enter` — is what actually calls `markKnown()`. That
   * keeps the common case (nothing outstanding) exactly as fast as before.
   */
  protected requestKnown(): void {
    if (this.confirmKnown()) {
      void this.markKnown();
      return;
    }
    if (this.otherHalfOpen()) {
      this.confirmKnown.set(true);
      return;
    }
    void this.markKnown();
  }

  /** Cancel button for the confirmation panel — keeps focus and typed text. */
  protected cancelKnown(): void {
    this.confirmKnown.set(false);
  }

  /**
   * "I know this", mid-session.
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
      this.showItem.set(false);
      this.raw.set('');
      this.held.set(false);
      this.confirmKnown.set(false);
      this.counters.spendDue();
      this.continueRound();
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
      this.showItem.set(false);
      this.raw.set('');
      this.held.set(false);
      this.confirmKnown.set(false);
      // Back to Apprentice I is four hours out, so this one has left the due
      // set as surely as an answered item has.
      this.counters.spendDue();
      this.continueRound();
    } catch (err) {
      this.error.set((err as Error).message);
    }
  }

  protected accuracy(): number | null {
    return this.answered() === 0 ? null : Math.round((this.correct() / this.answered()) * 100);
  }

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

  protected primaryMeanings(result: AnswerResult): string {
    return (result.subject?.meanings ?? [])
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  /**
   * Focus now, for the cases the effect cannot see.
   *
   * The effect covers every change of question. This covers the rest: the
   * field is already rendered and already the right one, it just lost the
   * caret to a button click.
   */
  private focus(): void {
    this.field()?.nativeElement.focus();
  }
}
