"""Starting and following a WaniKani import."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import ImportRun, get_session, get_sessionmaker
from ..importer import start_import
from ..models import ImportRunOut, ImportStartIn
from ..runtime_config import load_runtime_config

router = APIRouter()


@router.post("", response_model=ImportRunOut, status_code=202)
async def begin_import(
    payload: ImportStartIn, session: AsyncSession = Depends(get_session)
) -> ImportRun:
    """Kick off an import and hand back the run to poll.

    202, not 200: an import of ~9.000 subjects runs for minutes, and a request
    that waited for it would hit nginx's read timeout long before it finished.
    """
    config = await load_runtime_config(session)
    if not config.wanikani_configured:
        raise HTTPException(status_code=400, detail="No WaniKani token configured.")

    threshold = payload.known_srs_stage or config.wanikani_known_srs_stage
    try:
        run_id = await start_import(
            get_sessionmaker(),
            config,
            known_srs_stage=threshold,
            remap_existing=payload.remap_existing,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    run = await session.get(ImportRun, run_id)
    if run is None:  # pragma: no cover - the row was just committed
        raise HTTPException(status_code=500, detail="Import run vanished after creation.")
    return run


@router.get("/latest", response_model=ImportRunOut | None)
async def latest_import(session: AsyncSession = Depends(get_session)) -> ImportRun | None:
    return await session.scalar(select(ImportRun).order_by(ImportRun.id.desc()).limit(1))


@router.get("/{run_id}", response_model=ImportRunOut)
async def read_import(
    run_id: int, session: AsyncSession = Depends(get_session)
) -> ImportRun:
    run = await session.get(ImportRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Import run not found.")
    return run
