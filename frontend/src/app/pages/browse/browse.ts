import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api } from '../../core/api';
import type { Item, ObjectType } from '../../core/api.types';

const PAGE_SIZE = 100;

/**
 * Browsing and bulk-declaring.
 *
 * The filters exist to make declaring realistic rather than to be thorough:
 * "alle Kanji bis Level 20, die noch als neu gelten" has to be one query and
 * one gesture, or nobody will use the feature that the whole app is built
 * around.
 */
@Component({
  selector: 'app-browse',
  imports: [FormsModule],
  templateUrl: './browse.html',
  styleUrl: './browse.scss',
})
export class Browse {
  private readonly api = inject(Api);

  protected state = '';
  protected objectType = '';
  protected level: number | null = null;
  protected search = '';

  protected readonly items = signal<Item[]>([]);
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

  /** "Das kann ich", for everything ticked. */
  markKnown(knownStage?: number): void {
    void this.apply(
      (ids) => this.api.markKnown(ids, knownStage),
      (n) => `${n} Items als beherrscht markiert.`,
    );
  }

  reset(): void {
    void this.apply(
      (ids) => this.api.resetItems(ids),
      (n) => `${n} Items auf Apprentice I zurückgesetzt.`,
    );
  }

  suspend(): void {
    void this.apply(
      (ids) => this.api.suspendItems(ids),
      (n) => `${n} Items ausgeblendet.`,
    );
  }

  unsuspend(): void {
    void this.apply(
      (ids) => this.api.unsuspendItems(ids),
      (n) => `${n} Items wieder eingeblendet.`,
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

  protected typeLabel(type: ObjectType): string {
    return { radical: 'Radikal', kanji: 'Kanji', vocabulary: 'Vokabel', kana_vocabulary: 'Kana' }[
      type
    ];
  }

  protected stateLabel(state: string): string {
    return (
      { new: 'neu', learning: 'im Lernzyklus', known: 'beherrscht', suspended: 'ausgeblendet' }[
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
}
