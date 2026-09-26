import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Counters } from './core/counters';

@Component({
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  private readonly counters = inject(Counters);

  protected readonly due = this.counters.due;
  protected readonly lessons = this.counters.lessons;

  constructor() {
    void this.counters.refresh();
    // The badge can go stale with nobody touching anything: an item comes due
    // on the clock. Everything the learner does is reported to `Counters` as
    // it happens, so this is the correction for drift, not the count itself.
    // A minute is well under the shortest interval (four hours).
    setInterval(() => void this.counters.refresh(), 60_000);
  }
}
