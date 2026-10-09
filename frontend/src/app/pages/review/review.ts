import { DatePipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SUMI_KEYS, SumiHotkeys, injectHotkey } from 'sumi-ui/core';
import {
  SumiEmptyState,
  SumiErrorState,
  SumiFocusModeDirective,
  SumiHanko,
  SumiPage,
  SumiShellFocusActionsDirective,
} from 'sumi-ui/layout';
import { SumiButtonDirective } from 'sumi-ui/forms';
import { SUMI_PRACTICE, type SumiVerdict, type SumiVerdictKind } from 'sumi-ui/practice';

import { Api } from '../../core/api';
import type {
  AnswerResult,
  ObjectType,
  QuestionType,
  QueueItem,
  SubjectSummary,
} from '../../core/api.types';
import { Counters } from '../../core/counters';
import { Mnemonic } from '../../core/mnemonic';
import { reinsert, shuffleQuestions } from '../../core/review-queue';
import { ContextSentences } from '../../shared/context-sentences/context-sentences';
import { RadicalIllustration } from '../../shared/radical-illustration/radical-illustration';
import { Readings } from '../../shared/readings/readings';
import { Synonyms } from '../../shared/synonyms/synonyms';

/** A queue entry plus what it still owes. */
type Card = QueueItem;

type Phase = 'idle' | 'active' | 'ended';

/**
 * Reviews: an SRS session, gated by a start screen and ended by a summary.
 *
 * `sumi-answer-field` and `sumi-verdict` own the input/feedback mechanics
 * that used to live here (see CLAUDE.md's "The answer field keeps the caret
 * for the whole item") -- this component is left with the review-specific
 * decisions: which question is being asked, what counts for the session
 * tally, and the three ways an answer can resolve (settled, held, retry) or
 * not resolve at all (given up, Alt+H).
 */
@Component({
  selector: 'app-review',
  imports: [
    ContextSentences,
    Mnemonic,
    RadicalIllustration,
    Readings,
    RouterLink,
    Synonyms,
    SumiButtonDirective,
    SumiEmptyState,
    SumiErrorState,
    SumiFocusModeDirective,
    SumiHanko,
    SumiPage,
    SumiShellFocusActionsDirective,
    ...SUMI_PRACTICE,
  ],
  providers: [DatePipe],
  templateUrl: './review.html',
  styleUrl: './review.scss',
})
export class Review {
  private readonly api = inject(Api);
  protected readonly counters = inject(Counters);
  private readonly hotkeys = inject(SumiHotkeys);
  private readonly datePipe = inject(DatePipe);

  protected readonly phase = signal<Phase>('idle');
  protected readonly loadFailed = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly queue = signal<Card[]>([]);
  /**
   * The same number the header badge shows, because it is the same number.
   * Also what the idle gate's text and the "N due in total" line read off.
   */
  protected readonly totalDue = this.counters.due;

  protected readonly feedback = signal<AnswerResult | null>(null);
  protected readonly value = signal('');
  protected readonly submitting = signal(false);
  /**
   * An answer that would be wrong, held back for one keypress.
   *
   * The server has not applied anything and has deliberately not said what
   * the right answer is -- the question is still open, and a warning that
   * revealed it would be a reveal button with extra steps.
   */
  protected readonly held = signal(false);
  /** The verdict on screen came from Alt+H, not from a typed answer. */
  protected readonly gaveUp = signal(false);

  /**
   * "I know this" was pressed while the item's other half is still open.
   *
   * `mark_known` settles meaning and reading together, which is invisible
   * from whichever half happens to be on screen -- set once, by
   * `requestKnown()`, to ask before it is irreversible rather than after.
   */
  protected readonly confirmKnown = signal(false);
  private confirmKnownUnregisters: Array<() => void> = [];

  /** Whether the verdict's details slot is expanded. Reset on every new item. */
  protected readonly detailsOpen = signal(false);

  protected readonly answered = signal(0);
  protected readonly correct = signal(0);
  private sessionStartedAt = 0;
  protected readonly sessionDurationMs = signal(0);
  /** `counters.level()` at the moment the session started, for `levelUp`. */
  private startLevel: number | null = null;

  protected readonly current = computed(() => this.queue()[0] ?? null);
  protected readonly question = computed<QuestionType | null>(
    () => this.current()?.questions[0] ?? null,
  );

  /**
   * Whether the item's *other* question is still outstanding.
   *
   * `card.questions` is the order fixed when the item was served and goes
   * stale the moment an answer lands, so once feedback is up the true
   * picture is `remaining` from that answer instead -- except after a
   * `retry`, which leaves the current question open too (nothing was
   * consumed), so it reads exactly like no feedback at all.
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

  protected readonly gateText = computed(
    () => `${this.totalDue()} reviews due. Meaning and reading are asked separately.`,
  );

  /** The field's own one-line verdict -- see docs/concept.md#eingabe and the
   * issue brief for the exact mapping. `sumi-verdict` below carries the
   * richer, settled-only feedback (expected answer, stage line, details). */
  protected readonly fieldVerdict = computed<SumiVerdict | null>(() => {
    if (this.held()) {
      return { kind: 'held', message: 'That looks wrong. Enter to submit anyway, Esc to edit.' };
    }
    const result = this.feedback();
    if (!result) {
      return null;
    }
    if (result.retry) {
      const card = this.current();
      // On vocabulary the retry is the kanji's reading (issue #41), which is
      // not a reading of this item; the hint carries that story on its own.
      const generic =
        card && card.subject.object_type !== 'vocabulary'
          ? 'That is a real reading of this character — just not the one asked for.'
          : undefined;
      return { kind: 'retry', message: result.hint ?? generic };
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

  /** `sumi-verdict`'s kind -- only for a settled correct/wrong, never for a
   * held or open retry (those are still mid-answer, not a verdict to review). */
  protected readonly settledKind = computed<SumiVerdictKind | null>(() => {
    if (this.held()) {
      return null;
    }
    const result = this.feedback();
    if (!result || result.retry) {
      return null;
    }
    return result.correct ? 'correct' : 'wrong';
  });

  /** The stage line ("Apprentice IV → Guru I · due again …", only once the
   * item has actually moved) plus the backend's hint, as one message. */
  protected readonly verdictMessage = computed<string | undefined>(() => {
    const result = this.feedback();
    const card = this.current();
    if (!result || result.retry) {
      return undefined;
    }
    const parts: string[] = [];
    if (result.hint) {
      parts.push(result.hint);
    }
    if (result.completed && card) {
      let line = `${card.stage_name} → ${result.stage_name_after}`;
      const due = result.next_review_at
        ? this.datePipe.transform(result.next_review_at, 'dd MMM, HH:mm')
        : null;
      if (due) {
        line += ` · due again ${due}`;
      }
      parts.push(line);
    }
    return parts.length > 0 ? parts.join(' ') : undefined;
  });

  /** Primary "Check"/"Next"/"Submit anyway"/"Try again" button label --
   * mirrors `sumi-answer-field`'s own Enter-label switching. */
  protected readonly checkButtonLabel = computed(() => {
    if (this.held()) {
      return 'Submit anyway';
    }
    const result = this.feedback();
    if (!result) {
      return 'Check';
    }
    return result.retry ? 'Try again' : 'Next';
  });

  private readonly sessionAccuracy = computed(() => {
    const answered = this.answered();
    return answered > 0 ? this.correct() / answered : 0;
  });

  /** 合格 ("passed") at 80% or above, 練習 ("practice") otherwise -- see
   * docs/concept.md#tuschemotive and sumi-ui#38's `sumi-hanko` example. */
  protected readonly hankoCharacters = computed(() => (this.sessionAccuracy() >= 0.8 ? '合格' : '練習'));
  protected readonly hankoLabel = computed(() => (this.sessionAccuracy() >= 0.8 ? 'Passed' : 'Practice'));

  /** Set once `counters.level()` (refreshed at session end) is higher than
   * it was when the session started. `undefined`, not a falsy level, so
   * `sumi-session-summary`'s `levelUp` input stays unset without a rise. */
  protected readonly levelUp = computed<string | undefined>(() => {
    const start = this.startLevel;
    const level = this.counters.level();
    if (start === null || level === null || level <= start) {
      return undefined;
    }
    return `Level ${level}`;
  });

  constructor() {
    // `?` only becomes a hotkey once a settled verdict is on screen -- bare
    // keys otherwise belong to the field. `F` is registered by
    // `sumi-verdict` itself as soon as its details slot has content.
    injectHotkey({
      keys: SUMI_KEYS.help,
      label: 'Toggle this menu (after answering)',
      scope: 'feedback',
      allowInEditable: true,
      enabled: () => this.settledKind() !== null,
      handler: () => this.hotkeys.toggleHelp(),
    });

    inject(DestroyRef).onDestroy(() => this.unregisterConfirmKnownHotkeys());
  }

  // --- the session ---------------------------------------------------------

  protected start(): void {
    this.loadFailed.set(false);
    this.error.set(null);
    this.answered.set(0);
    this.correct.set(0);
    this.sessionStartedAt = Date.now();
    this.startLevel = this.counters.level();
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const queue = await this.api.reviewQueue(100);
      // The backend always hands back `['meaning', 'reading']` -- shuffle
      // each item's own half-order here, or the first sighting of every item
      // is always its meaning.
      this.queue.set(queue.items.map(shuffleQuestions));
      this.counters.setDue(queue.total_due);
      this.resetItemState();
      if (this.queue().length === 0) {
        // The gate said something was due, but it has since been answered
        // elsewhere (or the badge was stale) -- same ending as a round that
        // simply ran out.
        this.finishSession();
        return;
      }
      this.phase.set('active');
    } catch (err) {
      this.loadFailed.set(true);
      this.error.set((err as Error).message);
    }
  }

  protected end(): void {
    this.finishSession();
  }

  private finishSession(): void {
    this.queue.set([]);
    this.resetItemState();
    if (this.answered() > 0) {
      this.sessionDurationMs.set(Date.now() - this.sessionStartedAt);
      // Picks up a level reached during the session, for `levelUp` -- the
      // same number the header badge shows.
      void this.counters.refresh();
      this.phase.set('ended');
    } else {
      this.phase.set('idle');
    }
  }

  /** Hand the caret back, or fetch the next page of a long backlog. A round
   * is one page of the queue, and running one out used to end the session
   * while the badge went on counting the rest. */
  private continueRound(): void {
    if (this.queue().length > 0) {
      return;
    }
    if (this.totalDue() > 0) {
      void this.load();
      return;
    }
    this.finishSession();
  }

  // --- answering -------------------------------------------------------

  protected onSubmitted(answer: string): void {
    void this.submitAnswer(answer, false, false);
  }

  /** The second Enter after a "that looks wrong" warning. */
  protected onConfirmed(): void {
    void this.submitAnswer(this.value(), true, false);
  }

  /** Alt+H: reveal the answer, scored as a plain miss. */
  protected onGaveUp(): void {
    void this.submitAnswer('', true, true);
  }

  /** Any edit while held/retry is up -- the verdict is withdrawn, the typed
   * text stays (the field manages that part itself). */
  protected onEdited(): void {
    this.held.set(false);
    this.feedback.update((result) => (result?.retry ? null : result));
  }

  private async submitAnswer(answer: string, confirm: boolean, gaveUp: boolean): Promise<void> {
    const card = this.current();
    const question = this.question();
    if (!card || !question || this.submitting()) {
      return;
    }

    this.submitting.set(true);
    try {
      const result = await this.api.answer(card.subject.id, question, answer, confirm, gaveUp);

      if (result.held) {
        this.held.set(true);
        return;
      }
      this.held.set(false);
      this.gaveUp.set(gaveUp);
      this.feedback.set(result);

      // Completed means the item has moved and its next review is hours
      // away: it has left the due set, and the badge should say so now
      // rather than at the next poll.
      if (result.completed) {
        this.counters.spendDue();
      }

      // A retry is not an answer: the learner produced a real reading of
      // the character, just not the one asked for. It counts for nothing in
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

  /** `Enter` on a settled verdict, or on an open retry. */
  protected next(): void {
    const result = this.feedback();
    const card = this.current();
    if (!result || !card) {
      return;
    }

    if (result.retry) {
      // Same question, same item, nothing consumed -- just ask again.
      this.feedback.set(null);
      this.gaveUp.set(false);
      this.detailsOpen.set(false);
      this.value.set('');
      return;
    }

    let rest = this.queue().slice(1);
    if (result.remaining.length > 0) {
      // Somewhere later in the round, not straight back round again and not
      // at the very back either (issue #27) -- see `reinsert`.
      rest = reinsert(rest, { ...card, questions: result.remaining });
    }

    this.queue.set(rest);
    this.resetItemState();
    this.continueRound();
  }

  // --- "I know this" ---------------------------------------------------

  /**
   * Entry point for the "I know this" button and `Alt+K` (the field's own
   * hotkey, routed here via `(knew)`).
   *
   * Goes straight to `markKnown()` when there is nothing left to clarify (a
   * radical, or the other half already answered); otherwise the first press
   * only raises the confirmation panel, and a second press -- same button,
   * same shortcut, or `Enter` -- is what actually calls `markKnown()`.
   */
  protected requestKnown(): void {
    if (this.confirmKnown()) {
      void this.markKnown();
      return;
    }
    if (this.otherHalfOpen()) {
      this.confirmKnown.set(true);
      this.registerConfirmKnownHotkeys();
      return;
    }
    void this.markKnown();
  }

  protected cancelKnown(): void {
    this.confirmKnown.set(false);
    this.unregisterConfirmKnownHotkeys();
  }

  /**
   * `sumi-answer-field` registers its own `Enter`/`Escape` at construction
   * time, so this panel -- raised later, while the field is still mounted --
   * registers its own copies dynamically and wins under `SumiHotkeys`' stack
   * semantics (most recently registered wins) for as long as it is open.
   * That reproduces "the panel is the most recently raised prompt, so it
   * gets Enter/Escape first", including over a held answer shown at the
   * same time.
   */
  private registerConfirmKnownHotkeys(): void {
    this.unregisterConfirmKnownHotkeys();
    this.confirmKnownUnregisters = [
      this.hotkeys.register({
        keys: SUMI_KEYS.submit,
        label: 'Mark whole item known',
        scope: 'page',
        allowInEditable: true,
        handler: () => void this.markKnown(),
      }),
      this.hotkeys.register({
        keys: SUMI_KEYS.escape,
        label: 'Cancel',
        scope: 'page',
        allowInEditable: true,
        handler: () => this.cancelKnown(),
      }),
    ];
  }

  private unregisterConfirmKnownHotkeys(): void {
    for (const unregister of this.confirmKnownUnregisters) {
      unregister();
    }
    this.confirmKnownUnregisters = [];
  }

  /**
   * "I know this", mid-session.
   *
   * Takes the item out of the queue entirely rather than marking the current
   * question right: the point is that the whole item is known, and asking
   * for its other half would be the exact friction this button removes.
   */
  async markKnown(): Promise<void> {
    const card = this.current();
    if (!card) {
      return;
    }
    try {
      await this.api.markKnown([card.subject.id]);
      this.queue.set(this.queue().slice(1));
      this.resetItemState();
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
      this.resetItemState();
      // Back to Apprentice I is four hours out, so this one has left the
      // due set as surely as an answered item has.
      this.counters.spendDue();
      this.continueRound();
    } catch (err) {
      this.error.set((err as Error).message);
    }
  }

  private resetItemState(): void {
    this.feedback.set(null);
    this.held.set(false);
    this.gaveUp.set(false);
    this.value.set('');
    this.confirmKnown.set(false);
    this.detailsOpen.set(false);
    this.unregisterConfirmKnownHotkeys();
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

  /** `characters`, falling back to the slug -- unless there is an image to
   * show instead, in which case the prompt card gets no text at all and the
   * template projects the image into `[sumiPromptVisual]`. */
  protected promptText(subject: SubjectSummary): string | undefined {
    if (subject.characters) {
      return subject.characters;
    }
    return subject.character_image_url ? undefined : subject.slug;
  }

  protected toneFor(type: ObjectType): string {
    if (type === 'radical') {
      return 'var(--radical)';
    }
    return type === 'kanji' ? 'var(--kanji)' : 'var(--vocabulary)';
  }

  protected primaryMeanings(result: AnswerResult): string {
    return (result.subject?.meanings ?? [])
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  /**
   * Keep `feedback().subject.synonyms` current after a save in the details
   * slot -- same reasoning as the browse list item and the lessons item:
   * reopening the details (or answering the item's other half) must show
   * the new list without a refetch.
   */
  protected onSynonymsChange(subjectId: number, synonyms: string[]): void {
    this.feedback.update((result) => {
      if (!result?.subject || result.subject.id !== subjectId) {
        return result;
      }
      return { ...result, subject: { ...result.subject, synonyms } };
    });
  }
}
