"""Learner-entered synonyms for a subject's *meaning* (issue #33).

Modelled on WaniKani's "User Synonyms": a free-form extra spelling the
learner says also counts as correct for this item's meaning, checked in
``answers.check_meaning``. Never the reading -- a reading tolerates nothing,
see the asymmetry documented in :mod:`app.answers`.

Stored in :class:`app.db.SubjectSynonyms`, its own table for the same reason
as ``subject_illustrations``: the importer upserts ``subjects`` wholesale on
every (re-)import, and this is the learner's own data, never WaniKani's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import SubjectSynonyms

#: WaniKani's own limit on "User Synonyms" per item.
MAX_SYNONYMS = 8
MAX_LENGTH = 64

_WHITESPACE = re.compile(r"\s+")


def _dedupe_trim(raw: Iterable[str]) -> list[str]:
    """Trim, collapse inner whitespace, drop empties, and dedupe
    case-insensitively -- first spelling wins. No length or count limit here:
    the two callers below enforce those differently (one rejects, the other
    truncates), so the shared part stops before that split.
    """
    seen: set[str] = set()
    cleaned: list[str] = []
    for entry in raw:
        value = _WHITESPACE.sub(" ", entry.strip())
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned


def normalise_synonyms(raw: Iterable[str]) -> list[str]:
    """Validate a learner-submitted list, or raise ``HTTPException(422)``.

    Used by the endpoint, where a violation is the caller's mistake and
    deserves an error rather than a silent fix.
    """
    cleaned = _dedupe_trim(raw)
    for value in cleaned:
        if len(value) > MAX_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"A synonym can be at most {MAX_LENGTH} characters: {value!r}.",
            )
    if len(cleaned) > MAX_SYNONYMS:
        raise HTTPException(
            status_code=422, detail=f"At most {MAX_SYNONYMS} synonyms are allowed per item."
        )
    return cleaned


def merge_from_import(local: list[str], imported: Iterable[str]) -> list[str]:
    """Union of the local list and WaniKani's own ``meaning_synonyms``, local
    entries first so they are the ones kept if the union is over the limit.

    Truncates rather than raises: this runs unattended during an import, and
    an oversized or over-long entry here is WaniKani's data, not a mistake to
    report back to the learner. Never deletes a local entry -- the import
    only ever adds.
    """
    merged = [value for value in _dedupe_trim([*local, *imported]) if len(value) <= MAX_LENGTH]
    return merged[:MAX_SYNONYMS]


async def load_synonyms(session: AsyncSession, subject_id: int) -> list[str]:
    row = await session.get(SubjectSynonyms, subject_id)
    return list(row.synonyms) if row is not None else []


async def load_synonyms_map(
    session: AsyncSession, subject_ids: Iterable[int]
) -> dict[int, list[str]]:
    """Every row for the given ids, in one query -- for a list view, not N+1."""
    ids = list(subject_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(SubjectSynonyms).where(SubjectSynonyms.subject_id.in_(ids))
    )
    return {row.subject_id: list(row.synonyms) for row in rows.scalars()}


async def set_synonyms(session: AsyncSession, subject_id: int, cleaned: list[str]) -> None:
    """Write an already-cleaned list, without committing.

    Low-level: both callers below have already decided what "cleaned" means
    for them (:func:`normalise_synonyms` for the endpoint,
    :func:`merge_from_import` for the importer) before reaching here.
    """
    row = await session.get(SubjectSynonyms, subject_id)
    if not cleaned:
        # An empty list deletes the row rather than keeping an empty one
        # around -- nothing downstream needs to tell "never set" from "set to
        # nothing", and a missing row is one fewer place to check.
        if row is not None:
            await session.delete(row)
        return
    now = datetime.now(timezone.utc)
    if row is None:
        session.add(SubjectSynonyms(subject_id=subject_id, synonyms=cleaned, updated_at=now))
    else:
        row.synonyms = cleaned
        row.updated_at = now


async def save_synonyms(session: AsyncSession, subject_id: int, raw: Iterable[str]) -> list[str]:
    """Validate, store and commit -- the endpoint's entry point."""
    cleaned = normalise_synonyms(raw)
    await set_synonyms(session, subject_id, cleaned)
    await session.commit()
    return cleaned
