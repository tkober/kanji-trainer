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

When the expression is not listed *verbatim* at all, two further rules try
a notation difference before giving up (``_fallback_level``, found measuring
``get_jlpt_levels`` against real text from jp-reading-practice -- see that
function's and ``_adverb_variant_level``'s own doc comments for the detail
and the false-positive reasoning): an **okurigana variant** (the list's old
spelling 落る ~ a real tokeniser's 落ちる, same reading, same kanji in the
same order) and an **adverb's trailing と/に** that a tokeniser splits off
as its own particle/copula token (ゆっくりと ~ ゆっくり). Both still require
an exact reading match, so neither can turn an unrelated word into a false
hit -- only recognise a genuine variant of one already in the list.
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

#: Adverbs the list sometimes spells with a trailing と/に that a real
#: tokeniser's base form drops (unidic splits ゆっくりと into the adverb
#: ゆっくり plus the case/quotative particle と) -- see `_adverb_variant_level`.
_ADVERB_SUFFIXES = ("と", "に")

#: Of those, only に is safe to strip back off a query that already carries
#: it (the reverse direction in `_adverb_variant_level`): it is specifically
#: how a noun/na-adjective forms its adverb (本当 -> 本当に), so the stem
#: found underneath really is the same word at the same level. と has no
#: such narrow role -- it is also the plain conditional/quotative particle
#: ("読むと", "多いと"), and stripping it off *that* would credit an
#: ordinary sentence fragment with its verb's level for the wrong reason.
#: Appending と (the forward direction) stays safe for both suffixes: it
#: only ever matches an entry that is actually spelled that way in the list.
_ADVERB_STRIPPABLE_SUFFIXES = ("に",)

#: Rows in `kanji.csv` / `vocab.csv`, loaded once and cached at module level
#: (see `_ensure_loaded`). `None` means "not loaded yet", not "empty file".
_kanji_levels: dict[str, int] | None = None
_vocab_index: dict[str, list[tuple[int, frozenset[str]]]] | None = None
#: `(kanji skeleton, reading) -> levels`, built alongside `_vocab_index` --
#: the okurigana-variant fallback in `_fallback_level` (e.g. 落ちる ~ 落る,
#: both skeleton "落", reading おちる).
_vocab_skeleton_index: dict[tuple[str, str], list[int]] | None = None

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


def _kanji_skeleton(text: str) -> str:
    """Every kanji character in `text`, in order, kana/okurigana dropped --
    the key the okurigana-variant fallback matches on (`_fallback_level`):
    落ちる and the list's old spelling 落る both reduce to "落".

    Uses `_is_kanji` (defined below, resolved at call time -- `_ensure_loaded`
    only ever runs after the whole module has been imported)."""
    return "".join(ch for ch in text if _is_kanji(ch))


def _load_vocab_rows(
    path: Path,
    vocab_index: dict[str, list[tuple[int, frozenset[str]]]],
    skeleton_index: dict[tuple[str, str], list[int]],
) -> None:
    """Index one `jlpt,expression,reading` CSV into `vocab_index` (and the
    skeleton index the okurigana-variant rule reads) -- shared by `vocab.csv`
    and `supplement.csv` (same columns, same upstream notation quirks) so a
    row in either indexes exactly the same way."""
    with path.open(encoding="utf-8", newline="") as handle:
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
                    skeleton = _kanji_skeleton(key)
                    if skeleton:
                        for reading in readings:
                            skeleton_index.setdefault((skeleton, reading), []).append(level)


def _ensure_loaded() -> None:
    """Load both checked-in CSVs plus the supplement on first use; every
    later call reuses the cache.

    Lazy rather than at import time so a module import never touches the
    filesystem, and cached rather than re-read per call since nothing in the
    checked-in data changes while the process runs.
    """
    global _kanji_levels, _vocab_index, _vocab_skeleton_index
    if _kanji_levels is not None:
        return

    kanji_levels: dict[str, int] = {}
    with (_DATA_DIR / "kanji.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            kanji_levels[row["kanji"]] = int(row["jlpt"])

    vocab_index: dict[str, list[tuple[int, frozenset[str]]]] = {}
    skeleton_index: dict[tuple[str, str], list[int]] = {}
    _load_vocab_rows(_DATA_DIR / "vocab.csv", vocab_index, skeleton_index)
    # Words the measurement behind the okurigana/adverb rules above showed
    # are common and missing from the upstream snapshot entirely -- see
    # `supplement.csv`'s own header and the data README. Loaded the same way
    # and merged into the very same index, so an expression present in both
    # (never expected, but not fatal either) simply keeps both entries, same
    # as the 97 words `vocab.csv` itself already lists twice.
    _load_vocab_rows(_DATA_DIR / "supplement.csv", vocab_index, skeleton_index)

    _kanji_levels = kanji_levels
    _vocab_skeleton_index = skeleton_index
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

    wk_readings = {
        _strip_suru(_to_hiragana(reading["reading"]))
        for reading in readings
        if reading.get("reading")
    } or {_to_hiragana(key)}

    entries = _vocab_index.get(key)
    if not entries:
        # The expression itself is not listed at all -- try the notation
        # fallbacks (okurigana variant, trailing と/に) before giving up.
        # Not reached when `entries` exists but no reading matches: that is
        # a genuinely different word at this same spelling (one月 いちがつ
        # vs the list's 一月 ひとつき), and no fallback may paper over it.
        return _fallback_level(key, wk_readings)

    matches = [level for level, list_readings in entries if list_readings & wk_readings]
    # The easier (higher N, i.e. numerically larger) level wins when a word
    # is listed twice -- the README's own instruction for the 97 such words.
    return max(matches) if matches else None


def _adverb_variant_level(key: str, wk_readings: set[str]) -> int | None:
    """An adverb the list spells with a trailing と/に that `key` either
    lacks or carries -- see `_ADVERB_SUFFIXES`'s doc comment.

    Forward (the common real case: a tokeniser splits the と/に off as its
    own particle/copula token, e.g. ゆっくり + と): try `key` with the
    suffix added, matched against `wk_readings` with the same suffix added.
    Reverse (a caller that kept the suffix attached, e.g. a whole phrase
    passed in by hand): only for に (`_ADVERB_STRIPPABLE_SUFFIXES` -- see its
    own doc comment for why not と); if `key` already ends with に, also try
    it stripped, against readings stripped the same way. Either direction
    only ever matches an entry that is *actually* spelled that way in the
    list -- there is no entry to accidentally invent, so an unrelated word
    plus a case particle cannot collide with a real one.
    """
    assert _vocab_index is not None  # set by `_ensure_loaded`
    levels: list[int] = []
    for suffix in _ADVERB_SUFFIXES:
        candidates: list[tuple[str, set[str]]] = [
            (key + suffix, {reading + suffix for reading in wk_readings})
        ]
        if suffix in _ADVERB_STRIPPABLE_SUFFIXES and key.endswith(suffix):
            candidates.append(
                (
                    key[: -len(suffix)],
                    {
                        reading[: -len(suffix)]
                        for reading in wk_readings
                        if reading.endswith(suffix)
                    },
                )
            )
        for alt_key, alt_readings in candidates:
            if not alt_key or not alt_readings:
                continue
            entries = _vocab_index.get(alt_key)
            if entries:
                levels.extend(level for level, list_readings in entries if list_readings & alt_readings)
    return max(levels) if levels else None


def _fallback_level(key: str, wk_readings: set[str]) -> int | None:
    """Matching rules for a notation difference between a query and the
    list, tried only once the expression is not listed verbatim at all (see
    the two callers, `_vocab_level` and `word_level`) -- each rule requires
    an exact reading match, so neither can turn an unrelated word into a
    false hit, only a genuine variant of a listed one:

    * **Okurigana variant** (`_vocab_skeleton_index`): the same reading and
      the same kanji in the same order, different okurigana -- 落ちる
      (おちる) ~ the list's old-spelling 落る (おちる). Requires at least
      one kanji (`_kanji_skeleton` empty otherwise); two different
      kana-only words that happen to share a reading are homophones, not an
      okurigana variant, and must not be merged.
    * **Adverb と/に** (`_adverb_variant_level`): see its own doc comment.
    """
    assert _vocab_skeleton_index is not None  # set by `_ensure_loaded`
    skeleton = _kanji_skeleton(key)
    if skeleton:
        levels = [
            level
            for reading in wk_readings
            for level in (_vocab_skeleton_index.get((skeleton, reading)) or [])
        ]
        if levels:
            return max(levels)
    return _adverb_variant_level(key, wk_readings)


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

    Before that kanji fallback, and only when `reading` is given (the
    fallback rules both require an exact reading match -- see
    `_fallback_level`'s own doc comment), an expression the list does not
    have verbatim is tried against the okurigana-variant and adverb-と/に
    notation rules: 落ちる against the list's old spelling 落る, or
    ゆっくり (what a real tokeniser hands back once it has split ゆっくりと
    into the adverb and the と particle) against the list's ゆっくりと.
    """
    _ensure_loaded()
    assert _vocab_index is not None  # set by `_ensure_loaded`
    key = _strip_suru(_clean(text))
    entries = _vocab_index.get(key) if key else None

    if entries:
        if reading is None:
            return max(level for level, _ in entries)
        wk_reading = _normalise(reading)
        matches = [level for level, list_readings in entries if wk_reading in list_readings]
        return max(matches) if matches else None

    if key and reading is not None:
        level = _fallback_level(key, {_normalise(reading)})
        if level is not None:
            return level

    return kanji_level(text) if len(text) == 1 else None


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
