"""JLPT level estimates for kanji and vocabulary (issue #62).

WaniKani has no JLPT data at all, so the trainer brings its own: a checked-in
snapshot of Jonathan Waller's lists (tanos.co.uk, the source most tools --
Jisho included -- use since there is no official list after the 2010 exam
revision), in ``app/data/jlpt/`` -- see that directory's own README for the
exact sources, licenses and the upstream notation quirks the normalisation
below exists to undo.

Two lookups, both approximate on purpose:

* **Kanji** match on ``characters`` alone.
* **Vocabulary** (and ``kana_vocabulary``, which carries no kanji but is
  taught the same way) must match on the expression *and* at least one
  reading -- WaniKani and the JLPT list can use the same written form for two
  different words (e.g. 一月 read いちがつ "January" here vs. the list's
  ひとつき "one month"), and an expression-only match would silently conflate
  them.

Radicals never get a level -- the JLPT does not examine component shapes --
and ``jlpt_level`` returns ``None`` for them without even touching the data.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db import Subject

#: Affix markers WaniKani and the list both use for "goes before/after a
#: stem" entries (e.g. list's `～ごろ`) -- stripped from both sides so they
#: never become part of what is compared.
_TILDE = "～〜~"

#: Variants within one CSV cell are separated by one of these -- mostly `; `
#: (`足; 脚`), but a comma shows up too (`(花を〜) 生ける, 活ける`).
_VARIANT_SPLIT = re.compile(r"[;,、]")

#: Parenthesised affixes/context, either bracket style (`(花を〜) 生ける`,
#: `べんきょう (する)`). Removed before anything else runs.
_PARENS = re.compile(r"\([^)]*\)|（[^）]*）")

#: Katakana ァ..ヶ shifted down by this many code points lands on the matching
#: hiragana character -- the two blocks are laid out in parallel in Unicode.
_KATAKANA_TO_HIRAGANA_OFFSET = 0x60

#: Rows in `kanji.csv` / `vocab.csv`, loaded once and cached at module level
#: (see `_ensure_loaded`). `None` means "not loaded yet", not "empty file".
_kanji_levels: dict[str, int] | None = None
_vocab_index: dict[str, list[tuple[int, frozenset[str]]]] | None = None

_DATA_DIR = Path(__file__).resolve().parent / "data" / "jlpt"

#: Rows per UPDATE statement in `refresh_jlpt`, same chunking reasoning as
#: `importer._CHUNK`: comfortably under any driver's parameter limit.
_CHUNK = 400


def _clean(text: str) -> str:
    """Strip parenthesised affixes, internal spaces and leading/trailing
    `～`/`〜`/`~` -- the three kinds of upstream decoration the README names."""
    return _PARENS.sub("", text).replace(" ", "").strip().strip(_TILDE)


def _to_hiragana(text: str) -> str:
    """Katakana folded to hiragana, everything else left alone -- the
    matching rule treats the two scripts as the same reading."""
    return "".join(
        chr(ord(ch) - _KATAKANA_TO_HIRAGANA_OFFSET) if "ァ" <= ch <= "ヶ" else ch for ch in text
    )


def _strip_suru(text: str) -> str:
    """Drop a trailing する -- `べんきょうする` and `べんきょう` must match the
    same list entry, whichever side spells out the verb and which does not."""
    return text[:-2] if text.endswith("する") and len(text) > 2 else text


def _normalise(text: str) -> str:
    """The one-shot pipeline a *list* cell goes through before indexing."""
    return _strip_suru(_to_hiragana(_clean(text)))


def _ensure_loaded() -> None:
    """Load both CSVs on first use; every later call reuses the cache.

    Lazy rather than at import time so a module import never touches the
    filesystem, and cached rather than re-read per call since nothing in the
    checked-in data changes while the process runs.
    """
    global _kanji_levels, _vocab_index
    if _kanji_levels is not None:
        return

    kanji_levels: dict[str, int] = {}
    with (_DATA_DIR / "kanji.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            kanji_levels[row["kanji"]] = int(row["jlpt"])

    vocab_index: dict[str, list[tuple[int, frozenset[str]]]] = {}
    with (_DATA_DIR / "vocab.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            level = int(row["jlpt"])
            readings = frozenset(
                _normalise(variant)
                for variant in _VARIANT_SPLIT.split(row["reading"])
                if _clean(variant)
            )
            for variant in _VARIANT_SPLIT.split(row["expression"]):
                key = _strip_suru(_clean(variant))
                if key:
                    vocab_index.setdefault(key, []).append((level, readings))

    _kanji_levels = kanji_levels
    _vocab_index = vocab_index


def _vocab_level(characters: str, readings: list[dict[str, Any]]) -> int | None:
    """Expression match, then reading match -- see the module docstring.

    `readings` is `Subject.readings` as the importer stores it: a list of
    WaniKani reading objects, empty for `kana_vocabulary`, which carries no
    separate reading because its `characters` already *is* the reading --
    hence the fallback to the (hiragana-folded) key itself.
    """
    assert _vocab_index is not None  # set by `_ensure_loaded`
    key = _strip_suru(characters.strip(_TILDE))
    if not key:
        return None
    entries = _vocab_index.get(key)
    if not entries:
        return None

    wk_readings = {
        _strip_suru(_to_hiragana(reading["reading"]))
        for reading in readings
        if reading.get("reading")
    } or {_to_hiragana(key)}

    matches = [level for level, list_readings in entries if list_readings & wk_readings]
    # The easier (higher N, i.e. numerically larger) level wins when a word
    # is listed twice -- the README's own instruction for the 97 such words.
    return max(matches) if matches else None


#: CJK Unified Ideographs plus the two blocks that matter here: Extension A
#: (rarer kanji still in common dictionaries) and the Compatibility block
#: (old/variant forms). Enough to tell "this character is a kanji" from kana,
#: romaji and punctuation without a dependency -- not a claim that every code
#: point in range is a real, teachable kanji (`kanji_level` still answers
#: `None` for anything `kanji.csv` does not list).
_KANJI_RANGES: tuple[tuple[int, int], ...] = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF))


def _is_kanji(ch: str) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in _KANJI_RANGES)


def kanji_characters(text: str) -> list[str]:
    """Every kanji character in `text`, in order, duplicates removed --
    what `get_jlpt_levels` (issue #75) lists per item, independent of
    whether each one is actually in `kanji.csv`."""
    seen: dict[str, None] = {}
    for ch in text:
        if _is_kanji(ch):
            seen.setdefault(ch, None)
    return list(seen)


def kanji_level(character: str) -> int | None:
    """The JLPT level of one kanji character, straight from the kanji list --
    independent of whether WaniKani teaches it. `None` if the list does not
    have it (including when `character` is not actually a kanji)."""
    _ensure_loaded()
    assert _kanji_levels is not None  # set by `_ensure_loaded`
    return _kanji_levels.get(character)


def word_level(text: str, reading: str | None = None) -> int | None:
    """The JLPT level of an arbitrary word, looked up against the checked-in
    snapshot independent of whether WaniKani teaches it (issue #75's
    `get_jlpt_levels`).

    With `reading` given: match the expression *and* that reading, the same
    rule `_vocab_level` applies to a WaniKani subject's own readings
    (katakana folded to hiragana, a trailing する stripped on both sides).
    Without one: the easiest (numerically largest) level among *every* entry
    for that expression -- the same "easier level wins" rule for the 97 words
    the list carries at two levels (see the module docstring), just applied
    without a reading to narrow the candidates first.

    A single character that is not itself a vocabulary expression falls back
    to the kanji list (`kanji_level`) -- most single kanji are also words,
    but a handful (e.g. purely grammatical ones) are kanji-list-only. `None`
    when the word is not listed anywhere.
    """
    _ensure_loaded()
    assert _vocab_index is not None  # set by `_ensure_loaded`
    key = _strip_suru(_clean(text))
    entries = _vocab_index.get(key) if key else None
    if not entries:
        return kanji_level(text) if len(text) == 1 else None

    if reading is None:
        return max(level for level, _ in entries)

    wk_reading = _normalise(reading)
    matches = [level for level, list_readings in entries if wk_reading in list_readings]
    return max(matches) if matches else None


#: `kana_vocabulary` carries no separate JLPT estimate of its own (see
#: `jlpt_level`) -- it is taught the same way as `vocabulary` and always
#: folds into it for any level-based grouping. The one place that grouping
#: is defined, so `GET /api/stats/jlpt` and the MCP summary (issue #75) agree
#: on it rather than each re-deriving it.
VOCABULARY_OBJECT_TYPES: tuple[str, ...] = ("vocabulary", "kana_vocabulary")


def fold_object_type(object_type: str) -> str:
    """`kana_vocabulary` folds into `vocabulary`; every other object type
    (`kanji`, `radical`) is returned unchanged."""
    return "vocabulary" if object_type in VOCABULARY_OBJECT_TYPES else object_type


def jlpt_level(
    object_type: str, characters: str | None, readings: list[dict[str, Any]] | None
) -> int | None:
    """The JLPT level (5 = N5 .. 1 = N1) for one subject, or `None`.

    `None` covers three different things on purpose: a radical (never
    examined), a kanji or word the list simply does not have, and the
    handful of radicals with no `characters` at all.
    """
    _ensure_loaded()
    if not characters:
        return None
    if object_type == "kanji":
        return _kanji_levels.get(characters)  # type: ignore[union-attr]
    if object_type in VOCABULARY_OBJECT_TYPES:
        return _vocab_level(characters, readings or [])
    return None


async def refresh_jlpt(session: AsyncSession) -> int:
    """Recompute `Subject.jlpt` for every subject, writing only what changed.

    Not part of the Alembic migration that adds the column: the data this
    reads is a checked-in snapshot the app can refresh independently of the
    schema (a newer `vocab.csv`/`kanji.csv`, or a matching-rule fix), and that
    must take effect for every existing row without a new revision -- see the
    callers, `db.init_db` and `importer._import_everything`. Does not commit;
    callers decide when (the owner-session startup path and the importer both
    already have their own commit right after).
    """
    rows = await session.execute(
        select(Subject.id, Subject.object_type, Subject.characters, Subject.readings, Subject.jlpt)
    )

    # Grouped by the *new* value rather than updated one row at a time --
    # most subjects land on the same handful of levels (or None), so this
    # turns what would be thousands of single-row UPDATEs into a few dozen.
    by_new_value: dict[int | None, list[int]] = {}
    changed = 0
    for subject_id, object_type, characters, readings, current in rows:
        level = jlpt_level(object_type, characters, readings)
        if level != current:
            by_new_value.setdefault(level, []).append(subject_id)
            changed += 1

    for level, ids in by_new_value.items():
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            await session.execute(update(Subject).where(Subject.id.in_(chunk)).values(jlpt=level))

    return changed
