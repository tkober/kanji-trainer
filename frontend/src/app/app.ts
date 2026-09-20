import { Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Api } from './core/api';

@Component({
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  selector: 'app-root',
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  private readonly api = inject(Api);

  protected readonly due = signal(0);
  protected readonly lessons = signal(0);

  constructor() {
    void this.refresh();
    // The badge is the only thing on screen that goes stale on its own: an
    // item can come due while the tab sits open. A minute is well under the
    // shortest interval (four hours) and costs one small request.
    setInterval(() => void this.refresh(), 60_000);
  }

  async refresh(): Promise<void> {
    try {
      const stats = await this.api.stats();
      this.due.set(stats.due_now);
      // The level's open lessons, not the collection's. A badge reading
      // 9.321 after importing a reset account is worse than no badge.
      this.lessons.set(stats.lessons_available);
    } catch {
      // A badge is not worth an error banner; the screens themselves report.
    }
  }
}
