/**
 * WaniKani's mnemonic markup, turned into something a browser renders.
 *
 * The API tags the pieces of a mnemonic -- `<radical>`, `<kanji>`,
 * `<vocabulary>`, `<reading>`, `<meaning>`, `<ja>` -- and that tagging carries
 * half the sentence: "the <radical>toe</radical> is <kanji>above</kanji> the
 * ground" makes two different claims, and flattened to plain text it makes
 * one. The importer stored the markup all along; Angular's sanitiser dropped
 * the unknown elements on the way to the screen, keeping the words and losing
 * which was which.
 *
 * The tags become spans with a class, which the sanitiser keeps. Nothing is
 * bypassed here -- WaniKani's payload still goes through it -- and anything
 * that is not one of the six known tags is left for it to deal with.
 *
 * The colours live in styles.scss rather than in the components that use this:
 * emulated view encapsulation tags the elements the template renders, and
 * content inserted through `innerHTML` never gets that attribute, so a
 * component style sheet cannot reach it.
 */

import { Pipe, type PipeTransform } from '@angular/core';

const TAG = /<(\/?)(radical|kanji|vocabulary|reading|meaning|ja)>/gi;

export function highlightMnemonic(text: string): string {
  return text.replace(TAG, (_match, closing: string, tag: string) =>
    closing ? '</span>' : `<span class="wk wk-${tag.toLowerCase()}">`,
  );
}

@Pipe({ name: 'mnemonic' })
export class Mnemonic implements PipeTransform {
  transform(value: string | null | undefined): string {
    return value ? highlightMnemonic(value) : '';
  }
}
