import { Component, input } from '@angular/core';
import { SumiHoldFocus } from 'sumi-ui/practice';

let nextId = 0;

/**
 * A small (i) button that opens a native popover with projected content --
 * extracted from `app-readings`' own On'yomi/Kun'yomi info flyout (issue
 * #87) so the lessons reading phase's "I already know all N" info can reuse
 * the exact same look and behaviour instead of a second copy. `label` is
 * the button's own `aria-label` (e.g. "What is On'yomi?"); the popover's
 * content -- heading, body text, links -- is entirely up to the caller via
 * content projection, so it can style it exactly as before (see `app-readings`'
 * own stylesheet for its `.title`/`.common` text, now projected rather than
 * declared alongside the popover markup itself).
 *
 * The native Popover API gives light-dismiss (outside click, a second click
 * on the invoker, Esc) and the top layer for free, and its default UA
 * position -- centred in the viewport -- needs no anchoring maths to stay
 * on screen at 390px. `sumiHoldFocus` on the button keeps opening it from
 * taking the caret where this sits next to an answer field.
 */
@Component({
  selector: 'app-info-popover',
  imports: [SumiHoldFocus],
  templateUrl: './info-popover.html',
  styleUrl: './info-popover.scss',
})
export class InfoPopover {
  readonly label = input.required<string>();

  protected readonly popoverId = `info-popover-${++nextId}`;
}
