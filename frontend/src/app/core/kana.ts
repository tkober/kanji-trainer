/**
 * Romaji to hiragana, as the learner types.
 *
 * This is the browser's copy of `romaji_to_hiragana` in backend/app/answers.py
 * and the two must agree. It is duplicated rather than shared because the jobs
 * differ: here it runs on every keystroke so that typing "kan" shows かん
 * appearing — half the feedback of a reading question — while the backend's
 * copy is a fallback for answers that arrive as romaji anyway.
 *
 * Trailing consonants are deliberately left as romaji: mid-word "k" must stay
 * visible until its vowel arrives, or the input would swallow keystrokes.
 */

const DIGRAPHS: Record<string, string> = {
  kya: 'きゃ', kyu: 'きゅ', kyo: 'きょ', gya: 'ぎゃ', gyu: 'ぎゅ', gyo: 'ぎょ',
  sha: 'しゃ', shu: 'しゅ', sho: 'しょ', shi: 'し',
  ja: 'じゃ', ju: 'じゅ', jo: 'じょ', ji: 'じ',
  cha: 'ちゃ', chu: 'ちゅ', cho: 'ちょ', chi: 'ち', tsu: 'つ',
  nya: 'にゃ', nyu: 'にゅ', nyo: 'にょ',
  hya: 'ひゃ', hyu: 'ひゅ', hyo: 'ひょ', bya: 'びゃ', byu: 'びゅ', byo: 'びょ',
  pya: 'ぴゃ', pyu: 'ぴゅ', pyo: 'ぴょ',
  mya: 'みゃ', myu: 'みゅ', myo: 'みょ',
  rya: 'りゃ', ryu: 'りゅ', ryo: 'りょ',
};

const BASE: Record<string, string> = {
  ka: 'か', ki: 'き', ku: 'く', ke: 'け', ko: 'こ',
  ga: 'が', gi: 'ぎ', gu: 'ぐ', ge: 'げ', go: 'ご',
  sa: 'さ', su: 'す', se: 'せ', so: 'そ',
  za: 'ざ', zu: 'ず', ze: 'ぜ', zo: 'ぞ',
  ta: 'た', te: 'て', to: 'と', tu: 'つ',
  da: 'だ', di: 'ぢ', du: 'づ', de: 'で', do: 'ど',
  na: 'な', ni: 'に', nu: 'ぬ', ne: 'ね', no: 'の',
  ha: 'は', hi: 'ひ', fu: 'ふ', hu: 'ふ', he: 'へ', ho: 'ほ',
  ba: 'ば', bi: 'び', bu: 'ぶ', be: 'べ', bo: 'ぼ',
  pa: 'ぱ', pi: 'ぴ', pu: 'ぷ', pe: 'ぺ', po: 'ぽ',
  ma: 'ま', mi: 'み', mu: 'む', me: 'め', mo: 'も',
  ya: 'や', yu: 'ゆ', yo: 'よ',
  ra: 'ら', ri: 'り', ru: 'る', re: 'れ', ro: 'ろ',
  wa: 'わ', wo: 'を', wi: 'ゐ', we: 'ゑ',
  a: 'あ', i: 'い', u: 'う', e: 'え', o: 'お',
};

const TABLE: Record<string, string> = { ...DIGRAPHS, ...BASE };
const KEYS = Object.keys(TABLE).sort((a, b) => b.length - a.length);
const VOWELS = 'aiueo';

export function romajiToKana(value: string): string {
  const text = value.toLowerCase();
  let out = '';
  let index = 0;

  while (index < text.length) {
    const char = text[index];

    // Kana the learner typed directly (or an IME produced) passes through.
    if (char > '　') {
      out += char;
      index += 1;
      continue;
    }

    // A doubled consonant becomes っ — but not "nn", which is ん.
    if (
      !VOWELS.includes(char) &&
      char !== 'n' &&
      /[a-z]/.test(char) &&
      text[index + 1] === char
    ) {
      out += 'っ';
      index += 1;
      continue;
    }

    if (char === 'n') {
      const next = text[index + 1];
      if (next === "'") {
        out += 'ん';
        index += 2;
        continue;
      }
      // "nn" is the other spelling of ん, and the one most IMEs insist on, so
      // it is what the fingers do. The second n is swallowed only where it
      // could not open a syllable of its own: "onna" stays おんな, not おんあ.
      if (next === 'n' && !opensSyllable(text[index + 2])) {
        out += 'ん';
        index += 2;
        continue;
      }
      // Only commit ん once the next character proves it is not the start of
      // な/に/… — while the learner is still mid-word, "n" stays "n".
      if (next !== undefined && !VOWELS.includes(next) && next !== 'y') {
        out += 'ん';
        index += 1;
        continue;
      }
      if (next === undefined) {
        out += 'n';
        index += 1;
        continue;
      }
    }

    const key = KEYS.find((candidate) => text.startsWith(candidate, index));
    if (key) {
      out += TABLE[key];
      index += key.length;
      continue;
    }

    out += char;
    index += 1;
  }

  return out;
}

/** Whether a character could open a syllable after an "n": a vowel, or "y". */
function opensSyllable(char: string | undefined): boolean {
  return char !== undefined && (VOWELS.includes(char) || char === 'y');
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
 * learner has finished ("sann") and one still opening a syllable ("sanna").
 * While they are appending, the new keystrokes are added to the buffer rather
 * than read out of the field. Any other edit — backspace, paste, a caret
 * placed in the middle — falls back to what the field now holds, which is kana
 * as far as it was converted and converts to itself.
 */
export function absorbInput(typed: string, shown: string, buffer: string): string {
  return typed.startsWith(shown) ? buffer + typed.slice(shown.length) : typed;
}
