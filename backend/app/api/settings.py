"""The Settings screen.

The WaniKani token never leaves the backend. What the UI gets is whether one
is set, a hint of its last characters so the learner can tell which token it
is, and whether it came from the environment -- which the UI needs in order to
explain why an env-provided token cannot be cleared from the screen.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import SETTINGS_ROW_ID, AppSettings, get_session, load_settings
from ..models import SettingsIn, SettingsOut, WaniKaniAccount
from ..runtime_config import load_runtime_config
from ..wanikani import WaniKaniClient, WaniKaniError

router = APIRouter()

#: How much of the token the hint shows. A WaniKani token is a UUID, so four
#: characters is enough to tell two apart and useless to anyone who sees it.
_HINT_CHARS = 4


def _hint(token: str) -> str:
    if not token:
        return ""
    return f"…{token[-_HINT_CHARS:]}" if len(token) > _HINT_CHARS else "…"


async def _current(session: AsyncSession) -> SettingsOut:
    row = await load_settings(session)
    config = await load_runtime_config(session)
    env = get_settings()

    stored = ((row.wanikani_api_token if row else None) or "").strip()
    return SettingsOut(
        wanikani_token_set=bool(config.wanikani_api_token),
        wanikani_token_hint=_hint(config.wanikani_api_token),
        wanikani_token_from_env=not stored and bool(env.wanikani_api_token),
        wanikani_known_srs_stage=config.wanikani_known_srs_stage,
        known_srs_stage=config.known_srs_stage,
        srs_interval_hours=",".join(str(hours) for hours in config.srs_intervals),
        daily_lesson_limit=config.daily_lesson_limit,
        lesson_batch_size=config.lesson_batch_size,
    )


@router.get("", response_model=SettingsOut)
async def read_settings(session: AsyncSession = Depends(get_session)) -> SettingsOut:
    return await _current(session)


@router.put("", response_model=SettingsOut)
async def write_settings(
    payload: SettingsIn, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    """Store the fields the request actually carried.

    A field left out is untouched; an empty string clears the column so the
    environment default applies again. Collapsing those two into one would
    make it impossible to remove an override once it is set.
    """
    values: dict[str, object] = {}
    fields = payload.model_dump(exclude_unset=True)

    if "wanikani_api_token" in fields:
        token = (fields["wanikani_api_token"] or "").strip()
        values["wanikani_api_token"] = token or None
    for name in (
        "wanikani_known_srs_stage",
        "known_srs_stage",
        "srs_interval_hours",
        "daily_lesson_limit",
        "lesson_batch_size",
    ):
        if name in fields:
            values[name] = fields[name]

    if values:
        values["updated_at"] = datetime.now(timezone.utc)
        await session.execute(
            update(AppSettings).where(AppSettings.id == SETTINGS_ROW_ID).values(**values)
        )
        await session.commit()

    return await _current(session)


@router.get("/wanikani/account", response_model=WaniKaniAccount)
async def wanikani_account(session: AsyncSession = Depends(get_session)) -> WaniKaniAccount:
    """Check the token against WaniKani and report whose account it is.

    Offered next to the token field because the two failure modes look
    identical from the import screen: a token that is wrong, and a token that
    is right but belongs to an account whose subscription lapsed.
    """
    config = await load_runtime_config(session)
    if not config.wanikani_configured:
        raise HTTPException(status_code=400, detail="No WaniKani token configured.")

    client = WaniKaniClient(
        token=config.wanikani_api_token,
        api_base=config.wanikani_api_base,
        revision=config.wanikani_revision,
    )
    try:
        user = await client.fetch_user()
    except WaniKaniError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    subscription = user.get("subscription") or {}
    return WaniKaniAccount(
        username=user.get("username") or "",
        level=user.get("level") or 0,
        max_level_granted=subscription.get("max_level_granted") or 0,
        subscription_active=bool(subscription.get("active")),
    )
