import { Component, input } from '@angular/core';

import type { ContextSentence } from '../../core/api.types';

interface Segment {
  text: string;
  match: boolean;
}

/**
 * Splits one sentence around every occurrence of the item's own
 * `characters`, so the template can highlight them with a plain
 * `@if`/interpolation loop instead of `innerHTML` -- both the matched and
 * the surrounding text stay ordinary interpolated strings, which Angular
 * escapes on the way to the DOM, so nothing from the API (or, if ever
 * hand-added, from the learner) can inject markup here.
 */
function highlightSegments(sentence: string, characters: string | null): Segment[] {
  if (!characters) {
    return [{ text: sentence, match: false }];
  }
  const segments: Segment[] = [];
  let rest = sentence;
  let index = rest.indexOf(characters);
  while (index !== -1) {
    if (index > 0) {
      segments.push({ text: rest.slice(0, index), match: false });
    }
    segments.push({ text: rest.slice(index, index + characters.length), match: true });
    rest = rest.slice(index + characters.length);
    index = rest.indexOf(characters);
  }
  if (rest) {
    segments.push({ text: rest, match: false });
  }
  return segments;
}

/**
 * WaniKani's example sentences for a vocabulary item (issue #32), shared by
 * the review "Show item" panel, the lessons reading phase and the browse
 * detail dialog -- same reasoning as `shared/readings`. Renders nothing when
 * `sentences` is empty, which is every radical and kanji, and any vocabulary
 * WaniKani has none for.
 */
@Component({
  selector: 'app-context-sentences',
  templateUrl: './context-sentences.html',
  styleUrl: './context-sentences.scss',
})
export class ContextSentences {
  readonly sentences = input.required<ContextSentence[]>();
  /** The item's own characters, to highlight inside each Japanese sentence. */
  readonly characters = input<string | null>(null);

  protected segments(sentence: string): Segment[] {
    return highlightSegments(sentence, this.characters());
  }
}
