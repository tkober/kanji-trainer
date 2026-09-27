/** Counts the code points in a string, not UTF-16 units.
 *
 * Used to size the `.characters` display font: every WaniKani glyph (kanji,
 * kana, including small っ) is full-width and advances about 1em, so glyph
 * count times font size is the width the string needs. `.length` would
 * overcount characters outside the BMP (rare here, but cheap to get right).
 */
export function glyphCount(text: string): number {
  return [...text].length;
}
