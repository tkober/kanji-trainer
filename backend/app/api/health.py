"""Liveness, used by the container healthcheck."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Health

router = APIRouter()


@router.get("/health", response_model=Health)
async def health(session: AsyncSession = Depends(get_session)) -> Health:
    """Report whether the process is up and the database answers.

    The compose healthcheck only looks at the status code, so a database that
    is down must not fail this: the backend waits for postgres-core on its own
    and a failing healthcheck would keep the frontend from ever starting.
    """
    try:
        await session.execute(text("SELECT 1"))
        database = True
    except Exception:  # noqa: BLE001 - reported, not raised
        database = False
    return Health(status="ok", database=database)
