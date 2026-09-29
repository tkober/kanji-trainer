import { Component, input, model } from '@angular/core';

import { HoldFocus } from '../../core/hold-focus';

/** One row of the flyout: the key(s) that trigger something, and what it does. */
export interface Hotkey {
  keys: string[];
  label: string;
}

/**
 * A small, always-there reminder of the review screen's keyboard shortcuts.
 *
 * The shortcuts themselves are bare keys with no on-screen hint at the point
 * they act (see `Review.onKeydown`) -- that is what lets them stay out of the
 * answer field's way, but it also makes them impossible to discover by
 * looking at the screen. This is the discovery path, modelled on WaniKani's
 * own hotkey flyout: a small button in the same corner, closed by default so
 * it costs nothing to the learner who already knows the keys.
 *
 * `open` is a two-way `model()` rather than an output plus an internal
 * signal, because the host (`Review.onKeydown`, on `?`) needs to toggle it
 * from outside just as the button inside this component does.
 */
@Component({
  selector: 'app-hotkeys',
  imports: [HoldFocus],
  templateUrl: './hotkeys.html',
  styleUrl: './hotkeys.scss',
})
export class Hotkeys {
  readonly hotkeys = input.required<Hotkey[]>();
  readonly open = model(false);

  protected toggle(): void {
    this.open.update((open) => !open);
  }
}
