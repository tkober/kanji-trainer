import { Component, effect, inject, input, signal } from '@angular/core';

import { Api } from '../../core/api';
import type { ObjectType } from '../../core/api.types';

/**
 * A radical's WaniKani mnemonic illustration, next to its written mnemonic.
 *
 * Renders nothing for anything that is not a radical, and nothing while an
 * item is unavailable -- WaniKani does not have art for every radical yet.
 * The check-and-maybe-fetch happens on the backend (`GET .../illustration`);
 * this component only ever asks once per subject it is shown for and renders
 * an `<img>` at the SVG endpoint when told to.
 *
 * Never used where the answer is not revealed yet -- see the review question
 * and the lesson quiz, which do not include this component at all. An
 * `<img>` cannot steal focus, so it is safe next to the `sumiHoldFocus`
 * buttons on the answering screens even so.
 *
 * Errors are swallowed: a missing illustration is not worth an error banner,
 * the JSON endpoint already reports "unavailable" rather than failing, and a
 * network hiccup on top of that should simply render nothing.
 */
@Component({
  selector: 'app-radical-illustration',
  imports: [],
  templateUrl: './radical-illustration.html',
  styleUrl: './radical-illustration.scss',
})
export class RadicalIllustration {
  private readonly api = inject(Api);

  readonly subjectId = input.required<number>();
  readonly objectType = input.required<ObjectType>();

  protected readonly available = signal(false);
  protected readonly alt = signal<string | null>(null);

  constructor() {
    // Re-runs whenever either input changes -- a new item on the same screen
    // (next lesson card, next browse row) must not keep showing the old art.
    effect(() => {
      const id = this.subjectId();
      const type = this.objectType();
      this.available.set(false);
      this.alt.set(null);
      if (type === 'radical') {
        void this.load(id);
      }
    });
  }

  private async load(id: number): Promise<void> {
    try {
      const result = await this.api.illustration(id);
      // The input may have moved on while this was in flight; a stale
      // response must not overwrite what the screen has since asked for.
      if (this.subjectId() !== id) {
        return;
      }
      this.available.set(result.available);
      this.alt.set(result.alt);
    } catch {
      // Swallowed -- see the class doc.
    }
  }

  protected svgUrl(id: number): string {
    return `/api/items/${id}/illustration.svg`;
  }
}
