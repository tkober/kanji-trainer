import { DatePipe } from '@angular/common';
import { Component, Injector, OnDestroy, afterNextRender, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { SumiBanner, SumiCard, SumiErrorState, SumiPage, SumiProgress } from 'sumi-ui/layout';
import {
  SumiButtonDirective,
  SumiCheckboxDirective,
  SumiInputDirective,
  SumiKbdDirective,
  SumiSelectDirective,
  SumiToggle,
} from 'sumi-ui/forms';

import { Api } from '../../core/api';
import type {
  ImportRun,
  ReviewItemOrder,
  ReviewTypeOrder,
  Settings,
  WaniKaniAccount,
} from '../../core/api.types';

/** The import threshold's stage names, index 0 = stage 1 (Apprentice I),
 * same wording as the `<select>` in settings.html. */
const THRESHOLD_STAGE_NAMES = [
  'Apprentice I',
  'Apprentice II',
  'Apprentice III',
  'Apprentice IV',
  'Guru I',
  'Guru II',
  'Master',
  'Enlightened',
  'Burned',
];

@Component({
  selector: 'app-settings',
  imports: [
    DatePipe,
    FormsModule,
    SumiBanner,
    SumiButtonDirective,
    SumiCard,
    SumiCheckboxDirective,
    SumiErrorState,
    SumiInputDirective,
    SumiKbdDirective,
    SumiPage,
    SumiProgress,
    SumiSelectDirective,
    SumiToggle,
  ],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsPage implements OnDestroy {
  private readonly api = inject(Api);
  private readonly route = inject(ActivatedRoute);
  private readonly injector = inject(Injector);

  protected readonly settings = signal<Settings | null>(null);
  protected readonly account = signal<WaniKaniAccount | null>(null);
  protected readonly run = signal<ImportRun | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly status = signal<string | null>(null);
  protected readonly busy = signal(false);
  /** Whether the page has ever loaded successfully -- a failure before that
   * point is the T6 scene; one afterwards (saving, checking the account,
   * importing) stays a banner. */
  protected readonly loaded = signal(false);
  protected readonly loadFailed = signal(false);

  protected token = '';
  protected importThreshold = 5;
  protected knownStage = 9;
  protected intervals = '';
  protected dailyLimit = 0;
  protected batchSize = 5;
  protected softAnswer = true;
  protected reviewItemOrder: ReviewItemOrder = 'random';
  protected reviewTypeOrder: ReviewTypeOrder = 'mixed';
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
      this.reviewItemOrder = settings.review_item_order;
      this.reviewTypeOrder = settings.review_type_order;
      this.run.set(run);
      if (run?.status === 'running') {
        this.watch(run.id);
      }
      this.error.set(null);
      this.loaded.set(true);
      this.loadFailed.set(false);
      this.scrollToFragment();
    } catch (err) {
      if (this.loaded()) {
        this.error.set((err as Error).message);
      } else {
        this.loadFailed.set(true);
      }
    }
  }

  /**
   * The router's own anchor scrolling (`withInMemoryScrolling` in
   * app.config.ts) runs right after navigation, which can land before this
   * lazy-loaded page has actually rendered -- the dashboard's empty-state
   * button (`fragment="import"`) was landing at the top of the page instead
   * of on the import card. Re-running the scroll after the render that
   * follows `load()` is a no-op when the router already got there first,
   * and reliable when it did not. It has to wait for that render: right
   * after the signals are set, the card is not in the DOM yet.
   */
  private scrollToFragment(): void {
    const fragment = this.route.snapshot.fragment;
    if (fragment) {
      afterNextRender(() => document.getElementById(fragment)?.scrollIntoView(), {
        injector: this.injector,
      });
    }
  }

  /**
   * The import threshold's stage name, matching the `<select>` above
   * (stages 1-9 only -- there is no "Lesson" option here, unlike the
   * tile labelling in `lessons.ts`, which keeps its own separate copy for
   * stage 0).
   */
  protected thresholdName(stage: number): string {
    return THRESHOLD_STAGE_NAMES[Math.max(1, Math.min(9, stage)) - 1];
  }

  async saveToken(): Promise<void> {
    await this.save({ wanikani_api_token: this.token }, 'Token saved.');
    this.token = '';
    this.account.set(null);
  }

  async clearToken(): Promise<void> {
    await this.save({ wanikani_api_token: '' }, 'Token removed.');
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
        review_item_order: this.reviewItemOrder,
        review_type_order: this.reviewTypeOrder,
      },
      'Settings saved.',
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
}
