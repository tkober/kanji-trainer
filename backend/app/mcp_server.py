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
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from sqlalchemy import func, select

from .api.study import lowest_open_level
from .config import get_settings
from .db import Progress, Subject, get_sessionmaker
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


def _allowed_hosts() -> list[str]:
    raw = get_settings().mcp_allowed_hosts
    hosts = [host.strip() for host in raw.split(",") if host.strip()]
    return hosts or list(_DEFAULT_ALLOWED_HOSTS)


mcp = FastMCP(
    "kanji-trainer",
    instructions=(
        "Read-only access to this learner's kanji/vocabulary progress. Use "
        "get_learning_summary for an overview, get_known_kanji / "
        "get_known_vocabulary to list everything known at or above a given "
        "SRS stage, and lookup_items to check specific characters. No tool "
        "here writes anything."
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


class StateCounts(BaseModel):
    new: int = 0
    learning: int = 0
    known: int = 0
    suspended: int = 0


class LearningSummary(BaseModel):
    #: `lowest_open_level()` -- the same definition the header's `Counters`
    #: and `/stats` use.
    current_level: int | None
    #: Keyed by object_type ("radical", "kanji", "vocabulary",
    #: "kana_vocabulary"), each a count of every state.
    counts: dict[str, StateCounts]


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
    count. See `app.srs.is_known`.
    """
    return await _known_subjects(("kanji",), min_srs_stage)


@mcp.tool()
async def get_known_vocabulary(min_srs_stage: int = STAGE_GURU_1) -> list[KnownItem]:
    """Vocabulary the learner already knows, including kana_vocabulary
    (words already spelled in kana). Same known rule as `get_known_kanji`.
    """
    return await _known_subjects(("vocabulary", "kana_vocabulary"), min_srs_stage)


@mcp.tool()
async def get_learning_summary() -> LearningSummary:
    """The learner's current level and how many items of each type sit in
    each state.

    `current_level` is `lowest_open_level()` -- the same definition the
    header's `Counters` and `GET /api/stats` use, not the highest level
    touched.
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
    return LearningSummary(current_level=current_level, counts=counts)


@mcp.tool()
async def lookup_items(characters: list[str]) -> list[LookupResult]:
    """Look up specific kanji or words by their exact characters -- e.g. for
    a word popover while reading.

    One result per requested string, in the same order, each saying whether
    it was found and, if so, every subject it matches (a kanji and a
    vocabulary word can share the same characters) with that subject's
    level, state, SRS stage, the `is_known` flag (at the default threshold --
    this tool takes no `min_srs_stage` of its own) and its primary meaning
    and reading.
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
                )
                for subject, progress in by_characters.get(query, [])
            ],
        )
        for query in characters
    ]
