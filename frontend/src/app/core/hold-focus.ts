import { Directive } from '@angular/core';

/**
 * A button that leaves the caret where it is.
 *
 * Pressing a button moves focus to it, and on a phone that blur closes the
 * on-screen keyboard. It does not come back: a `focus()` call only opens the
 * keyboard while the browser still counts the tap that caused it as in
 * progress, and by the time the next item is on screen — after an answer round
 * trip, after change detection — that moment has passed. So the answer field
 * has to *keep* the caret rather than take it back, and the way to keep it is
 * to stop the button from asking for it.
 *
 * `mousedown` is where focus moves, for taps as much as for clicks: touch
 * browsers fire it as a compatibility event before `click`. Preventing its
 * default cancels only the focus change; the `(click)` handler still runs.
 */
@Directive({
  selector: '[appHoldFocus]',
  host: { '(mousedown)': '$event.preventDefault()' },
})
export class HoldFocus {}
