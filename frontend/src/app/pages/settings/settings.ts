import { Component, OnDestroy, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api } from '../../core/api';
import type { ImportRun, Settings, WaniKaniAccount } from '../../core/api.types';

@Component({
  selector: 'app-settings',
  imports: [FormsModule],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsPage implements OnDestroy {
  private readonly api = inject(Api);

  protected readonly settings = signal<Settings | null>(null);
  protected readonly account = signal<WaniKaniAccount | null>(null);
  protected readonly run = signal<ImportRun | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly status = signal<string | null>(null);
  protected readonly busy = signal(false);

  protected token = '';
  protected importThreshold = 5;
  protected knownStage = 9;
  protected intervals = '';
  protected dailyLimit = 0;
  protected batchSize = 5;
  protected softAnswer = true;
  protected remapExisting = false;

  private poller: ReturnType<typeof setInterval> | null = null;

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    try {
      const [settings, run] = await Promise.all([this.api.settings(), this.api.latestImport()]);
      this.settings.set(settings);
      this.importThreshold = settings.wanikani_known_srs_stage;
      this.knownStage = settings.known_srs_stage;
      this.intervals = settings.srs_interval_hours;
      this.dailyLimit = settings.daily_lesson_limit;
      this.batchSize = settings.lesson_batch_size;
      this.softAnswer = settings.soft_answer_enabled;
      this.run.set(run);
      if (run?.status === 'running') {
        this.watch(run.id);
      }
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    }
  }

  async saveToken(): Promise<void> {
    await this.save({ wanikani_api_token: this.token }, 'Token gespeichert.');
    this.token = '';
    this.account.set(null);
  }

  async clearToken(): Promise<void> {
    await this.save({ wanikani_api_token: '' }, 'Token entfernt.');
    this.account.set(null);
  }

  async saveSrs(): Promise<void> {
    await this.save(
      {
        wanikani_known_srs_stage: this.importThreshold,
        known_srs_stage: this.knownStage,
        srs_interval_hours: this.intervals,
        daily_lesson_limit: this.dailyLimit,
        lesson_batch_size: this.batchSize,
        soft_answer_enabled: this.softAnswer,
      },
      'Einstellungen gespeichert.',
    );
  }

  private async save(patch: Parameters<Api['saveSettings']>[0], message: string): Promise<void> {
    this.busy.set(true);
    this.status.set(null);
    try {
      this.settings.set(await this.api.saveSettings(patch));
      this.status.set(message);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  async checkAccount(): Promise<void> {
    this.busy.set(true);
    try {
      this.account.set(await this.api.wanikaniAccount());
      this.error.set(null);
    } catch (err) {
      this.account.set(null);
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  async startImport(): Promise<void> {
    this.busy.set(true);
    this.status.set(null);
    try {
      const run = await this.api.startImport(this.importThreshold, this.remapExisting);
      this.run.set(run);
      this.watch(run.id);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  /**
   * Poll the run row until it settles.
   *
   * Two seconds: the import writes its counters once per page of ~500
   * subjects, so anything faster would mostly return the same numbers.
   */
  private watch(runId: number): void {
    this.stopWatching();
    this.poller = setInterval(async () => {
      try {
        const run = await this.api.importRun(runId);
        this.run.set(run);
        if (run.status !== 'running') {
          this.stopWatching();
        }
      } catch {
        this.stopWatching();
      }
    }, 2000);
  }

  private stopWatching(): void {
    if (this.poller !== null) {
      clearInterval(this.poller);
      this.poller = null;
    }
  }

  ngOnDestroy(): void {
    this.stopWatching();
  }

  protected percent(run: ImportRun): number {
    if (!run.subjects_total) {
      return 0;
    }
    return Math.min(100, Math.round((run.subjects_imported / run.subjects_total) * 100));
  }
}
