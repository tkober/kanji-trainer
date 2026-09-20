import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api } from '../../core/api';
import type { LessonItem, ObjectType } from '../../core/api.types';

@Component({
  selector: 'app-lessons',
  imports: [RouterLink],
  templateUrl: './lessons.html',
  styleUrl: './lessons.scss',
})
export class LessonsPage {
  private readonly api = inject(Api);

  protected readonly items = signal<LessonItem[]>([]);
  protected readonly totalAvailable = signal(0);
  protected readonly dailyLimit = signal(0);
  protected readonly index = signal(0);
  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  constructor() {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const lessons = await this.api.lessons(20);
      this.items.set(lessons.items);
      this.totalAvailable.set(lessons.total_available);
      this.dailyLimit.set(lessons.daily_limit);
      this.index.set(0);
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  protected current(): LessonItem | null {
    return this.items()[this.index()] ?? null;
  }

  protected previous(): void {
    this.index.update((i) => Math.max(0, i - 1));
  }

  protected next(): void {
    this.index.update((i) => Math.min(this.items().length - 1, i + 1));
  }

  /** Put this batch into the review rotation at Apprentice I. */
  async startBatch(): Promise<void> {
    await this.apply(() =>
      this.api.startLessons(this.items().map((item) => item.subject.id)),
    );
  }

  /**
   * "Kenne ich schon" for the whole batch — the lesson-queue counterpart to
   * the button in the review screen, and the faster of the two for someone
   * working through levels they already finished once.
   */
  async markBatchKnown(): Promise<void> {
    await this.apply(() =>
      this.api.markKnown(this.items().map((item) => item.subject.id)),
    );
  }

  async markCurrentKnown(): Promise<void> {
    const item = this.current();
    if (!item) {
      return;
    }
    await this.apply(() => this.api.markKnown([item.subject.id]));
  }

  private async apply(action: () => Promise<unknown>): Promise<void> {
    this.busy.set(true);
    try {
      await action();
      await this.load();
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  protected typeLabel(type: ObjectType): string {
    return {
      radical: 'Radikal',
      kanji: 'Kanji',
      vocabulary: 'Vokabel',
      kana_vocabulary: 'Vokabel (Kana)',
    }[type];
  }

  protected meanings(item: LessonItem): string {
    return item.subject.meanings
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  protected readings(item: LessonItem): string {
    return item.subject.readings
      .filter((reading) => reading.accepted_answer !== false)
      .map((reading) => reading.reading)
      .join('、');
  }
}
