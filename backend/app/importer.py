"""Seeding the trainer from a WaniKani account.

This is the answer to the problem the project was started for. Restarting
WaniKani after a long break means re-earning thousands of items at four hours
a step, because WaniKani has no way to say "ich kann das schon". It does not
need one: the account already records what the learner knows, in
``assignments.srs_stage``. The import reads it and starts the trainer from
there.

Everything at or above the threshold (Guru I by default) comes in as known and
never enters the review queue. Everything below keeps its stage *and its
WaniKani due date*, so a half-learned item is neither lost nor reset. Locked
items become lessons.

The threshold is a setting rather than a constant because the right value is
not knowable in advance -- it is the answer to "how much do I trust my Guru
items after eight months away", and the honest way to find it is to import,
look at a few reviews, and import again with ``remap_existing``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db import ImportRun, Progress, Subject, _upsert
from .runtime_config import RuntimeConfig
from .srs import STAGE_BURNED, ItemState, schedule
from .wanikani import WaniKaniClient, WaniKaniError, subject_row

log = logging.getLogger(__name__)

# Rows per INSERT. Large enough that 9.000 subjects is a couple of dozen
# statements, small enough to stay well under asyncpg's parameter limit.
_CHUNK = 400


@dataclass(frozen=True)
class Mapping:
    """Where one WaniKani assignment lands in the trainer."""

    state: ItemState
    srs_stage: int
    scheduled: bool


def classify(wk_srs_stage: int, threshold: int, known_stage: int) -> Mapping:
    """Translate a WaniKani SRS stage into a local state.

    Three outcomes, and the middle one is the one that is easy to get wrong:
    an item between "started" and the threshold keeps the stage it had.
    Flattening those to Apprentice I would throw away the very progress the
    import exists to preserve.
    """
    if wk_srs_stage >= threshold:
        return Mapping(ItemState.KNOWN, max(1, min(STAGE_BURNED, known_stage)), scheduled=False)
    if wk_srs_stage >= 1:
        return Mapping(ItemState.LEARNING, wk_srs_stage, scheduled=True)
    return Mapping(ItemState.NEW, 0, scheduled=False)


def parse_timestamp(value: Any) -> datetime | None:
    """WaniKani's ISO-8601 with a Z, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- orchestration ---------------------------------------------------------

_task: asyncio.Task[None] | None = None


def import_running() -> bool:
    """Whether an import is in flight in this process.

    In-process rather than a database flag on purpose: a single backend
    container serves this app, and a lock in the database would survive a
    crash and need clearing by hand.
    """
    return _task is not None and not _task.done()


async def start_import(
    sessionmaker: async_sessionmaker[AsyncSession],
    config: RuntimeConfig,
    *,
    known_srs_stage: int,
    remap_existing: bool,
) -> int:
    """Create the run row and kick the import off in the background.

    Returns the run id immediately -- the UI polls it. An import takes minutes
    and a request that waited for it would time out in nginx long before it
    finished.
    """
    global _task

    if import_running():
        raise RuntimeError("An import is already running.")

    async with sessionmaker() as session:
        run = ImportRun(status="running", known_srs_stage=known_srs_stage)
        session.add(run)
        await session.commit()
        run_id = run.id

    _task = asyncio.create_task(
        _run(sessionmaker, config, run_id, known_srs_stage, remap_existing)
    )
    return run_id


async def _run(
    sessionmaker: async_sessionmaker[AsyncSession],
    config: RuntimeConfig,
    run_id: int,
    known_srs_stage: int,
    remap_existing: bool,
) -> None:
    """The background body. Never raises -- it records the failure instead."""
    try:
        async with sessionmaker() as session:
            await _import_everything(
                session, config, run_id, known_srs_stage, remap_existing
            )
    except WaniKaniError as exc:
        log.warning("WaniKani import failed: %s", exc)
        # `message` is shown on the import screen unchanged, so it is UI copy
        # and therefore German -- unlike the exception itself, which stays
        # English like every other error raised in this backend.
        await _fail(sessionmaker, run_id, f"Import abgebrochen: {exc}")
    except Exception as exc:  # noqa: BLE001 - the run row is the error channel
        log.exception("WaniKani import failed unexpectedly")
        await _fail(sessionmaker, run_id, f"Unerwarteter Fehler beim Import: {exc}")


async def _fail(
    sessionmaker: async_sessionmaker[AsyncSession], run_id: int, message: str
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            update(ImportRun)
            .where(ImportRun.id == run_id)
            .values(
                status="failed",
                message=message[:2000],
                finished_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


# --- the import itself -----------------------------------------------------


async def _import_everything(
    session: AsyncSession,
    config: RuntimeConfig,
    run_id: int,
    known_srs_stage: int,
    remap_existing: bool,
) -> None:
    client = WaniKaniClient(
        token=config.wanikani_api_token,
        api_base=config.wanikani_api_base,
        revision=config.wanikani_revision,
    )

    user = await client.fetch_user()
    warning = _subscription_warning(user)

    imported, wk_ids = await _import_subjects(session, client, run_id)
    await _resolve_components(session, wk_ids)
    await _ensure_progress_rows(session)
    counts = await _apply_assignments(
        session,
        client,
        run_id,
        known_srs_stage=known_srs_stage,
        known_stage=config.known_srs_stage,
        intervals=config.srs_intervals,
        remap_existing=remap_existing,
        wk_ids=wk_ids,
    )

    await session.execute(
        update(ImportRun)
        .where(ImportRun.id == run_id)
        .values(
            status="succeeded",
            subjects_imported=imported,
            marked_known=counts["known"],
            marked_learning=counts["learning"],
            marked_new=counts["new"],
            message=warning,
            finished_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    log.info(
        "WaniKani import finished: %d subjects, %d known, %d learning, %d new",
        imported,
        counts["known"],
        counts["learning"],
        counts["new"],
    )


def _subscription_warning(user: dict[str, Any]) -> str:
    """Say so when the account can only see the free levels.

    A lapsed subscription does not fail the import -- it quietly returns three
    levels' worth of subjects, which looks like a bug much later. Better to
    name it on the screen that reports the result.
    """
    subscription = user.get("subscription") or {}
    granted = subscription.get("max_level_granted")
    if isinstance(granted, int) and granted < 60:
        return (
            f"Das WaniKani-Abo gibt nur Level 1–{granted} frei; höhere Level fehlen "
            "im Import. Mit aktivem Abo noch einmal importieren, um alles zu holen."
        )
    return ""


async def _import_subjects(
    session: AsyncSession, client: WaniKaniClient, run_id: int
) -> tuple[int, dict[int, int]]:
    """Upsert every subject, then return the WaniKani-id -> local-id map."""
    buffer: list[dict[str, Any]] = []
    imported = 0
    total_seen = 0

    async for page, total in client.iter_subjects():
        if total_seen == 0 and total:
            await session.execute(
                update(ImportRun).where(ImportRun.id == run_id).values(subjects_total=total)
            )
            await session.commit()
        total_seen = total

        buffer.extend(subject_row(item) for item in page)
        while len(buffer) >= _CHUNK:
            imported += await _flush_subjects(session, buffer[:_CHUNK])
            del buffer[:_CHUNK]
            await session.execute(
                update(ImportRun)
                .where(ImportRun.id == run_id)
                .values(subjects_imported=imported)
            )
            await session.commit()

    if buffer:
        imported += await _flush_subjects(session, buffer)
        await session.commit()

    rows = await session.execute(
        select(Subject.wanikani_id, Subject.id).where(Subject.wanikani_id.is_not(None))
    )
    return imported, {wk_id: local_id for wk_id, local_id in rows}


async def _flush_subjects(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Insert or refresh one chunk of subjects.

    A re-import refreshes the content (WaniKani does revise mnemonics) but
    never touches ``progress`` -- that is the learner's, not WaniKani's.
    """
    if not rows:
        return 0
    stmt = _upsert(Subject).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Subject.wanikani_id],
            set_={
                column: stmt.excluded[column]
                for column in (
                    "object_type",
                    "level",
                    "slug",
                    "characters",
                    "character_image_url",
                    "meanings",
                    "auxiliary_meanings",
                    "readings",
                    "component_subject_ids",
                    "parts_of_speech",
                    "meaning_mnemonic",
                    "meaning_hint",
                    "reading_mnemonic",
                    "reading_hint",
                    "sort_order",
                )
            },
        )
    )
    return len(rows)


async def _resolve_components(session: AsyncSession, wk_ids: dict[int, int]) -> None:
    """Rewrite ``component_subject_ids`` from WaniKani ids to local ones.

    A second pass because a kanji can list a radical that had not been
    inserted yet when its own row went in. Ids WaniKani did not return -- a
    component above the subscription's level cap -- are dropped rather than
    left dangling, so the UI never has to handle a component that is not
    there.
    """
    rows = await session.execute(
        select(Subject.id, Subject.component_subject_ids).where(
            Subject.component_subject_ids != []
        )
    )
    for local_id, components in rows:
        if not components:
            continue
        resolved = [wk_ids[c] for c in components if c in wk_ids]
        if resolved != components:
            await session.execute(
                update(Subject).where(Subject.id == local_id).values(
                    component_subject_ids=resolved
                )
            )
    await session.commit()


async def _ensure_progress_rows(session: AsyncSession) -> None:
    """Give every subject a progress row, defaulting to "not learned yet".

    ``on_conflict_do_nothing`` even on a remap: the assignment pass rewrites
    what it finds, and a subject WaniKani no longer has an assignment for
    should keep whatever the learner did with it here.
    """
    ids = (await session.execute(select(Subject.id))).scalars().all()
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        stmt = _upsert(Progress).values(
            [{"subject_id": subject_id, "state": ItemState.NEW.value} for subject_id in chunk]
        )
        await session.execute(stmt.on_conflict_do_nothing(index_elements=[Progress.subject_id]))
    await session.commit()


async def _apply_assignments(
    session: AsyncSession,
    client: WaniKaniClient,
    run_id: int,
    *,
    known_srs_stage: int,
    known_stage: int,
    intervals: tuple[int, ...],
    remap_existing: bool,
    wk_ids: dict[int, int],
) -> dict[str, int]:
    """Walk the assignments and set each item's starting position."""
    now = datetime.now(timezone.utc)
    counts = {"known": 0, "learning": 0, "new": 0}
    seen = 0

    # Which subjects already carry local progress worth protecting. Without
    # `remap_existing`, a second import must not undo reviews done since the
    # first one -- that would make re-importing to pick up new WaniKani
    # content cost the learner their work.
    touched: set[int] = set()
    if not remap_existing:
        rows = await session.execute(
            select(Progress.subject_id).where(Progress.state != ItemState.NEW.value)
        )
        touched = set(rows.scalars().all())

    async for page, total in client.iter_assignments():
        if seen == 0 and total:
            await session.execute(
                update(ImportRun).where(ImportRun.id == run_id).values(assignments_total=total)
            )
            await session.commit()

        for item in page:
            seen += 1
            data = item.get("data") or {}
            local_id = wk_ids.get(data.get("subject_id"))
            if local_id is None or local_id in touched:
                continue

            mapping = classify(data.get("srs_stage") or 0, known_srs_stage, known_stage)
            next_review = None
            if mapping.scheduled:
                # Keep WaniKani's own due date where there is one: an item due
                # in two days should not be pulled forward just because the
                # import happened today.
                next_review = parse_timestamp(data.get("available_at")) or schedule(
                    mapping.srs_stage, now, intervals
                )

            values: dict[str, Any] = {
                "state": mapping.state.value,
                "srs_stage": mapping.srs_stage,
                "next_review_at": next_review,
                "pending_meaning": None,
                "pending_reading": None,
                "session_incorrect": 0,
                "started_at": parse_timestamp(data.get("started_at")),
                "passed_at": parse_timestamp(data.get("passed_at")),
                "burned_at": parse_timestamp(data.get("burned_at")),
                "known_at": now if mapping.state is ItemState.KNOWN else None,
                "updated_at": now,
            }
            await session.execute(
                update(Progress).where(Progress.subject_id == local_id).values(**values)
            )

            if mapping.state is ItemState.KNOWN:
                counts["known"] += 1
            elif mapping.state is ItemState.LEARNING:
                counts["learning"] += 1

        await session.commit()

    # Counted rather than accumulated: "new" also covers every subject
    # WaniKani had no assignment for at all, which is most of them on a fresh
    # account and none of them on a finished one.
    counts["new"] = (
        await session.scalar(
            select(func.count())
            .select_from(Progress)
            .where(Progress.state == ItemState.NEW.value)
        )
    ) or 0

    return counts
