"""Browsing the collection, and the three overrides WaniKani does not offer.

``POST /api/items/known`` is the feature the whole project is built around.
Everything else here exists to support it: you have to be able to *find* the
items you already know before you can say so, and you have to be able to take
it back when an item turns out to be shakier than it felt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import srs
from ..db import Progress, Subject, get_session
from ..models import ItemOut, ItemPage, MarkKnownIn, OverrideResult, SubjectIdsIn
from ..runtime_config import load_runtime_config
from ..serialize import progress_out, subject_detail
from ..srs import ItemState

router = APIRouter()


@router.get("", response_model=ItemPage)
async def list_items(
    state: str | None = Query(default=None),
    object_type: str | None = Query(default=None),
    level: int | None = Query(default=None, ge=1),
    srs_stage: int | None = Query(default=None, ge=0, le=9),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ItemPage:
    """Filtered list of items with their progress.

    The filters are the ones that make bulk-declaring realistic: "alle Kanji
    aus Level 1–20, die noch als neu gelten" is one query, and the result is
    selectable in one gesture.
    """
    filters = []
    if state:
        filters.append(Progress.state == state)
    if object_type:
        filters.append(Subject.object_type == object_type)
    if level is not None:
        filters.append(Subject.level == level)
    if srs_stage is not None:
        filters.append(Progress.srs_stage == srs_stage)
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(or_(Subject.characters.ilike(pattern), Subject.slug.ilike(pattern)))

    total = (
        await session.scalar(
            select(func.count())
            .select_from(Progress)
            .join(Subject, Subject.id == Progress.subject_id)
            .where(*filters)
        )
        or 0
    )
    rows = await session.execute(
        select(Progress, Subject)
        .join(Subject, Subject.id == Progress.subject_id)
        .where(*filters)
        .order_by(Subject.sort_order, Subject.id)
        .offset(offset)
        .limit(limit)
    )

    return ItemPage(
        items=[
            ItemOut(subject=subject_detail(subject), progress=progress_out(progress))
            for progress, subject in rows
        ],
        total=total,
    )


@router.get("/{subject_id}", response_model=ItemOut)
async def read_item(
    subject_id: int, session: AsyncSession = Depends(get_session)
) -> ItemOut:
    row = (
        await session.execute(
            select(Progress, Subject)
            .join(Subject, Subject.id == Progress.subject_id)
            .where(Subject.id == subject_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Subject not found.")
    progress, subject = row
    return ItemOut(subject=subject_detail(subject), progress=progress_out(progress))


# --- the overrides ---------------------------------------------------------


async def _load(session: AsyncSession, subject_ids: list[int]) -> list[Progress]:
    if not subject_ids:
        raise HTTPException(status_code=400, detail="No subjects given.")
    rows = await session.execute(
        select(Progress).where(Progress.subject_id.in_(subject_ids))
    )
    return list(rows.scalars())


@router.post("/known", response_model=OverrideResult)
async def mark_known(
    payload: MarkKnownIn, session: AsyncSession = Depends(get_session)
) -> OverrideResult:
    """"Das kann ich" -- the reason this trainer exists.

    Works on any item in any state, including one that has never been
    learned: skipping the lesson entirely is exactly what someone returning
    after a break needs for the first thirty levels.
    """
    config = await load_runtime_config(session)
    now = datetime.now(timezone.utc)
    stage = payload.known_stage or config.known_srs_stage

    rows = await _load(session, payload.subject_ids)
    for progress in rows:
        srs.mark_known(progress, now, stage, config.srs_intervals)
        progress.updated_at = now

    await session.commit()
    return OverrideResult(changed=len(rows))


@router.post("/reset", response_model=OverrideResult)
async def reset_items(
    payload: SubjectIdsIn, session: AsyncSession = Depends(get_session)
) -> OverrideResult:
    """"Nochmal von vorn" -- back to Apprentice I, due in four hours."""
    config = await load_runtime_config(session)
    now = datetime.now(timezone.utc)

    rows = await _load(session, payload.subject_ids)
    for progress in rows:
        srs.reset_to_apprentice(progress, now, config.srs_intervals)
        progress.updated_at = now

    await session.commit()
    return OverrideResult(changed=len(rows))


@router.post("/suspend", response_model=OverrideResult)
async def suspend_items(
    payload: SubjectIdsIn, session: AsyncSession = Depends(get_session)
) -> OverrideResult:
    """"Ausblenden" -- out of every queue, history kept."""
    now = datetime.now(timezone.utc)
    rows = await _load(session, payload.subject_ids)
    for progress in rows:
        srs.suspend(progress)
        progress.updated_at = now

    await session.commit()
    return OverrideResult(changed=len(rows))


@router.post("/unsuspend", response_model=OverrideResult)
async def unsuspend_items(
    payload: SubjectIdsIn, session: AsyncSession = Depends(get_session)
) -> OverrideResult:
    now = datetime.now(timezone.utc)
    rows = await _load(session, payload.subject_ids)
    changed = 0
    for progress in rows:
        if progress.state != ItemState.SUSPENDED.value:
            continue
        srs.unsuspend(progress, now)
        progress.updated_at = now
        changed += 1

    await session.commit()
    return OverrideResult(changed=changed)
