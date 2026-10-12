import { DatePipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import {
  SumiBanner,
  SumiCard,
  SumiDialog,
  SumiDialogHeader,
  SumiEmptyState,
  SumiErrorState,
  SumiPage,
} from 'sumi-ui/layout';
import { SumiButtonDirective, SumiInputDirective, SumiSelectDirective } from 'sumi-ui/forms';
import {
  SumiDataTable,
  SumiTableCellTemplate,
  type SumiTableColumn,
  type SumiTableRow,
} from 'sumi-ui/charts';

import { Api } from '../../core/api';
import type { Item, ObjectType } from '../../core/api.types';
import { Mnemonic } from '../../core/mnemonic';
import { ContextSentences } from '../../shared/context-sentences/context-sentences';
import { RadicalIllustration } from '../../shared/radical-illustration/radical-illustration';
import { Readings } from '../../shared/readings/readings';
import { Synonyms } from '../../shared/synonyms/synonyms';

const PAGE_SIZE = 100;

/** One table row: the flat fields `sumi-data-table` renders, plus the
 * original `Item` so the cell templates and `rowActivate` can reach
 * everything else (synonyms, readings, mnemonics, …) without a second
 * lookup. */
interface BrowseRow extends SumiTableRow {
  id: number;
  label: string;
  item: Item;
  meaning: string;
  type: string;
  level: number;
  stage: string;
  state: string;
}

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
  imports: [
    ContextSentences,
    DatePipe,
    FormsModule,
    Mnemonic,
    RadicalIllustration,
    Readings,
    SumiBanner,
    SumiButtonDirective,
    SumiCard,
    SumiDataTable,
    SumiDialog,
    SumiDialogHeader,
    SumiEmptyState,
    SumiErrorState,
    SumiInputDirective,
    SumiPage,
    SumiSelectDirective,
    SumiTableCellTemplate,
    Synonyms,
  ],
  templateUrl: './browse.html',
  styleUrl: './browse.scss',
})
export class Browse {
  private readonly api = inject(Api);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected state = '';
  protected objectType = '';
  protected level: number | null = null;
  protected search = '';

  protected readonly columns: SumiTableColumn[] = [
    { key: 'item', label: 'Item' },
    { key: 'meaning', label: 'Meaning' },
    { key: 'type', label: 'Type' },
    { key: 'level', label: 'Lvl' },
    { key: 'stage', label: 'Stage' },
    { key: 'state', label: 'State' },
  ];

  protected readonly levels = signal<number[]>([]);
  protected readonly items = signal<Item[]>([]);
  protected readonly rows = computed<BrowseRow[]>(() =>
    this.items().map((item) => ({
      id: item.subject.id,
      label: item.subject.characters ?? item.subject.slug,
      item,
      meaning: this.meanings(item),
      type: this.typeLabel(item.subject.object_type),
      level: item.subject.level,
      stage: item.progress.stage_name,
      state: this.stateLabel(item.progress.state),
    })),
  );
  /** The row whose details are open, or null. Never fetched: the list
      already carries the full subject. */
  protected readonly detail = signal<Item | null>(null);
  protected readonly detailOpen = signal(false);
  /** Set once the dialog has actually been open, so the `effect()` in the
   *  constructor can tell "never opened yet" (do nothing) apart from "was
   *  open, just closed" (drop `/browse?item=`'s query param again). */
  private readonly dialogWasOpen = signal(false);
  protected readonly total = signal(0);
  protected readonly offset = signal(0);
  /** The table's own selection model -- see sumi-data-table's `[(selection)]`. */
  protected readonly selectedIds = signal<readonly (string | number)[]>([]);
  protected readonly loading = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly status = signal<string | null>(null);
  /** Whether the list has ever loaded successfully -- a failure before that
   * point is the T6 "can't reach the server" scene; a failure afterwards
   * (paging, a re-filter) stays a banner, same as any other action error. */
  protected readonly loaded = signal(false);
  protected readonly loadFailed = signal(false);

  protected readonly selectedCount = computed(() => this.selectedIds().length);

  constructor() {
    // `/browse?level=N&type=<object_type>` (the statistics page's Levels
    // chart links here, `barSelect` -> `openLevel()`) seeds the existing
    // filters on init -- read once from the snapshot rather than a
    // subscription, since this link only ever arrives as a fresh navigation
    // into this page from elsewhere (there is nowhere on Browse itself that
    // could fire it again while already here). The filters show these
    // values like any other, and changing them afterwards works exactly as
    // before -- this never writes a query param back.
    const initialParams = this.route.snapshot.queryParamMap;
    const initialLevel = initialParams.get('level');
    const parsedLevel = initialLevel !== null ? Number(initialLevel) : NaN;
    if (Number.isFinite(parsedLevel)) {
      this.level = parsedLevel;
    }
    const initialType = initialParams.get('type');
    if (initialType) {
      this.objectType = initialType;
    }

    void this.load();
    void this.loadLevels();

    // `/browse?item=<id>` (the statistics page's trouble-items table links
    // here) opens that item's detail dialog on init without waiting for or
    // depending on the filtered list above -- `api.item()` fetches it
    // directly by id. Closing the dialog afterwards drops the param again,
    // via the `effect()` below, so navigating away and back does not
    // silently reopen it.
    this.route.queryParamMap.subscribe((params) => {
      const raw = params.get('item');
      const id = raw ? Number(raw) : NaN;
      if (Number.isFinite(id)) {
        void this.openDetailById(id);
      }
    });

    // Edge-triggered on purpose: `detailOpen()` is already `false` on this
    // first run too (the fetch above is still in flight), and a plain
    // "whenever it's false" check would strip `?item=` before the dialog
    // ever had the chance to open from it. Only a true→false transition --
    // an actual close -- clears the param; opening it sets `dialogWasOpen`
    // so that transition can be detected later.
    effect(() => {
      if (this.detailOpen()) {
        this.dialogWasOpen.set(true);
        return;
      }
      if (this.dialogWasOpen() && this.route.snapshot.queryParamMap.has('item')) {
        this.dialogWasOpen.set(false);
        void this.router.navigate([], {
          queryParams: { item: null },
          queryParamsHandling: 'merge',
          replaceUrl: true,
        });
      }
    });
  }

  private async openDetailById(id: number): Promise<void> {
    try {
      this.openDetail(await this.api.item(id));
    } catch {
      // The id is stale or invalid (e.g. a bookmarked link to a deleted
      // item) -- the dialog simply does not open, so there is nothing for
      // the effect above to close, but the bad param should not linger in
      // the address bar either.
      void this.router.navigate([], {
        queryParams: { item: null },
        queryParamsHandling: 'merge',
        replaceUrl: true,
      });
    }
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
      this.selectedIds.set([]);
      this.error.set(null);
      this.loaded.set(true);
      this.loadFailed.set(false);
    } catch (err) {
      if (this.loaded()) {
        this.error.set((err as Error).message);
      } else {
        this.loadFailed.set(true);
      }
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
    const ids = this.selectedIds().map((id) => id as number);
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
    this.detailOpen.set(true);
  }

  /** `(rowActivate)` hands back the table's flat row, not the `Item` --
   * unwrap it the same way the glyph cell template's own button does. */
  protected openDetailRow(row: SumiTableRow): void {
    this.openDetail((row as BrowseRow).item);
  }

  protected typeLabel(type: ObjectType): string {
    return {
      radical: 'Radical',
      kanji: 'Kanji',
      vocabulary: 'Vocabulary',
      kana_vocabulary: 'Kana',
    }[type];
  }

  protected stateLabel(state: string): string {
    return (
      { new: 'new', learning: 'in review cycle', known: 'known', suspended: 'hidden' }[state] ??
      state
    );
  }

  /** Keep both the open dialog and the underlying list row current after a
   * save -- `detail` and the matching row in `items` are separate objects by
   * the time either changes, so both need updating in place. */
  protected onSynonymsChange(subjectId: number, synonyms: string[]): void {
    const apply = (item: Item): Item =>
      item.subject.id === subjectId ? { ...item, subject: { ...item.subject, synonyms } } : item;
    this.items.update((items) => items.map(apply));
    this.detail.update((item) => (item ? apply(item) : item));
  }

  protected meanings(item: Item): string {
    return item.subject.meanings
      .filter((meaning) => meaning.accepted_answer !== false)
      .map((meaning) => meaning.meaning)
      .join(', ');
  }
}
