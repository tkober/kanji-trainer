import { Component, ElementRef, effect, inject, input, model, output, signal, viewChild } from '@angular/core';

import { Api } from '../../core/api';
import { HoldFocus } from '../../core/hold-focus';

/**
 * The learner's own synonyms for one item's *meaning* (issue #33), modelled
 * on WaniKani's "User Synonyms" -- shared by the review "Show item" panel,
 * the lessons reading phase and the browse detail dialog, same reasoning as
 * `shared/readings` and `shared/context-sentences`.
 *
 * `synonyms` is a two-way `model()` rather than an input plus an output: the
 * three hosts above each keep their own copy of the subject around (the
 * browse list item, the lessons item, the review result's subject), and all
 * of them need it updated in place after a save so reopening the same item
 * shows the new list without a refetch -- exactly the reason `Hotkeys.open`
 * is a `model()` too.
 *
 * `done` fires after every save *and* every cancel. `Review` listens for it
 * to hand focus back to the answer field: this component's own text input
 * legitimately takes the caret while the learner types a synonym (the phone
 * keyboard stays up because focus moves input-to-input, never blurring), but
 * once it is done with it the answer field is what should have it again, for
 * the next Enter to reach "Next" rather than nothing.
 */
@Component({
  selector: 'app-synonyms',
  imports: [HoldFocus],
  templateUrl: './synonyms.html',
  styleUrl: './synonyms.scss',
})
export class Synonyms {
  private readonly api = inject(Api);
  private readonly draftField = viewChild<ElementRef<HTMLInputElement>>('draftField');

  readonly subjectId = input.required<number>();
  readonly synonyms = model.required<string[]>();
  readonly done = output<void>();

  protected readonly adding = signal(false);
  protected readonly draft = signal('');
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);

  constructor() {
    // The draft field only exists in the DOM once `adding` is true (it is
    // behind an `@if`), so focusing it eagerly in `startAdding()` would land
    // on nothing -- this re-runs once Angular has actually rendered it,
    // exactly like `Review`'s own `keepFocus` effect on the answer field.
    effect(() => {
      if (this.adding()) {
        this.draftField()?.nativeElement.focus();
      }
    });
  }

  protected startAdding(): void {
    this.draft.set('');
    this.error.set(null);
    this.adding.set(true);
  }

  protected onInput(event: Event): void {
    this.draft.set((event.target as HTMLInputElement).value);
  }

  /**
   * Enter saves, Esc cancels -- and neither must reach the surrounding
   * screen. `Review.onKeydown` reacts to a bare `Enter`/`Esc`/`F`/`?` while
   * feedback is on screen, which is exactly when this component is shown, so
   * every keydown here is stopped before it can bubble into that handler.
   */
  protected onKeydown(event: KeyboardEvent): void {
    event.stopPropagation();
    if (event.key === 'Enter') {
      event.preventDefault();
      void this.save();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.cancel();
    }
  }

  protected cancel(): void {
    this.adding.set(false);
    this.draft.set('');
    this.error.set(null);
    this.done.emit();
  }

  protected async save(): Promise<void> {
    const value = this.draft().trim();
    if (!value || this.busy()) {
      return;
    }
    await this.persist([...this.synonyms(), value]);
  }

  protected async remove(value: string): Promise<void> {
    if (this.busy()) {
      return;
    }
    await this.persist(this.synonyms().filter((entry) => entry !== value));
  }

  /** Round-trips through the server rather than updating optimistically --
   * the stored list is normalised (trimmed, deduped) and the 8-synonym limit
   * is enforced there, so what comes back is the truth, not a guess. */
  private async persist(next: string[]): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      const stored = await this.api.setSynonyms(this.subjectId(), next);
      this.synonyms.set(stored);
      this.adding.set(false);
      this.draft.set('');
      this.done.emit();
    } catch (err) {
      this.error.set((err as Error).message);
    } finally {
      this.busy.set(false);
    }
  }
}
