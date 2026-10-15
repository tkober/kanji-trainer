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

`jlpt` is the level as a number, 5 for N5 down to 1 for N1.

## Notes on the vocabulary

`expression` and `reading` are copied verbatim, so the upstream notation is
still there and has to be normalised before matching:

- Variants separated by `; ` (`足; 脚`, `まいげつ; まいつき`).
- Suru verbs with ` (する)` in the reading (`べんきょう (する)`).
- Affixes and context in `～`/`〜` and parentheses (`～ごろ`, `(花を〜) 生ける, 活ける`).

97 words are listed at two levels and keep both rows. Use the easier one.

## Updating

Bump the pinned commits in `backend/scripts/update_jlpt_data.py`, run
`uv run python scripts/update_jlpt_data.py` from `backend/`, and update the
commits and row counts above.

## Licenses

See [LICENSES.md](LICENSES.md).
