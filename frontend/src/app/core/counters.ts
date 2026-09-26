import { Injectable, WritableSignal, inject, signal } from '@angular/core';

import { Api } from './api';

/** One header value: what it is, and whether a poll may still overwrite it. */
class Tracked<T> {
  readonly value: WritableSignal<T>;

  /**
   * How many times a screen has set this value.
   *
   * A poll issued before an answer carries a value taken before it too, so
   * landing late it would put the stale number straight back — the bug this
   * file exists to fix, only rarer and harder to see. Comparing the count of
   * edits before and after the request says whether that happened. Each
   * number keeps its own, or a review would discard a fresh lesson count.
   */
  private edits = 0;

  constructor(initial: T) {
    this.value = signal(initial);
  }

  mark(): number {
    return this.edits;
  }

  set(value: T): void {
    this.edits += 1;
    this.value.set(value);
  }

  update(change: (current: T) => T): void {
    this.edits += 1;
    this.value.update(change);
  }

  /** Take a polled value, unless a screen has said something newer. */
  settle(value: T, seen: number): void {
    if (this.edits === seen) {
      this.value.set(value);
    }
  }
}

/** A badge number, which a screen can also subtract from without refetching. */
class Counter extends Tracked<number> {
  constructor() {
    super(0);
  }

  spend(n: number): void {
    this.update((current) => Math.max(0, current - n));
  }
}

/**
 * What the header shows, in one place: the two badges and the level.
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
  private readonly levelValue = new Tracked<number | null>(null);

  readonly due = this.dueCounter.value.asReadonly();
  readonly lessons = this.lessonCounter.value.asReadonly();
  /** The level the lesson queue is on; null when nothing is left to learn. */
  readonly level = this.levelValue.value.asReadonly();

  async refresh(): Promise<void> {
    const dueSeen = this.dueCounter.mark();
    const lessonsSeen = this.lessonCounter.mark();
    const levelSeen = this.levelValue.mark();
    try {
      const stats = await this.api.stats();
      this.dueCounter.settle(stats.due_now, dueSeen);
      this.lessonCounter.settle(stats.lessons_available, lessonsSeen);
      this.levelValue.settle(stats.current_level, levelSeen);
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

  /**
   * The lowest level with lessons left, from a response that already named it.
   *
   * Only `current_level` belongs here, never the level the lesson screen is
   * *showing*: the picker can walk ahead to level 40 while level 3 is still
   * open, and the header would then advertise a level the learner is not on
   * until the next poll put it back.
   */
  setLevel(level: number | null): void {
    this.levelValue.set(level);
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
