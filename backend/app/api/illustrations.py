"""A radical's mnemonic illustration -- fetched lazily, cached in our own
database (see :mod:`app.illustrations`), and never part of a queue item.

Mounted under ``/items`` alongside ``items.py``: both path shapes here have
two segments after ``{subject_id}``, so they never collide with that
module's ``/{subject_id}`` or ``/levels`` routes regardless of registration
order.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import Subject, get_session
from ..illustrations import get_or_fetch, load_stored
from ..models import IllustrationOut

router = APIRouter()

#: A week -- the SVG never changes once fetched, and a stale illustration on
#: a phone that skipped a refresh costs nothing worth a shorter cache.
_CACHE_SECONDS = 7 * 24 * 3600

_SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


@router.get("/{subject_id}/illustration", response_model=IllustrationOut)
async def read_illustration(
    subject_id: int, session: AsyncSession = Depends(get_session)
) -> IllustrationOut:
    """Whether there is an illustration to show, fetching it first if nothing
    fresh is cached yet -- see :func:`app.illustrations.get_or_fetch`."""
    subject = await session.get(Subject, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subject not found.")
    result = await get_or_fetch(session, subject)
    return IllustrationOut(available=result.available, alt=result.alt)


@router.get("/{subject_id}/illustration.svg")
async def read_illustration_svg(
    subject_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    """The stored SVG, verbatim -- never triggers a fetch.

    The response headers matter here more than for most endpoints: this is
    third-party markup, served from our own origin and shown through an
    ``<img>`` (where a script tag would not run anyway), but a direct
    navigation to this URL must not be able to run one either. ``nosniff``
    keeps a browser from re-guessing the content type, and the sandboxed CSP
    denies scripts, framing and everything else regardless.
    """
    row = await load_stored(session, subject_id)
    if row is None or row.svg is None:
        raise HTTPException(status_code=404, detail="No illustration stored.")
    return Response(
        content=row.svg,
        media_type="image/svg+xml",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _SVG_CSP,
            "Cache-Control": f"private, max-age={_CACHE_SECONDS}",
        },
    )
