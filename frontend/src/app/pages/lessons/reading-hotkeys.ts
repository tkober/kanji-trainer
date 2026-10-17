import { isEditableTarget } from 'sumi-ui/core';

/**
 * The lessons page's phases (issue #88 moved this here from `lessons.ts`,
 * which now imports it back -- one definition, and this module stays the
 * only thing `lessons.ts` needs for its hotkeys' `enabled()` checks).
 */
export type Phase = 'select' | 'reading' | 'gate' | 'quiz' | 'ended';

/**
 * The plain state `LessonsPage`'s reading-phase hotkeys enable against,
 * handed to these pure functions instead of reading signals directly --
 * same reasoning as `core/review-queue.ts`: the actual decision ("can Back
 * fire right now") is worth unit-testing on its own, without a component,
 * `SumiHotkeys` or a real `document`.
 */
export interface ReadingHotkeyState {
  readonly phase: Phase;
  readonly busy: boolean;
  readonly index: number;
  readonly itemCount: number;
}

/** `ArrowLeft` -- not on the first item, so it never shows in the help as a
 * dead key there (issue #88). */
export function canGoBack(state: ReadingHotkeyState): boolean {
  return state.phase === 'reading' && !state.busy && state.index > 0;
}

/** `ArrowRight` -- enabled for the whole batch, last item included: there it
 * opens the quiz gate instead of stepping forward (see `LessonsPage`'s
 * handler), exactly what the "Next" / "Continue to quiz" button does. */
export function canGoForward(state: ReadingHotkeyState): boolean {
  return state.phase === 'reading' && !state.busy && state.itemCount > 0;
}

/**
 * `Alt+K` ("I know this one").
 *
 * `SumiHotkeys`' own ground rule already lets Alt combos through while an
 * editable element has focus (only bare keys are blocked there), so the
 * synonym field would otherwise still trigger this. `enabled()` has no
 * access to the triggering event, so this checks `document.activeElement`
 * (passed in, not read here, to keep this function pure and testable)
 * instead -- the same element the ground rule itself would have inspected.
 */
export function canMarkKnown(state: ReadingHotkeyState, activeElement: Element | null): boolean {
  return state.phase === 'reading' && !state.busy && !isEditableTarget(activeElement);
}
