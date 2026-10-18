# JLPT levels

A checked-in snapshot of approximate JLPT levels for kanji and vocabulary.
WaniKani has no JLPT data, so the trainer brings its own.

There are no official JLPT lists since the 2010 revision. These levels are
Jonathan Waller's estimates (tanos.co.uk), the source most tools use, Jisho
included. Treat them as a guide, not as the exam syllabus.

| File | Rows | Columns | Source |
|---|---|---|---|
| `vocab.csv` | 8131 | `jlpt`, `expression`, `reading` | [jamsinclair/open-anki-jlpt-decks](https://github.com/jamsinclair/open-anki-jlpt-decks/tree/1ad66734417aca9dbcca6b2d5ee440cb13ab3ba0), `src/n*.csv` |
| `kanji.csv` | 2211 | `jlpt`, `kanji` | [davidluzgouveia/kanji-data](https://github.com/davidluzgouveia/kanji-data/tree/00fd7079c3890f430759536f91aa5e854ec0ca4f), `kanji.json` field `jlpt_new` |
| `supplement.csv` | 26 | `jlpt`, `expression`, `reading` | hand-picked, see "Supplement" below -- every row checked against [Jisho's API](https://jisho.org/api/v1/search/words?keyword=%3Cword%3E) `jlpt` field, 2026-10-18 |

`jlpt` is the level as a number, 5 for N5 down to 1 for N1.

## Notes on the vocabulary

`expression` and `reading` are copied verbatim, so the upstream notation is
still there and has to be normalised before matching:

- Variants separated by `; ` (`足; 脚`, `まいげつ; まいつき`).
- Suru verbs with ` (する)` in the reading (`べんきょう (する)`).
- Affixes and context in `～`/`〜` and parentheses (`～ごろ`, `(花を〜) 生ける, 活ける`).

97 words are listed at two levels and keep both rows. Use the easier one.

## Matching gaps found measuring against real text

Measured by tokenising ~1200 NHK News Web headlines (`cat0`-`cat7` RSS feeds)
with fugashi/unidic-lite the way `get_jlpt_levels`' real caller,
jp-reading-practice, does (`orthBase` as the lemma, `kanaBase` folded to
hiragana as its reading), then running every distinct `(lemma, reading)`
through the same lookup the MCP tool uses: 3643 distinct candidates, 1729
misses (47.5%) before this round of fixes.

Most of that is not fixable here: 345 are proper nouns (never JLPT material),
113 are bare numbers, and the great majority of the rest is genuinely
news-register vocabulary beyond N1 that Waller's own list simply never
covered (`献花`, `退任`, `搬出`, ...) -- adding levels for those would be
guessing, which the rule below is written to avoid. Two real gaps did show
up, fixed in `app/jlpt.py` rather than here (the rows below stay exactly as
upstream ships them):

- **Okurigana variants.** `vocab.csv` still carries some pre-reform
  okurigana (`落る`/`おちる`, `落す`/`おとす`) that a real tokeniser's base
  form never produces (`落ちる`, `落とす`). `word_level`/`jlpt_level` now
  also match on "same reading, same kanji in the same order" once the exact
  expression is not listed -- see `app/jlpt.py`'s module docstring and
  `_fallback_level`.
- **Adverbs with a trailing と/に.** `ゆっくりと` is listed as one word, but
  a tokeniser splits the と off as its own particle, so the query that
  actually reaches `get_jlpt_levels` is just `ゆっくり`. Matched the same
  way, in both directions for に (see `_adverb_variant_level` for why not
  と in reverse -- it also doubles as the plain conditional/quotative
  particle, and stripping it there would be wrong).

These two rules took the miss rate from 47.46% to 47.10% on the same sample
(13 words) -- modest, because most of what they fix (old okurigana, split
adverbs) is a small slice of a mostly-unfixable-by-rule miss list.

## Supplement

`supplement.csv` (same columns as `vocab.csv`) holds words the measurement
above showed are common in real text and missing from `vocab.csv`
*entirely* (not a notation difference the rules above can bridge) -- never
a guess: every row was checked against
[Jisho's own JLPT tag](https://jisho.org/api/v1/search/words?keyword=%3Cword%3E)
(`data[].jlpt`), which it derives from the same Waller lists, and kept only
when Jisho's own reading for that tag is hiragana-identical to the one
being added -- an expression can be two different words at two different
readings (same reason `_vocab_level`/`word_level` require a reading match
at all), so a tag on an unrelated sense of the same spelling does not count.
One candidate was dropped for exactly that reason: katakana `ホーク` reads
identically to an alternate spelling of フォーク ("fork", N5), but unidic's
own analysis of the actual headline says this `ホーク` is "hawk" (as in a
sports team name), which carries no JLPT tag at all.

Checked against ~1050 of the measurement's 1256 distinct non-proper-noun,
non-number misses (stopped there -- diminishing returns once a dataset
this size has already surfaced its common words); 36 passed the check and 26 are kept,
added at the level and reading Jisho cites, duplicate readings of a word
`vocab.csv` already lists kept as their own row exactly like the 97
two-level words there (e.g. `退く` is already listed at どく, N3; this adds
it again at しりぞく, N1 -- two different words, same spelling). 室内 and
楽しむ, the two words jp-reading-practice originally reported missing, are
*not* in this file: 楽しむ turned out to be an okurigana variant of the
already-listed `楽む` (fixed by the rule above, N4) and 室内, checked the
same way as everything else here, has no Jisho JLPT tag at all -- Jisho
itself does not consider it part of the Waller lists, so there is no
source to cite for it and it stays out, per "never guess a level."

**Affixes and compound parts stay out**, even with a Jisho tag: 的, 初, 原,
店, 次, 相, 過ぎ and 極 are prefixes or suffixes a tokenizer splits off
(経済的, 初日, 書店, 第二次), and 日曜/木曜 are the front half of
日曜日/木曜日 (N5) once split. Listing them would put the level of the affix
on every word it attaches to and push a text's measured mix toward N1 for
no reason.

## Updating

Bump the pinned commits in `backend/scripts/update_jlpt_data.py`, run
`uv run python scripts/update_jlpt_data.py` from `backend/`, and update the
commits and row counts above. `supplement.csv` is hand-maintained and
untouched by that script.

## Licenses

See [LICENSES.md](LICENSES.md).
