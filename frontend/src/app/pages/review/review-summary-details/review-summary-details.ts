import { Component, computed, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SumiIcon } from 'sumi-ui/core';
import {
  SumiFlowChart,
  SumiProgressRow,
  type SumiFlowCategory,
  type SumiSegment,
} from 'sumi-ui/charts';

import type { ObjectType, SubjectSummary } from '../../../core/api.types';
import {
  answerRows,
  byTypeRows,
  footerLine,
  stageCards,
  stageFlowAriaLabel,
  stageFlowHint,
  withinStagePossessive,
} from '../../../core/review-summary-format';
import { tallyType, type TallySummary } from '../../../core/review-tally';

/** `sumi-flow-chart`'s `categories`, in ladder order -- Apprentice included
 *  (unlike `RiseGroup`/the stage cards above): a flow's `from` side can be
 *  Apprentice even though it is never a rise *target*. Colours are the same
 *  `--stage-*` tokens the "Reached a new stage" cards use (`app-tokens.scss`),
 *  so the two sections read as the same colour language. Module-level since
 *  it never depends on `summary`. */
const STAGE_FLOW_CATEGORIES: readonly SumiFlowCategory[] = [
  { key: 'apprentice', label: 'Apprentice', color: 'var(--stage-apprentice)' },
  { key: 'guru', label: 'Guru', color: 'var(--stage-guru)' },
  { key: 'master', label: 'Master', color: 'var(--stage-master)' },
  { key: 'enlightened', label: 'Enlightened', color: 'var(--stage-enlightened)' },
  { key: 'burned', label: 'Burned', color: 'var(--stage-burned)' },
];

const STAGE_FLOW_KEY = 'kt-summary-stage-flow';

/** Collapsed unless an earlier summary was explicitly left open -- same
 *  try/catch-and-fall-back shape as `lessons.ts`'s `readBatchingPreference`,
 *  but defaulting closed rather than open: a missing/invalid value here
 *  means "never opened", not "no preference set". */
function readStageFlowOpen(): boolean {
  try {
    return localStorage.getItem(STAGE_FLOW_KEY) === '1';
  } catch {
    return false;
  }
}

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
  imports: [RouterLink, SumiProgressRow, SumiFlowChart, SumiIcon],
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

  /** Hidden entirely when nothing completed this session (e.g. ended after
   *  half an item) -- there is no flow to draw. Desktop-only is CSS alone
   *  (see the stylesheet), not reflected here, since it must survive a
   *  resize without Angular re-rendering anything. */
  protected readonly showStageFlowSection = computed(() => this.summary().items > 0);

  protected readonly stageFlowCategories = STAGE_FLOW_CATEGORIES;

  protected readonly stageFlowFlows = computed(() =>
    this.summary().flow.map((f) => ({ from: f.from, to: f.to, value: f.count })),
  );

  protected readonly stageFlowAriaLabel = computed(() => stageFlowAriaLabel(this.summary().items));

  protected readonly stageFlowHint = computed(() => stageFlowHint(this.summary().items));

  /** Open/closed, remembered in localStorage -- read once here (component
   *  creation), written back on the `<details>`'s own `toggle` event (see
   *  the template and `onStageFlowToggle` below). A later summary's box
   *  starts open if this one was left open. */
  protected readonly stageFlowOpen = signal(readStageFlowOpen());

  protected onStageFlowToggle(event: Event): void {
    const open = (event.target as HTMLDetailsElement).open;
    this.stageFlowOpen.set(open);
    try {
      localStorage.setItem(STAGE_FLOW_KEY, open ? '1' : '0');
    } catch {
      // A remembered preference is a convenience, not a requirement.
    }
  }

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
