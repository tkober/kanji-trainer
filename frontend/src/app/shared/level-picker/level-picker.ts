import {
  Component,
  ElementRef,
  HostListener,
  computed,
  effect,
  input,
  output,
  signal,
  viewChild,
  viewChildren,
} from '@angular/core';
import { SumiButtonDirective } from 'sumi-ui/forms';

import type { LevelSummary } from '../../core/api.types';

/**
 * The level picker: replaces the plain `<select>` the lessons page used to
 * show "Level 5 - 12 left" with -- a custom listbox-button so the trigger can
 * show the open counts per type (radicals / kanji / vocabulary) as coloured
 * chips, which a native `<option>` cannot render.
 *
 * Follows the WAI-ARIA "select-only combobox" / listbox-button pattern: the
 * trigger is `aria-haspopup="listbox"` and toggles `aria-expanded`, the panel
 * is `role="listbox"`, its rows are `role="option"`, and the active row is
 * tracked with `aria-activedescendant` rather than real DOM focus -- the
 * trigger button keeps focus throughout, exactly as a native `<select>` would.
 *
 * All state lives in signals -- the app is zoneless, so anything the template
 * reacts to has to be one.
 */
@Component({
  selector: 'app-level-picker',
  imports: [SumiButtonDirective],
  templateUrl: './level-picker.html',
  styleUrl: './level-picker.scss',
})
export class LevelPicker {
  readonly levels = input.required<LevelSummary[]>();
  readonly level = input.required<number | null>();
  readonly picked = output<number>();

  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');
  private readonly trigger = viewChild<ElementRef<HTMLButtonElement>>('trigger');
  private readonly optionEls = viewChildren<ElementRef<HTMLElement>>('optionEl');

  protected readonly open = signal(false);
  /** Open the panel from the trigger's left edge rather than its right one --
   * set on open, from where the trigger actually sits on screen. */
  protected readonly alignStart = signal(false);
  /** The level under keyboard focus while the panel is open -- not necessarily
   * the picked one, exactly like a native listbox's active descendant. */
  protected readonly activeLevel = signal<number | null>(null);

  protected readonly current = computed<LevelSummary | null>(() => {
    const level = this.level();
    return this.levels().find((entry) => entry.level === level) ?? null;
  });

  protected readonly activeIndex = computed(() => {
    const active = this.activeLevel();
    return this.levels().findIndex((entry) => entry.level === active);
  });

  protected readonly panelId = 'level-picker-panel';

  constructor() {
    // Scroll the current level into view whenever the panel opens, and seed
    // the active option so ArrowUp/Down has somewhere to start from.
    effect(() => {
      if (!this.open()) {
        return;
      }
      const level = this.current()?.level ?? this.levels()[0]?.level ?? null;
      this.activeLevel.set(level);
      queueMicrotask(() => this.scrollActiveIntoView());
    });
  }

  @HostListener('document:click', ['$event'])
  protected onDocumentClick(event: MouseEvent): void {
    if (!this.open()) {
      return;
    }
    const target = event.target as Node;
    const panelEl = this.panel()?.nativeElement;
    const triggerEl = this.trigger()?.nativeElement;
    if (panelEl?.contains(target) || triggerEl?.contains(target)) {
      return;
    }
    this.close();
  }

  protected optionId(level: number): string {
    return `level-picker-option-${level}`;
  }

  protected toggle(): void {
    this.open() ? this.close() : this.openPanel();
  }

  protected openPanel(): void {
    if (this.levels().length === 0) {
      return;
    }
    const rect = this.trigger()?.nativeElement.getBoundingClientRect();
    this.alignStart.set(!!rect && rect.left + rect.width / 2 < window.innerWidth / 2);
    this.open.set(true);
  }

  protected close(returnFocus = false): void {
    this.open.set(false);
    if (returnFocus) {
      this.trigger()?.nativeElement.focus();
    }
  }

  protected select(level: number): void {
    this.close();
    if (level !== this.level()) {
      this.picked.emit(level);
    }
  }

  /**
   * All keyboard handling lives on the trigger, because that is where focus
   * stays throughout -- the "select-only combobox" pattern never moves real
   * DOM focus into the listbox, it tracks the active option with
   * `aria-activedescendant` instead. A handler on the panel would never fire.
   */
  protected onTriggerKeydown(event: KeyboardEvent): void {
    if (!this.open()) {
      if (
        event.key === 'Enter' ||
        event.key === ' ' ||
        event.key === 'ArrowDown' ||
        event.key === 'ArrowUp'
      ) {
        event.preventDefault();
        this.openPanel();
      }
      return;
    }
    this.onPanelKeydown(event);
  }

  private onPanelKeydown(event: KeyboardEvent): void {
    const entries = this.levels();
    if (entries.length === 0) {
      return;
    }

    switch (event.key) {
      case 'ArrowDown': {
        event.preventDefault();
        this.moveActive(1);
        break;
      }
      case 'ArrowUp': {
        event.preventDefault();
        this.moveActive(-1);
        break;
      }
      case 'Home': {
        event.preventDefault();
        this.activeLevel.set(entries[0].level);
        this.scrollActiveIntoView();
        break;
      }
      case 'End': {
        event.preventDefault();
        this.activeLevel.set(entries[entries.length - 1].level);
        this.scrollActiveIntoView();
        break;
      }
      case 'Enter':
      case ' ': {
        event.preventDefault();
        const active = this.activeLevel();
        if (active !== null) {
          this.select(active);
        }
        break;
      }
      case 'Escape': {
        event.preventDefault();
        this.close(true);
        break;
      }
      case 'Tab': {
        this.close();
        break;
      }
    }
  }

  private moveActive(delta: number): void {
    const entries = this.levels();
    const index = this.activeIndex();
    const next = Math.max(0, Math.min(entries.length - 1, (index < 0 ? 0 : index) + delta));
    this.activeLevel.set(entries[next].level);
    this.scrollActiveIntoView();
  }

  private scrollActiveIntoView(): void {
    const active = this.activeLevel();
    if (active === null) {
      return;
    }
    const el = this.optionEls().find(
      (ref) => ref.nativeElement.dataset['level'] === String(active),
    );
    el?.nativeElement.scrollIntoView({ block: 'nearest' });
  }

  protected isDone(entry: LevelSummary): boolean {
    return entry.open_count === 0;
  }
}
