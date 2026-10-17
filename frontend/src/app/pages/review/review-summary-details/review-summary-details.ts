import { Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SumiProgressRow, type SumiSegment } from 'sumi-ui/charts';

import type { ObjectType, SubjectSummary } from '../../../core/api.types';
import {
  answerRows,
  byTypeRows,
  footerLine,
  stageCards,
  withinStagePossessive,
} from '../../../core/review-summary-format';
import { tallyType, type TallySummary } from '../../../core/review-tally';

/**
 * `sumi-session-summary`'s `[sumiSummaryDetails]` content for a finished
 * review session (#83, part of the review-summary epic #80): the stage-card
 * grid, the by-type table and the per-question-type answer bars that do not
 * fit the summary's own tile row.
 *
 * Takes the whole `TallySummary` rather than individual numbers -- every
 * section reads a different slice of it, and the alternative is a dozen
 * narrower inputs that just reassemble the same object in the parent.
 * `review.ts` leaves `answered`/`correct` unset on `sumi-session-summary`
 * itself (dropping its built-in tiles) and projects this component plus its
 * own Items/Correct/Answers tiles instead -- see that component's template.
 *
 * `sumi-session-summary`'s `[sumiSummaryDetails]` slot wrapper spaces out
 * whatever single element lands in it; this component's own host does the
 * same one level down, since it is that one element and has several
 * sections of its own. `container-type: inline-size` on the host (rather
 * than relying on the wrapping `sumi-session-gate`'s own named container) is
 * what the two `@container` breakpoints below key off -- safe here because
 * the summary is only ever shown inside a `width="wide"` gate, which gives
 * this component a definite, generous width to query against.
 */
@Component({
  selector: 'app-review-summary-details',
  imports: [RouterLink, SumiProgressRow],
  templateUrl: './review-summary-details.html',
  styleUrl: './review-summary-details.scss',
  host: { class: 'app-review-summary-details' },
})
export class ReviewSummaryDetails {
  readonly summary = input.required<TallySummary>();

  /** Hidden entirely when nothing rose at all this session (every wrong
   *  answer, or every rise an Apprentice I mistake that cannot drop
   *  further). */
  protected readonly showStageSection = computed(
    () => this.summary().newStageCount > 0 || this.summary().withinStageUp > 0,
  );

  protected readonly stageCards = computed(() => stageCards(this.summary().newStage));

  protected readonly withinStagePossessive = computed(() =>
    withinStagePossessive(this.summary().withinStageUp),
  );

  protected readonly byTypeRows = computed(() => byTypeRows(this.summary().byType));

  protected readonly answerRows = computed(() => {
    const summary = this.summary();
    return answerRows(summary.answers).map((row) => ({
      ...row,
      segments: this.answerSegments(row.correct, row.answered),
    }));
  });

  /** Side by side (by type / answers) only once both sections actually have
   *  something to show -- one alone just renders at its natural width. */
  protected readonly showSplit = computed(
    () => this.byTypeRows().length > 0 && this.answerRows().length > 0,
  );

  protected readonly footerLine = computed(() => {
    const summary = this.summary();
    return footerLine(summary.markedKnown, summary.reset, summary.open);
  });

  /** One coloured "correct" segment plus a same-colour-as-track "wrong"
   *  segment -- `sumi-segmented-bar`'s own background is already
   *  `--sumi-sunken`, so the uncoloured remainder reads as empty track
   *  rather than a second visible fill. `segmentGeometry` sizes segments
   *  off their own sum, not off a separately passed total, so the "wrong"
   *  entry is what makes `correct / answered` the right proportion of the
   *  bar instead of always drawing full width. */
  private answerSegments(correct: number, answered: number): SumiSegment[] {
    return [
      { label: 'Correct', value: correct, color: 'var(--sumi-accent)' },
      { label: 'Wrong', value: answered - correct, color: 'var(--sumi-sunken)' },
    ];
  }

  protected typeColor(type: ObjectType): string {
    return `var(--${tallyType(type)})`;
  }

  /** `characters`, falling back to the slug -- same precedence as
   *  `review.ts`'s own `promptText`, used here as the chip's accessible
   *  title rather than its visual content. */
  protected chipTitle(subject: SubjectSummary): string {
    return subject.characters ?? subject.slug;
  }
}
