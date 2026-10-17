import { Component, OnInit, computed, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  SumiBadge,
  SumiFocusModeDirective,
  SumiHanko,
  SumiShellFocusActionsDirective,
} from 'sumi-ui/layout';
import { SumiButtonDirective } from 'sumi-ui/forms';
import {
  SUMI_PRACTICE,
  type SumiSessionProgressSegment,
  type SumiVerdict,
  type SumiVerdictKind,
} from 'sumi-ui/practice';

import { Api } from '../../../core/api';
import type {
  ObjectType,
  PracticeOut,
  QuestionType,
  SubjectSummary,
} from '../../../core/api.types';
import {
  buildPracticeQueue,
  EMPTY_PRACTICE_TALLY,
  recordPracticeAttempt,
  shakyPracticeItems,
  summarizePracticeRound,
  type PracticeQueueEntry,
  type PracticeRoundSummary,
  type PracticeSource,
  type PracticeTally,
} from '../../../core/practice-queue';
import { reinsert } from '../../../core/review-queue';
import { tallyType, type PracticeItem } from '../../../core/review-tally';
import { ContextSentences } from '../../../shared/context-sentences/context-sentences';
import { Mnemonic } from '../../../core/mnemonic';
import { RadicalIllustration } from '../../../shared/radical-illustration/radical-illustration';
import { Readings } from '../../../shared/readings/readings';
import { Synonyms } from '../../../shared/synonyms/synonyms';

type RoundPhase = 'active' | 'ended';

/**
 * "Review mistakes" (#96, epic #85): a practice round over a review
 * session's mistakes, rendered by `Review` in its own `'practice'` phase so
 * the review summary underneath survives untouched -- "Back to summary"
 * (`back`, the only output) just switches the parent's phase back.
 *
 * Deliberately a single component covering both the round and its own end
 * screen (mockups 2 and 3): "Practice again" there starts a fresh round
 * over just the shaky items without ever leaving this component or telling
 * the parent, so `items` (the input) only ever reflects the *original*
 * mistakes from the review session, not whatever the learner is currently
 * drilling.
 *
 * Reuses the review screen's own prompt card / answer field / verdict
 * (`sumi-prompt-card appearance="tinted"`, `sumi-answer-field`,
 * `sumi-verdict`) so the practice card looks identical to a review's,
 * differing only in the header and the meta line (CLAUDE.md's "Mockups:
 * reuse real components"). No "I know this", no reset, no stage line, no
 * level-up -- practice moves nothing, see `backend/app/api/study.py`'s
 * `practice_answer`.
 */
@Component({
  selector: 'app-review-practice',
  imports: [
    ContextSentences,
    Mnemonic,
    RadicalIllustration,
    Readings,
    RouterLink,
    Synonyms,
    SumiBadge,
    SumiButtonDirective,
    SumiFocusModeDirective,
    SumiHanko,
    SumiShellFocusActionsDirective,
    ...SUMI_PRACTICE,
  ],
  templateUrl: './review-practice.html',
  styleUrl: './review-practice.scss',
})
export class ReviewPractice implements OnInit {
  private readonly api = inject(Api);

  readonly items = input.required<readonly PracticeItem[]>();
  readonly back = output<void>();

  protected readonly roundPhase = signal<RoundPhase>('active');

  /** The items *this round* covers -- the original mistakes on the first
   *  round, just the shaky subset on a "Practice again". Kept separate from
   *  `items()` (the input), which never changes once the parent hands it
   *  over. */
  private readonly roundItems = signal<readonly PracticeSource[]>([]);
  protected readonly queue = signal<PracticeQueueEntry[]>([]);
  protected readonly totalCount = signal(0);
  protected readonly doneCount = signal(0);
  protected readonly firstTry = signal<PracticeTally>(EMPTY_PRACTICE_TALLY);
  private readonly skippedIds = signal<ReadonlySet<number>>(new Set());
  protected readonly skippedNotes = signal<readonly string[]>([]);

  protected readonly feedback = signal<PracticeOut | null>(null);
  protected readonly value = signal('');
  protected readonly submitting = signal(false);
  /** Frontend-only soft-answer hold (#96) -- the backend never holds a
   *  practice verdict (see `Api.practiceAnswer`'s doc comment), so this
   *  component always gets the real result back and decides for itself
   *  whether to reveal it yet. `heldResult` is that withheld result. */
  protected readonly held = signal(false);
  private readonly heldResult = signal<PracticeOut | null>(null);
  protected readonly gaveUp = signal(false);
  protected readonly detailsOpen = signal(false);
  protected readonly error = signal<string | null>(null);
  private softAnswerEnabled = false;

  private sessionStartedAt = 0;
  protected readonly durationMs = signal(0);
  protected readonly endStats = signal<PracticeRoundSummary | null>(null);

  protected readonly current = computed<PracticeQueueEntry | null>(() => this.queue()[0] ?? null);
  protected readonly question = computed<QuestionType | null>(
    () => this.current()?.question ?? null,
  );

  protected readonly progressLabel = computed(() => `${this.doneCount()} / ${this.totalCount()}`);
  /** `sumi-session-progress`, same placement and single-segment shape as
   *  the review screen's own `segments()` for a single round (no label --
   *  "the practice round" is the only segment there ever is, see the
   *  component's own doc comment on what an unlabelled lone segment means). */
  protected readonly progressSegments = computed<SumiSessionProgressSegment[]>(() => [
    { size: this.totalCount(), done: this.doneCount() },
  ]);

  protected readonly shakyCount = computed(() => this.endStats()?.shaky.length ?? 0);
  protected readonly firstTryPercent = computed(() => {
    const stats = this.endStats();
    if (!stats || stats.items === 0) {
      return 0;
    }
    return Math.round((stats.firstTryItems / stats.items) * 100);
  });

  /** The field's own one-line verdict -- mirrors `Review.fieldVerdict`,
   *  minus anything SRS-specific (there is nothing to be specific about). */
  protected readonly fieldVerdict = computed<SumiVerdict | null>(() => {
    if (this.held()) {
      return { kind: 'held', message: 'That looks wrong. Enter to submit anyway, Esc to edit.' };
    }
    const result = this.feedback();
    if (!result) {
      return null;
    }
    if (result.retry) {
      const entry = this.current();
      const generic =
        entry && entry.subject.object_type !== 'vocabulary'
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

  /** "Not quite" (mockup 2) rather than `sumi-verdict`'s default "Wrong". */
  protected readonly verdictTitle = computed<string | undefined>(() =>
    this.settledKind() === 'wrong' ? 'Not quite' : undefined,
  );
  protected readonly verdictMessage = computed<string | undefined>(() =>
    this.settledKind() === 'wrong' ? 'Comes back later in this round.' : undefined,
  );

  ngOnInit(): void {
    this.startRound(this.items());
    void this.loadSettings();
  }

  private async loadSettings(): Promise<void> {
    try {
      const settings = await this.api.settings();
      this.softAnswerEnabled = settings.soft_answer_enabled;
    } catch {
      // Soft-answer is a convenience; practising without it is still correct.
    }
  }

  private startRound(items: readonly PracticeSource[]): void {
    this.roundItems.set(items);
    const queue = buildPracticeQueue(items);
    this.queue.set(queue);
    this.totalCount.set(queue.length);
    this.doneCount.set(0);
    this.firstTry.set(EMPTY_PRACTICE_TALLY);
    this.skippedIds.set(new Set());
    this.skippedNotes.set([]);
    this.endStats.set(null);
    this.sessionStartedAt = Date.now();
    this.durationMs.set(0);
    this.resetItemState();
    this.roundPhase.set('active');
  }

  // --- answering ---------------------------------------------------------

  protected onSubmitted(answer: string): void {
    void this.submitAnswer(answer, false, false);
  }

  /** The second Enter after a "that looks wrong" warning -- the result was
   *  already fetched (see `submitAnswer`), so this reveals it rather than
   *  asking the backend again for the same answer. */
  protected onConfirmed(): void {
    const entry = this.current();
    const stored = this.heldResult();
    if (!entry || !stored) {
      return;
    }
    this.settle(entry, stored, false);
  }

  /** Alt+H: reveal the answer, scored as a plain miss. */
  protected onGaveUp(): void {
    void this.submitAnswer('', true, true);
  }

  protected onEdited(): void {
    this.held.set(false);
    this.heldResult.set(null);
    this.feedback.update((result) => (result?.retry ? null : result));
  }

  private async submitAnswer(answer: string, confirm: boolean, gaveUp: boolean): Promise<void> {
    const entry = this.current();
    if (!entry || this.submitting()) {
      return;
    }
    this.submitting.set(true);
    try {
      const result = await this.api.practiceAnswer(
        entry.subject.id,
        entry.question,
        answer,
        gaveUp,
      );

      if (!gaveUp && !result.retry && !result.correct && !confirm && this.softAnswerEnabled) {
        // Hold it and ask once -- withheld, not revealed, same as a review's
        // own soft-answer hold (CLAUDE.md), just simulated here since the
        // backend already committed to the real verdict.
        this.heldResult.set(result);
        this.held.set(true);
        return;
      }
      this.settle(entry, result, gaveUp);
    } catch (err) {
      // 409 (the item became due meanwhile, or any other failure) -- skip
      // it rather than crash the round, per the issue.
      this.skipCurrentItem(entry, (err as Error).message);
    } finally {
      this.submitting.set(false);
    }
  }

  /** A settled (non-held) verdict: reveal it, and record the first-attempt
   *  outcome unless it was a retry (costs nothing, same as a review). */
  private settle(entry: PracticeQueueEntry, result: PracticeOut, gaveUp: boolean): void {
    this.held.set(false);
    this.heldResult.set(null);
    this.gaveUp.set(gaveUp);
    this.feedback.set(result);
    if (!result.retry) {
      this.firstTry.update((tally) =>
        recordPracticeAttempt(tally, entry.subject.id, entry.question, result.correct),
      );
      if (result.correct) {
        this.doneCount.update((n) => n + 1);
      }
    }
  }

  /** `Enter` on a settled verdict, or on an open retry -- mirrors `Review.next`. */
  protected next(): void {
    const result = this.feedback();
    const entry = this.current();
    if (!result || !entry) {
      return;
    }
    if (result.retry) {
      this.feedback.set(null);
      this.gaveUp.set(false);
      this.detailsOpen.set(false);
      this.value.set('');
      return;
    }

    let rest = this.queue().slice(1);
    if (!result.correct) {
      // Wrong -- comes back later in the round (E4), until it is right once.
      rest = reinsert(rest, entry);
    }
    this.queue.set(rest);
    this.resetItemState();
    if (rest.length === 0) {
      this.finishRound();
    }
  }

  /** A practice-answer call failed (409 because the item became due
   *  meanwhile, or anything else) -- drop every remaining queue entry for
   *  this subject and shrink the round total to match, rather than leaving
   *  a question in the round nothing can ever answer. */
  private skipCurrentItem(entry: PracticeQueueEntry, message: string): void {
    const id = entry.subject.id;
    const rest = this.queue().slice(1);
    const remaining = rest.filter((e) => e.subject.id !== id);
    const removed = 1 + (rest.length - remaining.length);
    this.totalCount.update((n) => Math.max(this.doneCount(), n - removed));
    this.skippedIds.update((ids) => new Set([...ids, id]));
    this.skippedNotes.update((notes) => [
      ...notes,
      `${this.chipTitle(entry.subject)} — skipped (${message})`,
    ]);
    this.queue.set(remaining);
    this.resetItemState();
    if (remaining.length === 0) {
      this.finishRound();
    }
  }

  private resetItemState(): void {
    this.feedback.set(null);
    this.heldResult.set(null);
    this.held.set(false);
    this.gaveUp.set(false);
    this.value.set('');
    this.detailsOpen.set(false);
  }

  // --- ending --------------------------------------------------------------

  /** The header's "End" button. Nothing answered yet at all (not even a
   *  wrong attempt) -- straight back to the summary, like a review ended
   *  right after `start()`. Otherwise, the end screen; whatever is still
   *  outstanding counts as shaky. */
  protected endRound(): void {
    if (Object.keys(this.firstTry().firstAttempts).length === 0) {
      this.back.emit();
      return;
    }
    this.finishRound();
  }

  private finishRound(): void {
    this.durationMs.set(Date.now() - this.sessionStartedAt);
    this.endStats.set(
      summarizePracticeRound(this.roundItems(), this.firstTry(), this.skippedIds()),
    );
    this.resetItemState();
    this.roundPhase.set('ended');
  }

  /** The end screen's "Practice again" -- a new round over just the shaky
   *  items, drilling only the questions that were not first-try correct
   *  (E1's own reasoning, applied again). Entirely internal: the parent
   *  never learns about it, and `items()` keeps meaning "the original
   *  mistakes" throughout. */
  protected practiceAgain(): void {
    const shaky = shakyPracticeItems(this.roundItems(), this.firstTry(), this.skippedIds());
    if (shaky.length === 0) {
      return;
    }
    this.startRound(shaky);
  }

  protected goBack(): void {
    this.back.emit();
  }

  // --- presentation ----------------------------------------------------

  protected typeLabel(type: ObjectType): string {
    return {
      radical: 'Radical',
      kanji: 'Kanji',
      vocabulary: 'Vocabulary',
      kana_vocabulary: 'Vocabulary (kana)',
    }[type];
  }

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

  protected typeColor(type: ObjectType): string {
    return `var(--${tallyType(type)})`;
  }

  protected chipTitle(subject: SubjectSummary): string {
    return subject.characters ?? subject.slug;
  }

  protected primaryMeanings(result: PracticeOut): string {
    return (result.subject?.meanings ?? [])
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  protected onSynonymsChange(subjectId: number, synonyms: string[]): void {
    this.feedback.update((result) => {
      if (!result?.subject || result.subject.id !== subjectId) {
        return result;
      }
      return { ...result, subject: { ...result.subject, synonyms } };
    });
  }
}
