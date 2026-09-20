import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api } from '../../core/api';
import type { ImportRun, Stats } from '../../core/api.types';

@Component({
  selector: 'app-dashboard',
  imports: [RouterLink],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
})
export class Dashboard {
  private readonly api = inject(Api);

  protected readonly stats = signal<Stats | null>(null);
  protected readonly lastImport = signal<ImportRun | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly loading = signal(true);

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const [stats, lastImport] = await Promise.all([
        this.api.stats(),
        this.api.latestImport(),
      ]);
      this.stats.set(stats);
      this.lastImport.set(lastImport);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  protected accuracy(stats: Stats): string {
    return stats.accuracy_today === null
      ? '—'
      : `${Math.round(stats.accuracy_today * 100)} %`;
  }

  /** Share of the collection that is out of the rotation for good. */
  protected settled(stats: Stats): number {
    if (stats.total_subjects === 0) {
      return 0;
    }
    return Math.round(((stats.known_count + stats.burned_count) / stats.total_subjects) * 100);
  }
}
