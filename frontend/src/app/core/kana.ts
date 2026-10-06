import { toHiragana } from 'wanakana';

/**
 * Romaji to hiragana, as the learner types.
 *
 * This is wanakana — WaniKani's own converter — in IME mode, and the browser's
 * counterpart of `romaji_to_hiragana` in backend/app/answers.py; the two must
 * agree. IME mode matters for ん: "nn" closes it at once, whatever follows, as
 * every IME does. So "sennenn" is せんえん, and おんな takes "onnna" or "on'na".
 *
 * Trailing consonants are deliberately left as romaji: "kan" shows かn until
 * the next key says whether the n was ん or the start of な/に/….
 */

export function romajiToKana(value: string): string {
  return toHiragana(value.toLowerCase(), { IMEMode: true });
}

/** Whether a string is already kana (or empty) — used to skip conversion. */
export function isKana(value: string): boolean {
  return /^[぀-ヿー\s]*$/.test(value);
}

/** Commit a trailing bare "n" to ん, for the moment the answer is submitted. */
export function finaliseKana(value: string): string {
  return value.endsWith('n') ? `${value.slice(0, -1)}ん` : value;
}

/**
 * The romaji behind a fresh input value.
 *
 * The field shows the converted text, so taking it back as it stands loses the
 * romaji that produced it — and with it the difference between an ん the
 * learner has finished ("sann") and a bare trailing "n" still waiting on
 * whatever key comes next ("san", which may yet become さん or さに or ...).
 * While they are appending, the new keystrokes are added to the buffer rather
 * than read out of the field. Any other edit — backspace, paste, a caret
 * placed in the middle — falls back to what the field now holds, which is kana
 * as far as it was converted and converts to itself.
 */
export function absorbInput(typed: string, shown: string, buffer: string): string {
  return typed.startsWith(shown) ? buffer + typed.slice(shown.length) : typed;
}
