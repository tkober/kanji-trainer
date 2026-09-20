"""WaniKani API v2 client, used once to seed the trainer.

Read-only by design. Nothing here writes back: the import is a one-way copy of
what the learner already knows, and after it the trainer's own SRS is the
source of truth. That is also why a read-only personal access token is enough,
and what the Settings screen asks for.

The endpoints page, so everything that returns a collection is an async
generator over *pages* rather than a list: an import of ~9.000 subjects takes
long enough that the UI needs to show it moving, and holding every page in
memory to hand back one list would buy nothing.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# WaniKani's collection endpoints page at 1000 (subjects) / 500 (assignments).
_PAGE_TIMEOUT_SECONDS = 60.0


class WaniKaniError(RuntimeError):
    """Raised when the WaniKani API cannot be queried."""


class WaniKaniClient:
    """Minimal async WaniKani v2 client."""

    def __init__(
        self,
        token: str = "",
        api_base: str = "https://api.wanikani.com/v2",
        revision: str = "20170710",
    ) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.revision = revision

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            # Pinned: WaniKani promises not to break a revision, so an
            # unattended import cannot be surprised by a schema change.
            "Wanikani-Revision": self.revision,
        }

    # --- endpoints -------------------------------------------------------

    async def fetch_user(self) -> dict[str, Any]:
        """The account behind the token. Used to validate it and show a level.

        Also the cheapest way to learn whether the subscription is active:
        ``subscription.max_level_granted`` is 3 for a free account, and an
        import that silently stopped at level 3 is worth warning about before
        it runs rather than after.
        """
        self._require_token()
        async with httpx.AsyncClient(timeout=30.0, headers=self._headers()) as client:
            payload = await self._get_json(client, f"{self.api_base}/user", None)
        return payload.get("data") or {}

    async def iter_subjects(self) -> AsyncIterator[tuple[list[dict[str, Any]], int]]:
        """Every subject, page by page, with the collection's total count."""
        async for page in self._iter_pages(f"{self.api_base}/subjects", {"hidden": "false"}):
            yield page

    async def iter_assignments(self) -> AsyncIterator[tuple[list[dict[str, Any]], int]]:
        """Every assignment, page by page, with the collection's total count.

        Assignments are the half that matters: they carry ``srs_stage``, which
        is what lets the import decide that an item is already known instead
        of asking the learner to re-earn it.
        """
        async for page in self._iter_pages(f"{self.api_base}/assignments", {"hidden": "false"}):
            yield page

    # --- internals -------------------------------------------------------

    def _require_token(self) -> None:
        if not self.enabled:
            raise WaniKaniError("No WaniKani token configured.")

    async def _iter_pages(
        self, start_url: str, params: dict[str, str] | None
    ) -> AsyncIterator[tuple[list[dict[str, Any]], int]]:
        self._require_token()
        url: str | None = start_url
        query = params

        async with httpx.AsyncClient(
            timeout=_PAGE_TIMEOUT_SECONDS, headers=self._headers()
        ) as client:
            while url:
                payload = await self._get_json(client, url, query)
                data = payload.get("data") or []
                total = payload.get("total_count") or len(data)
                yield data, total
                # `next_url` already carries the query string, so passing the
                # original params again would duplicate them.
                url = (payload.get("pages") or {}).get("next_url")
                query = None

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict[str, str] | None,
    ) -> dict[str, Any]:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise WaniKaniError("WaniKani rejected the API token (401).") from exc
            if status == 429:
                raise WaniKaniError("WaniKani rate limit reached (429).") from exc
            raise WaniKaniError(f"WaniKani request failed with HTTP {status}.") from exc
        except httpx.HTTPError as exc:
            raise WaniKaniError(f"Could not reach WaniKani: {exc}") from exc


# --- payload shaping -------------------------------------------------------


def pick_character_image(entry: dict[str, Any]) -> str | None:
    """The image to show for a radical WaniKani has no character for.

    Prefers the SVG without inline styles -- the styled variant carries a hard
    coded fill colour, which disappears against a dark background.
    """
    images = entry.get("character_images") or []
    for image in images:
        metadata = image.get("metadata") or {}
        if image.get("content_type") == "image/svg+xml" and not metadata.get("inline_styles"):
            return image.get("url")
    for image in images:
        if image.get("content_type") == "image/svg+xml":
            return image.get("url")
    return images[0].get("url") if images else None


def subject_row(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten one WaniKani subject into the columns :class:`Subject` has.

    ``component_subject_ids`` stays in WaniKani's numbering here; the importer
    rewrites it to local ids once every subject has one.
    """
    data = item.get("data") or {}
    return {
        "wanikani_id": item.get("id"),
        "object_type": item.get("object") or "",
        "level": data.get("level") or 1,
        "slug": data.get("slug") or "",
        "characters": data.get("characters"),
        "character_image_url": pick_character_image(data),
        "meanings": data.get("meanings") or [],
        "auxiliary_meanings": data.get("auxiliary_meanings") or [],
        "readings": data.get("readings") or [],
        "component_subject_ids": data.get("component_subject_ids") or [],
        "parts_of_speech": data.get("parts_of_speech") or [],
        "meaning_mnemonic": data.get("meaning_mnemonic") or "",
        "meaning_hint": data.get("meaning_hint"),
        "reading_mnemonic": data.get("reading_mnemonic"),
        "reading_hint": data.get("reading_hint"),
        # WaniKani's own teaching order, so lessons can follow it.
        "sort_order": (data.get("level") or 1) * 1000 + _TYPE_ORDER.get(item.get("object"), 9),
    }


#: Radicals before the kanji built from them, kanji before their vocabulary --
#: the order WaniKani teaches in, and the order the lesson queue follows.
_TYPE_ORDER = {"radical": 0, "kanji": 1, "vocabulary": 2, "kana_vocabulary": 3}
