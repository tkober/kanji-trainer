"""Read-only MCP server (issue #75), served at ``/mcp`` by :mod:`app.main`.

``jp-reading-practice`` (and other agents -- Claude, openclaw) need to know
which kanji and vocabulary this learner already has, to tailor a text to the
current level instead of asking them to read material WaniKani never taught.
The server lives in this repo rather than a separate one so a schema change
and the MCP tool reading it always land in the same PR: no second database
connection, no second container.

Every tool opens its own session through :func:`app.db.get_sessionmaker` --
the same engine and app-role credentials every HTTP router uses -- and never
writes. The "known" rule is :func:`app.srs.is_known`, the one place it is
decided, so every tool here agrees with it rather than re-deriving it.

Payloads are deliberately lean: meanings and readings (primary first), never
mnemonics or context sentences -- those exist to teach the *first* encounter,
and this endpoint exists for everything after that.

``app/jlpt.py`` (issue #62) is the trainer's own JLPT data -- WaniKani has
none -- so this is also the one place that owns it for other agents: every
`KnownItem`/`LookupMatch` carries its subject's `jlpt` level (5 = N5 .. 1 =
N1, `None` if unlisted or a radical), `get_learning_summary` adds a per-level
breakdown, and `get_jlpt_levels` looks up arbitrary words and kanji against
the checked-in snapshot, independent of whether WaniKani teaches them --
`jp-reading-practice` needs the level of every word in a generated text,
including ones this learner has never been taught.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .api.study import lowest_open_level
from .config import get_settings
from .db import Progress, Subject, get_sessionmaker
from .jlpt import fold_object_type, kanji_characters, kanji_level, word_level
from .srs import STAGE_GURU_1, ItemState, is_known

#: Every form of "this machine" DNS-rebinding protection should accept when
#: MCP_ALLOWED_HOSTS is unset -- see Settings.mcp_allowed_hosts.
_DEFAULT_ALLOWED_HOSTS: tuple[str, ...] = (
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
    "[::1]",
    "[::1]:*",
)

_OBJECT_TYPES: tuple[str, ...] = ("radical", "kanji", "vocabulary", "kana_vocabulary")
_STATES: tuple[ItemState, ...] = (
    ItemState.NEW,
    ItemState.LEARNING,
    ItemState.KNOWN,
    ItemState.SUSPENDED,
)

#: JLPT levels, easiest to hardest -- the fixed row order of the summary's
#: `jlpt` section and `LearningSummary.jlpt`'s keys.
_JLPT_LEVELS: tuple[int, ...] = (5, 4, 3, 2, 1)

#: `get_jlpt_levels`' request cap -- generous for a long text's distinct
#: tokens, small enough that a mistaken request (e.g. the whole dictionary)
#: fails fast with a clear error instead of loading the CSVs for nothing.
_MAX_JLPT_QUERY_ITEMS = 2000


def _allowed_hosts() -> list[str]:
    raw = get_settings().mcp_allowed_hosts
    hosts = [host.strip() for host in raw.split(",") if host.strip()]
    return hosts or list(_DEFAULT_ALLOWED_HOSTS)


mcp = FastMCP(
    "kanji-trainer",
    instructions=(
        "Read-only access to this learner's kanji/vocabulary progress and to "
        "the trainer's own JLPT level data. Use get_learning_summary for an "
        "overview (including a per-JLPT-level breakdown), get_known_kanji / "
        "get_known_vocabulary to list everything known at or above a given "
        "SRS stage, lookup_items to check specific characters against this "
        "learner's progress, and get_jlpt_levels to look up the JLPT level "
        "(5 = N5 .. 1 = N1) of arbitrary words and kanji -- including ones "
        "WaniKani never taught -- against the checked-in JLPT snapshot. No "
        "tool here writes anything."
    ),
    stateless_http=True,
    json_response=True,
    # Default is "/mcp" already, but named explicitly: app/main.py adds
    # this route to the FastAPI router as it is, so this path is the whole
    # story -- see the comment there for why it is not mounted.
    streamable_http_path="/mcp",
    transport_security=TransportSecuritySettings(allowed_hosts=_allowed_hosts()),
)

#: Built once at import time (this also lazily creates `mcp.session_manager`,
#: which app/main.py's lifespan needs to be able to reach). Only its route is
#: used, so its own lifespan -- which would start that session manager --
#: never runs; app/main.py drives it from the FastAPI app's lifespan instead.
mcp_app = mcp.streamable_http_app()


# --- shared shapes -----------------------------------------------------------


class MeaningOut(BaseModel):
    meaning: str
    primary: bool = False


class ReadingOut(BaseModel):
    reading: str
    primary: bool = False
    type: str | None = None


class KnownItem(BaseModel):
    """One subject the learner already knows, by the `is_known` rule."""

    characters: str | None
    slug: str
    object_type: str
    level: int
    state: str
    srs_stage: int
    #: Primary first; never the blacklist entries `answers.py` rejects.
    meanings: list[MeaningOut]
    readings: list[ReadingOut]
    #: This subject's JLPT level, 5 = N5 .. 1 = N1 (easiest to hardest), or
    #: `None` for a radical (never examined) or a kanji/word the trainer's
    #: checked-in JLPT snapshot (`app/jlpt.py`, issue #62) does not list.
    jlpt: int | None


class StateCounts(BaseModel):
    new: int = 0
    learning: int = 0
    known: int = 0
    suspended: int = 0


class JlptLevelCounts(BaseModel):
    #: Every subject at this level, this object type -- suspended items
    #: included, exactly like `total` everywhere else on this server; only
    #: `known` ever excludes them.
    total: int = 0
    #: How many of `total` pass `app.srs.is_known` at the default threshold
    #: (Guru I). A suspended item never counts here, no matter its stage.
    known: int = 0


class JlptLevelSummary(BaseModel):
    kanji: JlptLevelCounts = Field(default_factory=JlptLevelCounts)
    #: `kana_vocabulary` folded in, same grouping `GET /api/stats/jlpt` uses
    #: (`app.jlpt.fold_object_type`).
    vocabulary: JlptLevelCounts = Field(default_factory=JlptLevelCounts)


class LearningSummary(BaseModel):
    #: `lowest_open_level()` -- the same definition the header's `Counters`
    #: and `/stats` use.
    current_level: int | None
    #: Keyed by object_type ("radical", "kanji", "vocabulary",
    #: "kana_vocabulary"), each a count of every state.
    counts: dict[str, StateCounts]
    #: Keyed by JLPT level, 5 = N5 .. 1 = N1, every level always present even
    #: when empty -- see `JlptLevelCounts` for what `total`/`known` count.
    jlpt: dict[int, JlptLevelSummary]


class LookupMatch(BaseModel):
    object_type: str
    level: int
    state: str
    srs_stage: int
    #: By `is_known` at the default threshold (Guru I) -- `lookup_items`
    #: takes no `min_srs_stage` of its own, see the issue.
    known: bool
    primary_meaning: str | None
    primary_reading: str | None
    #: Same JLPT level as `KnownItem.jlpt` -- 5 = N5 .. 1 = N1, `None` for a
    #: radical or an unlisted kanji/word.
    jlpt: int | None


class JlptQuery(BaseModel):
    """One `get_jlpt_levels` request item -- an arbitrary word or kanji,
    independent of whether it is one of this learner's own subjects."""

    text: str
    #: Narrows a vocabulary match to this reading (katakana folded to
    #: hiragana, a trailing する stripped on both sides, same rule
    #: `app.jlpt._vocab_level` applies to a WaniKani subject's own readings).
    #: Without one, `jlpt` is the easiest level among every entry for `text`.
    reading: str | None = None


class KanjiCharacterLevel(BaseModel):
    character: str
    #: From the kanji list alone, 5 = N5 .. 1 = N1, `None` if unlisted.
    jlpt: int | None


class JlptLevelResult(BaseModel):
    #: Both echoed from the request, so a caller can zip results back up
    #: against its own list even though order already matches 1:1.
    text: str
    reading: str | None
    #: `text`'s own JLPT level -- see `JlptQuery.reading` for how it is
    #: matched, and `app.jlpt.word_level` for the single-kanji fallback to
    #: the kanji list. `None` when `text` is not listed anywhere.
    jlpt: int | None
    #: Every kanji character in `text`, in order, duplicates removed, each
    #: with its own kanji-list level (`None` if unlisted) -- independent of
    #: `jlpt` above, which is `text`'s level as a whole word.
    kanji: list[KanjiCharacterLevel]


class LookupResult(BaseModel):
    #: Echoes the requested string, so a caller can zip results back up
    #: against its own list even if some are missing.
    characters: str
    found: bool
    #: More than one subject can share the same characters (a kanji and a
    #: vocabulary word spelled the same way), so this is a list, not one.
    matches: list[LookupMatch]


# --- meanings / readings -----------------------------------------------------


def _accepted(entries: Iterable[dict]) -> list[dict]:
    """Drop the blacklist entries `answers.py` rejects -- never worth handing
    to another agent as something the learner could answer with."""
    return [entry for entry in entries if entry.get("accepted_answer") is not False]


def _primary_first(entries: Iterable[dict]) -> list[dict]:
    return sorted(_accepted(entries), key=lambda entry: not entry.get("primary", False))


def _primary(entries: Iterable[dict], field: str) -> str | None:
    accepted = _primary_first(entries)
    return accepted[0][field] if accepted else None


def _known_item(subject: Subject, progress: Progress) -> KnownItem:
    return KnownItem(
        characters=subject.characters,
        slug=subject.slug,
        object_type=subject.object_type,
        level=subject.level,
        state=progress.state,
        srs_stage=progress.srs_stage,
        jlpt=subject.jlpt,
        meanings=[
            MeaningOut(meaning=m["meaning"], primary=bool(m.get("primary")))
            for m in _primary_first(subject.meanings)
        ],
        readings=[
            ReadingOut(reading=r["reading"], primary=bool(r.get("primary")), type=r.get("type"))
            for r in _primary_first(subject.readings)
        ],
    )


async def _known_subjects(object_types: tuple[str, ...], min_srs_stage: int) -> list[KnownItem]:
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(Subject, Progress)
                .join(Progress, Progress.subject_id == Subject.id)
                .where(Subject.object_type.in_(object_types))
            )
        ).all()
    return [
        _known_item(subject, progress) for subject, progress in rows if is_known(progress, min_srs_stage)
    ]


# --- tools --------------------------------------------------------------------


@mcp.tool()
async def get_known_kanji(min_srs_stage: int = STAGE_GURU_1) -> list[KnownItem]:
    """Kanji the learner already knows.

    "Known" is `state == known`, or `state == learning` at or above
    `min_srs_stage` (default 5, Guru I) -- suspended and new items never
    count. See `app.srs.is_known`. Each item's `jlpt` is its JLPT level,
    5 = N5 (easiest) down to 1 = N1 (hardest), or `None` if the trainer's
    checked-in JLPT snapshot does not list it.
    """
    return await _known_subjects(("kanji",), min_srs_stage)


@mcp.tool()
async def get_known_vocabulary(min_srs_stage: int = STAGE_GURU_1) -> list[KnownItem]:
    """Vocabulary the learner already knows, including kana_vocabulary
    (words already spelled in kana). Same known rule as `get_known_kanji`,
    and the same `jlpt` meaning (5 = N5 .. 1 = N1, `None` if unlisted).
    """
    return await _known_subjects(("vocabulary", "kana_vocabulary"), min_srs_stage)


async def _jlpt_summary(session) -> dict[int, JlptLevelSummary]:
    """`LearningSummary.jlpt` -- every level present even when empty, so a
    caller never has to treat a missing key as zero itself."""
    summary = {level: JlptLevelSummary() for level in _JLPT_LEVELS}
    rows = await session.execute(
        select(Subject.jlpt, Subject.object_type, Progress.state, Progress.srs_stage, func.count())
        .select_from(Progress)
        .join(Subject, Subject.id == Progress.subject_id)
        .where(Subject.jlpt.is_not(None))
        .group_by(Subject.jlpt, Subject.object_type, Progress.state, Progress.srs_stage)
    )
    for level, object_type, state, stage, n in rows:
        bucket: JlptLevelCounts = getattr(summary[level], fold_object_type(object_type))
        bucket.total += n
        # A plain stand-in with just the two fields `is_known` reads -- these
        # rows are already grouped by (state, srs_stage), not individual
        # Progress objects.
        if is_known(SimpleNamespace(state=state, srs_stage=stage)):
            bucket.known += n
    return summary


@mcp.tool()
async def get_learning_summary() -> LearningSummary:
    """The learner's current level, how many items of each type sit in each
    state, and a JLPT breakdown.

    `current_level` is `lowest_open_level()` -- the same definition the
    header's `Counters` and `GET /api/stats` use, not the highest level
    touched.

    `jlpt` has one entry per level, 5 = N5 .. 1 = N1, each with a `kanji` and
    a `vocabulary` count (`kana_vocabulary` folded into `vocabulary`, the
    same grouping `GET /api/stats/jlpt` uses). `total` is every subject at
    that level and type, suspended ones included; `known` is how many of
    those pass `app.srs.is_known` at the default threshold (Guru I) --
    suspended items are in `total` but never in `known`, no matter their
    SRS stage.
    """
    async with get_sessionmaker()() as session:
        current_level = await lowest_open_level(session)
        rows = await session.execute(
            select(Subject.object_type, Progress.state, func.count())
            .select_from(Progress)
            .join(Subject, Subject.id == Progress.subject_id)
            .group_by(Subject.object_type, Progress.state)
        )
        counts = {object_type: StateCounts() for object_type in _OBJECT_TYPES}
        for object_type, state, n in rows:
            bucket = counts.setdefault(object_type, StateCounts())
            if state in _STATES:
                setattr(bucket, state, getattr(bucket, state) + n)
        jlpt = await _jlpt_summary(session)
    return LearningSummary(current_level=current_level, counts=counts, jlpt=jlpt)


@mcp.tool()
async def lookup_items(characters: list[str]) -> list[LookupResult]:
    """Look up specific kanji or words by their exact characters -- e.g. for
    a word popover while reading.

    One result per requested string, in the same order, each saying whether
    it was found and, if so, every subject it matches (a kanji and a
    vocabulary word can share the same characters) with that subject's
    level, state, SRS stage, the `is_known` flag (at the default threshold --
    this tool takes no `min_srs_stage` of its own), its primary meaning and
    reading, and its JLPT level (5 = N5 .. 1 = N1, `None` if unlisted).

    This only ever matches the learner's own WaniKani subjects -- for an
    arbitrary word or kanji WaniKani never taught, use `get_jlpt_levels`.
    """
    if not characters:
        return []

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(Subject, Progress)
                .join(Progress, Progress.subject_id == Subject.id)
                .where(Subject.characters.in_(characters))
            )
        ).all()

    by_characters: dict[str, list[tuple[Subject, Progress]]] = defaultdict(list)
    for subject, progress in rows:
        by_characters[subject.characters].append((subject, progress))

    return [
        LookupResult(
            characters=query,
            found=bool(by_characters.get(query)),
            matches=[
                LookupMatch(
                    object_type=subject.object_type,
                    level=subject.level,
                    state=progress.state,
                    srs_stage=progress.srs_stage,
                    known=is_known(progress, STAGE_GURU_1),
                    primary_meaning=_primary(subject.meanings, "meaning"),
                    primary_reading=_primary(subject.readings, "reading"),
                    jlpt=subject.jlpt,
                )
                for subject, progress in by_characters.get(query, [])
            ],
        )
        for query in characters
    ]


@mcp.tool()
async def get_jlpt_levels(items: list[JlptQuery]) -> list[JlptLevelResult]:
    """JLPT levels for arbitrary words and kanji, independent of whether
    WaniKani teaches them -- looked up against the trainer's checked-in JLPT
    snapshot (`app/jlpt.py`, issue #62), not this learner's own progress.

    For `jp-reading-practice` (and anyone else) needing the level of every
    word and kanji in a generated text, including the ones WaniKani never
    taught this learner.

    Per item, `jlpt` is 5 = N5 (easiest) .. 1 = N1 (hardest), or `None` if
    the word is not listed. With `reading` set, it must match that reading
    (katakana folded to hiragana, a trailing する stripped either side) as
    well as the expression; without one, it is the easiest (numerically
    largest) level among every entry for that expression. A single
    character not itself a listed vocabulary expression falls back to the
    kanji list. `kanji` lists every kanji character in `text`, in order,
    duplicates removed, each with its own kanji-list level (`None` if
    unlisted) -- independent of `jlpt`, `text`'s level as a whole word.

    Capped at `_MAX_JLPT_QUERY_ITEMS` items per call (a long text's distinct
    tokens comfortably fit); a larger request raises rather than silently
    truncating.
    """
    if len(items) > _MAX_JLPT_QUERY_ITEMS:
        raise ValueError(
            f"get_jlpt_levels takes at most {_MAX_JLPT_QUERY_ITEMS} items "
            f"per call, got {len(items)}."
        )
    return [
        JlptLevelResult(
            text=item.text,
            reading=item.reading,
            jlpt=word_level(item.text, item.reading),
            kanji=[
                KanjiCharacterLevel(character=ch, jlpt=kanji_level(ch))
                for ch in kanji_characters(item.text)
            ],
        )
        for item in items
    ]
