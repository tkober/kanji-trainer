"""The effective configuration for one request.

Two layers: the environment (``.env`` / compose ``env_file``) provides the
defaults a fresh deployment boots with, and the ``app_settings`` row overrides
anything the user changes in the Settings screen. A NULL column means "not set
here", so clearing a field in the UI falls back to the environment rather than
blanking the setting.

Loaded fresh per request instead of cached: the table has one row, and a stale
WaniKani token or SRS interval after a settings change would be far more
annoying than the lookup.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db import AppSettings, load_settings


@dataclass(frozen=True)
class RuntimeConfig:
    """Everything the app needs at runtime, with overrides already applied."""

    # --- user-editable ---
    wanikani_api_token: str
    wanikani_known_srs_stage: int
    known_srs_stage: int
    srs_intervals: tuple[int, ...]
    #: 0 means unlimited.
    daily_lesson_limit: int
    lesson_batch_size: int

    # --- environment only (infrastructure, not user business) ---
    wanikani_api_base: str
    wanikani_revision: str
    review_grace_minutes: int
    meaning_typo_tolerance_divisor: int

    @property
    def wanikani_configured(self) -> bool:
        return bool(self.wanikani_api_token)


def _pick_int(override: int | None, fallback: int) -> int:
    return fallback if override is None else override


def _parse_intervals(raw: str | None, fallback: tuple[int, ...]) -> tuple[int, ...]:
    """Parse an override interval table, falling back on anything malformed.

    A bad value must not be able to stop reviews from being scheduled: the one
    screen that could fix it is served by the same app.
    """
    if not raw:
        return fallback
    try:
        values = [int(part) for part in raw.split(",")]
    except ValueError:
        return fallback
    if len(values) < 10:
        values += list(fallback[len(values) :])
    return tuple(values[:10])


def build_runtime_config(row: AppSettings | None, env: Settings) -> RuntimeConfig:
    """Merge the settings row onto the environment defaults."""
    token = ((row.wanikani_api_token if row else None) or "").strip()

    return RuntimeConfig(
        wanikani_api_token=token or env.wanikani_api_token,
        wanikani_known_srs_stage=_pick_int(
            row.wanikani_known_srs_stage if row else None, env.wanikani_known_srs_stage
        ),
        known_srs_stage=_pick_int(
            row.known_srs_stage if row else None, env.known_srs_stage
        ),
        srs_intervals=_parse_intervals(
            row.srs_interval_hours if row else None, env.srs_intervals
        ),
        daily_lesson_limit=_pick_int(row.daily_lesson_limit if row else None, 0),
        lesson_batch_size=max(
            1, _pick_int(row.lesson_batch_size if row else None, env.lesson_batch_size)
        ),
        wanikani_api_base=env.wanikani_api_base,
        wanikani_revision=env.wanikani_revision,
        review_grace_minutes=env.review_grace_minutes,
        meaning_typo_tolerance_divisor=env.meaning_typo_tolerance_divisor,
    )


async def load_runtime_config(session: AsyncSession) -> RuntimeConfig:
    """Read the settings row and merge it onto the environment."""
    return build_runtime_config(await load_settings(session), get_settings())
