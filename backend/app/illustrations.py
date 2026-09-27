"""Radical mnemonic illustrations, fetched lazily from wanikani.com.

WaniKani's API (``app/wanikani.py``) has no endpoint for these -- the
mnemonic image WaniKani shows on a radical's own page
(``https://www.wanikani.com/radicals/<slug>``, in a ``<wk-mnemonic-image>``
element) is not part of ``character_images``, which are glyph SVGs only. So
this module scrapes the one element it needs from that public, unauthenticated
page instead, the first time a radical's detail is actually shown, and caches
the result in :class:`app.db.SubjectIllustration` so it keeps working without
WaniKani afterwards.

Deliberately conservative about when it talks to the network at all:

* only a radical with a ``wanikani_id`` has a page to scrape -- a hand-added
  item, or any kanji/vocabulary, is "unavailable" without a single request.
* a row with ``svg`` already set is never re-fetched.
* a row recording "the page had no illustration" is re-checked only after
  :data:`RECHECK_AFTER_DAYS` -- WaniKani keeps adding art for radicals that
  did not have any, but not on a timescale that is worth checking every view.
* a network or HTTP failure stores nothing at all, so the next view retries
  rather than getting stuck on a transient error.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .db import Subject, SubjectIllustration, _upsert
from .serialize import wanikani_url

log = logging.getLogger(__name__)

# WaniKani is still adding illustrations to radicals that never had one, so
# "no <wk-mnemonic-image> on the page" is not forever -- but checking every
# view for something that changes at most a few times a year would be a
# request per radical detail for nothing.
RECHECK_AFTER_DAYS = 30

# The SVGs this is fetching run a few KB; anything past this is not one of
# them, and reading further would just be handing an attacker-sized response
# to our own storage.
MAX_SVG_BYTES = 512 * 1024

_TIMEOUT = httpx.Timeout(10.0)

# Tolerant of attribute order and quoting style -- the two attributes this
# cares about, `src` and `aria-label`, can appear in either order on the real
# page, and a full HTML parse is more machinery than one element needs.
_ELEMENT_RE = re.compile(r"<wk-mnemonic-image\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z_-]+)\s*=\s*"([^"]*)"')


@dataclass(frozen=True)
class IllustrationResult:
    """What the JSON endpoint needs to answer -- never the SVG itself."""

    available: bool
    alt: str | None


@dataclass(frozen=True)
class _Fetched:
    """The outcome of one attempt to get the illustration from wanikani.com."""

    kind: Literal["found", "none", "error"]
    source_url: str | None = None
    alt: str | None = None
    svg: str | None = None


def eligible(subject: Subject) -> bool:
    """Whether this subject could possibly have a page to scrape.

    Only WaniKani-sourced radicals have one: hand-added items (no
    ``wanikani_id``) and every non-radical type never carry this art.
    """
    return subject.object_type == "radical" and subject.wanikani_id is not None


def _client_factory() -> httpx.AsyncClient:
    """The client used for both requests.

    A function rather than a module-level client so tests can monkeypatch it
    to one built on ``httpx.MockTransport`` -- nothing here should ever open a
    real connection in a test run.
    """
    return httpx.AsyncClient(timeout=_TIMEOUT)


async def load_stored(session: AsyncSession, subject_id: int) -> SubjectIllustration | None:
    """The cached row, however stale -- for the endpoint that never fetches."""
    return await session.get(SubjectIllustration, subject_id)


def _is_fresh(row: SubjectIllustration) -> bool:
    if row.svg is not None:
        return True
    age = datetime.now(timezone.utc) - row.checked_at
    return age < timedelta(days=RECHECK_AFTER_DAYS)


async def get_or_fetch(session: AsyncSession, subject: Subject) -> IllustrationResult:
    """The answer for the JSON endpoint, fetching first if there is nothing
    fresh cached yet.

    No DB transaction is held open across the HTTP calls: the read below ends
    its own (implicit) transaction with a commit before anything goes out
    over the network, and the eventual write opens a new, short one.
    """
    if not eligible(subject):
        return IllustrationResult(available=False, alt=None)

    row = await load_stored(session, subject.id)
    await session.commit()
    if row is not None and _is_fresh(row):
        return IllustrationResult(available=row.svg is not None, alt=row.alt)

    fetched = await _fetch(subject)
    await _store(session, subject.id, fetched)

    if fetched.kind == "error":
        # Nothing was written -- report whatever was already known (a stale
        # "none", or nothing at all) rather than claim it is now unavailable.
        return IllustrationResult(
            available=row is not None and row.svg is not None,
            alt=row.alt if row is not None else None,
        )
    return IllustrationResult(available=fetched.kind == "found", alt=fetched.alt)


async def _fetch(subject: Subject) -> _Fetched:
    page_url = wanikani_url(subject)
    if page_url is None:
        return _Fetched("error")

    async with _client_factory() as client:
        try:
            page_response = await client.get(page_url)
        except httpx.HTTPError as exc:
            log.info("Could not reach %s for an illustration: %s", page_url, exc)
            return _Fetched("error")

        if page_response.status_code != 200:
            log.info("Unexpected status %d fetching %s", page_response.status_code, page_url)
            return _Fetched("error")

        src, alt = _extract_mnemonic_image(page_response.text)
        if src is None:
            return _Fetched("none", alt=alt)

        try:
            image_response = await client.get(src)
        except httpx.HTTPError as exc:
            log.info("Could not fetch illustration %s: %s", src, exc)
            return _Fetched("error")

    if image_response.status_code != 200:
        return _Fetched("error")
    content_type = image_response.headers.get("content-type", "")
    if "image/svg+xml" not in content_type:
        return _Fetched("error")
    body = image_response.text
    if len(body.encode("utf-8")) > MAX_SVG_BYTES or "<svg" not in body:
        return _Fetched("error")

    return _Fetched("found", source_url=src, alt=alt, svg=body)


def _extract_mnemonic_image(page_html: str) -> tuple[str | None, str | None]:
    """The first ``<wk-mnemonic-image>``'s ``src`` and ``aria-label``, if any.

    Attribute order does not matter (a real regex per attribute rather than
    one that assumes a fixed layout), and entities in the label (``&amp;``
    etc.) are unescaped -- WaniKani's mnemonics use plain punctuation, but the
    aria-label is free text and there is no reason to trust it is bare.
    """
    element = _ELEMENT_RE.search(page_html)
    if element is None:
        return None, None
    attrs = dict(_ATTR_RE.findall(element.group(1)))
    src = attrs.get("src")
    alt = attrs.get("aria-label")
    return (src or None), (html.unescape(alt) if alt else None)


async def _store(session: AsyncSession, subject_id: int, fetched: _Fetched) -> None:
    if fetched.kind == "error":
        return

    stmt = _upsert(SubjectIllustration).values(
        subject_id=subject_id,
        source_url=fetched.source_url,
        alt=fetched.alt,
        svg=fetched.svg,
        checked_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SubjectIllustration.subject_id],
        set_={
            "source_url": stmt.excluded.source_url,
            "alt": stmt.excluded.alt,
            "svg": stmt.excluded.svg,
            "checked_at": stmt.excluded.checked_at,
        },
    )
    await session.execute(stmt)
    await session.commit()
