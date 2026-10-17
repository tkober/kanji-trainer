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
import {
  SUMI_PRACTICE,
  type SumiSessionProgressSegment,
  type SumiVerdict,
  type SumiVerdictKind,
} from 'sumi-ui/practice';

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
import {
  EMPTY_TALLY,
  recordAnswer,
  recordKnown,
  recordReset,
  summarize,
  type ReviewTally,
} from '../../core/review-tally';
import { ContextSentences } from '../../shared/context-sentences/context-sentences';
import { RadicalIllustration } from '../../shared/radical-illustration/radical-illustration';
import { Readings } from '../../shared/readings/readings';
import { Synonyms } from '../../shared/synonyms/synonyms';

/** A queue entry plus what it still owes. */
type Card = QueueItem;

type Phase = 'idle' | 'active' | 'ended';

/** One review round's exact question size, and the correct answers given
 * while it was the active round -- `rounds[i]` while `i` has been fetched,
 * see `segments` below for the estimated rounds beyond that. */
interface RoundProgress {
  size: number;
  done: number;
}

/** Sum of every card's own `questions.length` -- the unit `done`/`remaining`
 * and the progress segments all count in, same reasoning as the old
 * `remaining` computed this replaces (see its doc comment history). */
function questionsCount(cards: readonly Card[]): number {
  return cards.reduce((sum, card) => sum + card.questions.length, 0);
}

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
  /** True while the idle gate's own queue fetch is in flight -- covers the
   * very first load and every retry/restart, not an active round's page
   * turn (the active screen never shows a loading state of its own). */
  protected readonly loading = signal(true);
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
  /** Every event of this session, raw -- feeds the session summary (#80),
   * not yet read anywhere. Reset wherever `answered`/`correct` are reset. */
  protected readonly tally = signal<ReviewTally>(EMPTY_TALLY);
  protected readonly tallySummary = computed(() => summarize(this.tally()));
  /** Correctly answered questions this session (issue #54) -- separate from
   * `correct`/`answered`, which stay the accuracy's own numbers. A non-retry
   * correct verdict counts one question; "I know this" counts every open
   * question the declared item still owed. A retry and a wrong answer count
   * for neither. Updated in the same place and at the same moment as
   * `remaining` below, so `done + remaining` never visibly moves except for
   * a reason the learner can see. */
  protected readonly done = signal(0);
  /**
   * Open questions still due, across the whole account -- not just the
   * loaded round(s). `sumi-session-bar`'s `[remaining]` reads this, and
   * `total = done + remaining` is what the counter and the built-in bar used
   * to show before `[showProgress]="false"` handed that bar to
   * `sumi-session-progress` instead.
   *
   * Synced straight from `total_due_questions` on every fetch -- the
   * backend's query already excludes whatever has been answered through by
   * then, so a refetch can only ever replace this with a fresher truth, not
   * double-count. Between fetches it moves by exactly as much as `done`
   * does (a correct answer or "I know this" hands a question from here to
   * `done`, so the total holds steady) or shrinks on its own when a reset
   * item's questions leave the session without ever becoming `done`.
   */
  protected readonly remaining = signal(0);
  /**
   * One entry per *loaded* review round (a page `fetchQueue`/
   * `continueRound` has actually fetched), in order. `segments` below adds
   * estimated entries for whatever is due beyond that. `size` is fixed at
   * fetch time (wrong answers loop a question back into the same round
   * without growing it) except for a reset, which shrinks it -- the item's
   * open questions leave the round, not just the session.
   */
  protected readonly rounds = signal<RoundProgress[]>([]);
  private sessionStartedAt = 0;
  protected readonly sessionDurationMs = signal(0);
  /** `counters.level()` at the moment the session started, for `levelUp`. */
  private startLevel: number | null = null;

  protected readonly current = computed(() => this.queue()[0] ?? null);
  protected readonly question = computed<QuestionType | null>(
    () => this.current()?.questions[0] ?? null,
  );

  /**
   * Open questions still in `queue()` -- the *current round's* own
   * outstanding halves, not the session-wide `remaining` above. Used only to
   * work out how much of the account-wide `remaining`/`totalDue` lies beyond
   * the loaded round(s), for `segments`' estimated future rounds.
   *
   * While a settled verdict is up, the card only leaves `queue()` in
   * `next()`; counting it as what `next()` will leave behind
   * (`result.remaining`) keeps this reading steady across that window
   * instead of jumping up by one and back down, same reasoning as `done`/
   * `remaining` above being updated at the verdict rather than at `next()`.
   */
  protected readonly roundOpenQuestions = computed(() => {
    const open = questionsCount(this.queue());
    const result = this.feedback();
    const card = this.current();
    if (!result || result.retry || !card) {
      return open;
    }
    return open - card.questions.length + result.remaining.length;
  });

  /** Same idea as `roundOpenQuestions`, but in items -- what `segments`
   * needs to turn the account-wide `totalDue()` (items) into "items beyond
   * the loaded round(s)" before splitting those into estimated rounds of up
   * to 100 items each. An item only leaves `queue()` once every question it
   * owes has been answered, so the verdict-up adjustment only applies once
   * nothing remains outstanding for it. */
  protected readonly roundOpenItems = computed(() => {
    const open = this.queue().length;
    const result = this.feedback();
    const card = this.current();
    if (!result || result.retry || !card) {
      return open;
    }
    return result.remaining.length > 0 ? open : open - 1;
  });

  /**
   * `sumi-session-progress`'s segments: one per loaded round, at its exact
   * question size, plus one per *estimated* future round for whatever is
   * due beyond the loaded round(s) -- split into rounds of up to 100 items
   * each (matching `fetchQueue`/`continueRound`'s own page size), with the
   * remaining due questions distributed across them proportionally to their
   * item counts. A single segment overall gets no label (and no "Round 1"),
   * per `sumi-session-progress`'s own convention for an unsegmented bar.
   */
  protected readonly segments = computed<SumiSessionProgressSegment[]>(() => {
    const rounds = this.rounds();
    const segments: SumiSessionProgressSegment[] = rounds.map((round, i) => ({
      size: round.size,
      done: round.done,
      label: `Round ${i + 1}`,
    }));

    const futureItems = Math.max(0, this.totalDue() - this.roundOpenItems());
    let futureQuestions = Math.max(0, this.remaining() - this.roundOpenQuestions());

    if (futureItems > 0 && futureQuestions > 0) {
      const roundCount = Math.ceil(futureItems / 100);
      let itemsLeft = futureItems;
      for (let i = 0; i < roundCount; i++) {
        const isLast = i === roundCount - 1;
        const itemsInRound = isLast ? itemsLeft : Math.min(100, itemsLeft);
        // The last estimated round takes whatever is left over, so rounding
        // every earlier one never drifts the total away from `remaining()`.
        const size = isLast
          ? futureQuestions
          : Math.round((itemsInRound / futureItems) * futureQuestions);
        segments.push({ size, done: 0, label: `Round ${rounds.length + i + 1}` });
        itemsLeft -= itemsInRound;
        futureQuestions -= size;
      }
    }

    if (segments.length <= 1) {
      return segments.map(({ size, done }) => ({ size, done }));
    }
    return segments;
  });

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
  protected readonly hankoCharacters = computed(() =>
    this.sessionAccuracy() >= 0.8 ? '合格' : '練習',
  );
  protected readonly hankoLabel = computed(() =>
    this.sessionAccuracy() >= 0.8 ? 'Passed' : 'Practice',
  );

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

    // Fetch once on open, the same way the page always has, so the idle
    // screen can tell "loading" from "nothing due" from "can't reach the
    // server" instead of reading `counters.due()` before its first poll has
    // ever landed (which reads as "No reviews due" for a beat, and as the
    // empty state rather than an error if the backend happens to be down).
    void this.fetchQueue();
  }

  // --- the session ---------------------------------------------------------

  /** The idle gate's own "Start reviews" (and `Enter`, via the gate's
   * hotkey) -- goes straight to `active` on the queue `fetchQueue()` already
   * loaded, no second request, so Enter is instant. */
  protected start(): void {
    if (this.queue().length === 0) {
      return;
    }
    this.answered.set(0);
    this.correct.set(0);
    this.tally.set(EMPTY_TALLY);
    this.done.set(0);
    this.rounds.set([{ size: questionsCount(this.queue()), done: 0 }]);
    this.sessionStartedAt = Date.now();
    this.startLevel = this.counters.level();
    this.resetItemState();
    this.phase.set('active');
  }

  /** The idle gate's error state's "Try again". */
  protected retryLoad(): void {
    void this.fetchQueue();
  }

  /** "Practice again" after a summary, or `Enter` on that gate -- unlike
   * `start()`, this one *does* re-fetch: the queue the session began with is
   * gone (answered through, or the session was abandoned minutes ago), so
   * there is nothing to reuse. Goes straight into a fresh round once the
   * fetch lands, same as `start()` would right after it. */
  protected async restart(): Promise<void> {
    this.answered.set(0);
    this.correct.set(0);
    this.tally.set(EMPTY_TALLY);
    this.done.set(0);
    this.startLevel = this.counters.level();
    this.phase.set('idle');
    const ok = await this.fetchQueue();
    if (ok && this.queue().length > 0) {
      this.rounds.set([{ size: questionsCount(this.queue()), done: 0 }]);
      this.sessionStartedAt = Date.now();
      this.resetItemState();
      this.phase.set('active');
    }
    // Otherwise the idle screen already shows the right thing on its own --
    // the error state, or the empty state if nothing turned out to be due.
  }

  /** The one place that calls `GET /api/reviews`. Leaves `phase` alone --
   * every caller (the constructor, `retryLoad()`, `restart()`,
   * `continueRound()`) decides what to do with the result itself. */
  private async fetchQueue(): Promise<boolean> {
    this.loading.set(true);
    this.loadFailed.set(false);
    this.error.set(null);
    try {
      const queue = await this.api.reviewQueue(100);
      // The backend always hands back `['meaning', 'reading']` -- shuffle
      // each item's own half-order here, or the first sighting of every item
      // is always its meaning.
      this.queue.set(queue.items.map(shuffleQuestions));
      this.counters.setDue(queue.total_due);
      // The backend's query already excludes whatever this session has
      // answered through by now, so this can only replace `remaining` with a
      // fresher truth -- never double-count. `start()`/`restart()`/
      // `continueFetching()` are what turn this fetch into a new round.
      this.remaining.set(queue.total_due_questions);
      return true;
    } catch (err) {
      this.loadFailed.set(true);
      this.error.set((err as Error).message);
      return false;
    } finally {
      this.loading.set(false);
    }
  }

  protected end(): void {
    this.finishSession();
  }

  private finishSession(): void {
    this.resetItemState();
    if (this.answered() > 0) {
      this.sessionDurationMs.set(Date.now() - this.sessionStartedAt);
      // Picks up a level reached during the session, for `levelUp` -- the
      // same number the header badge shows.
      void this.counters.refresh();
      this.phase.set('ended');
    } else {
      // Nothing answered -- e.g. `end()` right after `start()` -- so
      // whatever is left in `queue()` is still exactly what the gate
      // advertised and "Start reviews" can reuse it without a re-fetch.
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
      void this.continueFetching();
      return;
    }
    this.finishSession();
  }

  /** `continueRound()`'s fetch -- stays in `active` throughout (the session
   * is already running), and only `finishSession()`s if the backlog turned
   * out to be spent after all. A failure drops back to `idle`, whose error
   * state offers "Try again" -- the round is empty at this point, so the
   * active screen would have nothing to show the error next to. */
  private async continueFetching(): Promise<void> {
    const ok = await this.fetchQueue();
    if (!ok) {
      this.phase.set('idle');
      return;
    }
    if (this.queue().length === 0) {
      this.finishSession();
    } else {
      // A new round -- its exact size, same as `start()`/`restart()` record
      // for round 1. `segments` stops estimating this round the moment it
      // appears here.
      this.rounds.update((rounds) => [...rounds, { size: questionsCount(this.queue()), done: 0 }]);
    }
  }

  /**
   * `n` questions just became done -- a correct non-retry answer (`n = 1`)
   * or "I know this" (`n` = however many questions the declared item still
   * owed). Moves `done` and `remaining` by the same amount in the same tick,
   * so their sum (what the counter and `sumi-session-progress` show as the
   * session total) never visibly jumps, and attributes it to whichever round
   * is currently loaded -- the last entry in `rounds()` is always the active
   * one, since `continueRound()` only starts fetching the next page once the
   * current one's queue is empty.
   */
  private addDone(n: number): void {
    this.done.update((d) => d + n);
    this.remaining.update((r) => Math.max(0, r - n));
    this.rounds.update((rounds) => {
      const last = rounds.length - 1;
      if (last < 0) {
        return rounds;
      }
      return rounds.map((round, i) => (i === last ? { ...round, done: round.done + n } : round));
    });
  }

  /**
   * `n` questions left the session without becoming `done` -- a reset
   * ("Relearn from scratch"). Shrinks `remaining` and the active round's own
   * `size` by the same amount, since those questions are not coming back
   * into this round either, unlike `addDone`, which never touches `size`.
   */
  private shrinkOpen(n: number): void {
    this.remaining.update((r) => Math.max(0, r - n));
    this.rounds.update((rounds) => {
      const last = rounds.length - 1;
      if (last < 0) {
        return rounds;
      }
      return rounds.map((round, i) =>
        i === last ? { ...round, size: Math.max(round.done, round.size - n) } : round,
      );
    });
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
        // `recordAnswer` also ignores retry/held itself -- belt and braces.
        this.tally.update((tally) => recordAnswer(tally, card.subject, question, result));
        if (result.correct) {
          this.correct.update((n) => n + 1);
          this.addDone(1);
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
      this.tally.update((tally) => recordKnown(tally, card.subject));
      // The whole item is known, meaning and reading together -- every
      // question it still owed counts as done right away, not just the half
      // that happened to be on screen.
      this.addDone(card.questions.length);
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
      this.tally.update((tally) => recordReset(tally, card.subject));
      // Its open questions leave the session without ever becoming done --
      // the session total shrinks rather than holding steady.
      this.shrinkOpen(card.questions.length);
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
