import { DatePipe } from '@angular/common';
import { Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api } from '../../core/api';
import type { Item, ObjectType } from '../../core/api.types';
import { Mnemonic } from '../../core/mnemonic';
import { type ReadingGroup, readingGroups } from '../../core/readings';

const PAGE_SIZE = 100;

/**
 * Browsing and bulk-declaring.
 *
 * The filters exist to make declaring realistic rather than to be thorough:
 * "all kanji up to level 20 that still count as new" has to be one query and
 * one gesture, or nobody will use the feature that the whole app is built
 * around.
 */
@Component({
  selector: 'app-browse',
  imports: [DatePipe, FormsModule, Mnemonic],
  templateUrl: './browse.html',
  styleUrl: './browse.scss',
})
export class Browse {
  private readonly api = inject(Api);
  private readonly dialog = viewChild<ElementRef<HTMLDialogElement>>('detailDialog');

  protected state = '';
  protected objectType = '';
  protected level: number | null = null;
  protected search = '';

  protected readonly levels = signal<number[]>([]);
  protected readonly items = signal<Item[]>([]);
  /** The row whose details are open, or null. Never fetched: the list
      already carries the full subject. */
  protected readonly detail = signal<Item | null>(null);
  protected readonly total = signal(0);
  protected readonly offset = signal(0);
  protected readonly selected = signal<Set<number>>(new Set());
  protected readonly loading = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly status = signal<string | null>(null);

  protected readonly selectedCount = computed(() => this.selected().size);
  protected readonly allSelected = computed(
    () => this.items().length > 0 && this.selected().size === this.items().length,
  );

  constructor() {
    void this.load();
    void this.loadLevels();

    // `showModal()` is what gives the dialog its backdrop, its focus trap and
    // Escape; no attribute does that, so the signal drives it imperatively.
    effect(() => {
      const element = this.dialog()?.nativeElement;
      if (!element) {
        return;
      }
      if (this.detail() && !element.open) {
        element.showModal();
      } else if (!this.detail() && element.open) {
        element.close();
      }
    });
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const page = await this.api.items({
        state: this.state || undefined,
        object_type: this.objectType || undefined,
        level: this.level ?? undefined,
        search: this.search || undefined,
        limit: PAGE_SIZE,
        offset: this.offset(),
      });
      this.items.set(page.items);
      this.total.set(page.total);
      this.selected.set(new Set());
      this.error.set(null);
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.loading.set(false);
    }
  }

  /** The levels that exist, so the filter never offers an empty one. */
  private async loadLevels(): Promise<void> {
    try {
      this.levels.set(await this.api.levels());
    } catch {
      // Not worth an error banner: the filter falls back to "all levels" and
      // the table itself, which has its own error handling, still works.
    }
  }

  applyFilters(): void {
    this.offset.set(0);
    void this.load();
  }

  pageBack(): void {
    this.offset.update((value) => Math.max(0, value - PAGE_SIZE));
    void this.load();
  }

  pageForward(): void {
    this.offset.update((value) => value + PAGE_SIZE);
    void this.load();
  }

  toggle(id: number): void {
    const next = new Set(this.selected());
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    this.selected.set(next);
  }

  toggleAll(): void {
    this.selected.set(
      this.allSelected() ? new Set() : new Set(this.items().map((item) => item.subject.id)),
    );
  }

  /** "I know this", for everything ticked. */
  markKnown(knownStage?: number): void {
    void this.apply(
      (ids) => this.api.markKnown(ids, knownStage),
      (n) => `${n} items marked as known.`,
    );
  }

  reset(): void {
    void this.apply(
      (ids) => this.api.resetItems(ids),
      (n) => `${n} items reset to Apprentice I.`,
    );
  }

  suspend(): void {
    void this.apply(
      (ids) => this.api.suspendItems(ids),
      (n) => `${n} items hidden.`,
    );
  }

  unsuspend(): void {
    void this.apply(
      (ids) => this.api.unsuspendItems(ids),
      (n) => `${n} items unhidden.`,
    );
  }

  private async apply(
    action: (ids: number[]) => Promise<{ changed: number }>,
    message: (changed: number) => string,
  ): Promise<void> {
    const ids = [...this.selected()];
    if (ids.length === 0) {
      return;
    }
    this.busy.set(true);
    this.status.set(null);
    try {
      const result = await action(ids);
      this.status.set(message(result.changed));
      await this.load();
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }

  openDetail(item: Item): void {
    this.detail.set(item);
  }

  closeDetail(): void {
    this.detail.set(null);
  }

  /** A click on the backdrop rather than on the card inside it. */
  backdropClick(event: MouseEvent): void {
    if (event.target === this.dialog()?.nativeElement) {
      this.closeDetail();
    }
  }

  protected typeLabel(type: ObjectType): string {
    return { radical: 'Radical', kanji: 'Kanji', vocabulary: 'Vocabulary', kana_vocabulary: 'Kana' }[
      type
    ];
  }

  protected stateLabel(state: string): string {
    return (
      { new: 'new', learning: 'in review cycle', known: 'known', suspended: 'hidden' }[
        state
      ] ?? state
    );
  }

  protected meanings(item: Item): string {
    return item.subject.meanings
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }

  protected readings(item: Item): ReadingGroup[] {
    return readingGroups(item.subject.readings);
  }
}
