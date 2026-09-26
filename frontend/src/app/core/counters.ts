import { Injectable, inject, signal } from '@angular/core';

import { Api } from './api';

/** One badge number: what it is, and whether a poll may still overwrite it. */
class Counter {
  readonly value = signal(0);

  /**
   * How many times a screen has set this number.
   *
   * A poll issued before an answer carries a count taken before it too, so
   * landing late it would put the stale number straight back — the bug this
   * file exists to fix, only rarer and harder to see. Comparing the count of
   * edits before and after the request says whether that happened. Each
   * number keeps its own, or a review would discard a fresh lesson count.
   */
  private edits = 0;

  mark(): number {
    return this.edits;
  }

  set(count: number): void {
    this.edits += 1;
    this.value.set(count);
  }

  spend(n: number): void {
    this.edits += 1;
    this.value.update((current) => Math.max(0, current - n));
  }

  /** Take a polled count, unless a screen has said something newer. */
  settle(count: number, seen: number): void {
    if (this.edits === seen) {
      this.value.set(count);
    }
  }
}

/**
 * The two numbers in the header, in one place.
 *
 * They are the app's only cross-screen state, and the only state that can go
 * stale with nothing happening: an item comes due on the clock, without a
 * request to notice it. That is what the poll in `App` is for.
 *
 * The commoner failure is the opposite one, and a poll cannot fix it. The
 * learner clears the queue, the header goes on advertising six reviews for
 * the rest of the minute, and the click it invites lands on "No reviews due".
 * So every screen that moves an item reports it here, and the poll is left to
 * correct drift rather than to carry the count.
 */
@Injectable({ providedIn: 'root' })
export class Counters {
  private readonly api = inject(Api);

  private readonly dueCounter = new Counter();
  private readonly lessonCounter = new Counter();

  readonly due = this.dueCounter.value.asReadonly();
  readonly lessons = this.lessonCounter.value.asReadonly();

  async refresh(): Promise<void> {
    const dueSeen = this.dueCounter.mark();
    const lessonsSeen = this.lessonCounter.mark();
    try {
      const stats = await this.api.stats();
      this.dueCounter.settle(stats.due_now, dueSeen);
      this.lessonCounter.settle(stats.lessons_available, lessonsSeen);
    } catch {
      // A badge is not worth an error banner; the screens themselves report.
    }
  }

  /** An exact count, from a response that already carried one. */
  setDue(count: number): void {
    this.dueCounter.set(count);
  }

  /** Open lessons in the level the lesson screen serves, not everywhere. */
  setLessons(count: number): void {
    this.lessonCounter.set(count);
  }

  /** `n` items just left the due set: answered through, declared or reset. */
  spendDue(n = 1): void {
    this.dueCounter.spend(n);
  }

  /** `n` items are no longer lessons: started, or declared known. */
  spendLessons(n: number): void {
    this.lessonCounter.spend(n);
  }
}
