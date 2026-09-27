"""Radical mnemonic illustrations: fetched lazily, cached, never in the queue.

No real network anywhere here -- `_client_factory` is monkeypatched to build
on `httpx.MockTransport`, so a test that expects zero requests can prove it by
asserting the call log stayed empty rather than by trusting a timeout.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import illustrations
from app.db import Progress, Subject, SubjectIllustration
from app.importer import _flush_subjects
from app.srs import ItemState
from app.wanikani import subject_row

PAGE_WITH_IMAGE = (
    "<html><body>"
    '<wk-mnemonic-image src="https://files.wanikani.com/i8oxg6xj34x761lsdmqk5p9mh3kx" '
    'aria-label="The surface of the ground, corresponding to the horizontal line '
    'of the radical." class="subject-mnemonic-image__image" '
    'style="aspect-ratio: 1/1;"></wk-mnemonic-image>'
    "</body></html>"
)
PAGE_WITHOUT_IMAGE = "<html><body><p>Nothing to see here.</p></body></html>"
SVG_BODY = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'


def _install_transport(monkeypatch, handler) -> list[str]:
    """Point the module's client factory at a mock transport, and return the
    list every request URL gets appended to."""
    calls: list[str] = []

    def logging_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(logging_handler), timeout=httpx.Timeout(10.0)
        )

    monkeypatch.setattr(illustrations, "_client_factory", factory)
    return calls


def _found_handler(request: httpx.Request) -> httpx.Response:
    if "files.wanikani.com" in str(request.url):
        return httpx.Response(200, headers={"content-type": "image/svg+xml"}, text=SVG_BODY)
    return httpx.Response(200, text=PAGE_WITH_IMAGE)


def _none_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=PAGE_WITHOUT_IMAGE)


async def seed_radical(
    session, *, wanikani_id: int | None = 1, slug: str = "ground", level: int = 1
) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type="radical",
        level=level,
        slug=slug,
        characters="一",
        meanings=[{"meaning": "Ground", "primary": True, "accepted_answer": True}],
        meaning_mnemonic="The ground.",
    )
    session.add(subject)
    await session.flush()
    session.add(Progress(subject_id=subject.id, state=ItemState.NEW.value))
    await session.commit()
    return subject.id


async def seed_kanji(session, *, wanikani_id: int = 440, slug: str = "上") -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type="kanji",
        level=1,
        slug=slug,
        characters=slug,
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
        meaning_mnemonic="A toe above the ground.",
    )
    session.add(subject)
    await session.flush()
    session.add(Progress(subject_id=subject.id, state=ItemState.NEW.value))
    await session.commit()
    return subject.id


# --- the happy path ---------------------------------------------------------


async def test_first_view_fetches_and_stores(client, session, monkeypatch):
    calls = _install_transport(monkeypatch, _found_handler)
    subject_id = await seed_radical(session)

    body = (await client.get(f"/api/items/{subject_id}/illustration")).json()

    assert body == {
        "available": True,
        "alt": (
            "The surface of the ground, corresponding to the horizontal line "
            "of the radical."
        ),
    }
    assert len(calls) == 2  # the page, then the image

    row = await session.get(SubjectIllustration, subject_id)
    assert row is not None
    assert row.svg is not None and "<svg" in row.svg


async def test_second_view_serves_from_the_database(client, session, monkeypatch):
    calls = _install_transport(monkeypatch, _found_handler)
    subject_id = await seed_radical(session)

    await client.get(f"/api/items/{subject_id}/illustration")
    assert len(calls) == 2

    body = (await client.get(f"/api/items/{subject_id}/illustration")).json()
    assert body["available"] is True
    assert len(calls) == 2, "a fresh row must not trigger a second fetch"


async def test_page_without_the_element_is_stored_as_none(client, session, monkeypatch):
    calls = _install_transport(monkeypatch, _none_handler)
    subject_id = await seed_radical(session)

    body = (await client.get(f"/api/items/{subject_id}/illustration")).json()

    assert body == {"available": False, "alt": None}
    assert len(calls) == 1  # only the page -- there was no src to fetch

    row = await session.get(SubjectIllustration, subject_id)
    assert row is not None and row.svg is None


async def test_a_stale_none_is_rechecked_after_the_recheck_window(client, session, monkeypatch):
    calls = _install_transport(monkeypatch, _none_handler)
    subject_id = await seed_radical(session)

    await client.get(f"/api/items/{subject_id}/illustration")
    assert len(calls) == 1

    # Still within the window: no second request.
    await client.get(f"/api/items/{subject_id}/illustration")
    assert len(calls) == 1

    # Age the row past the recheck window by hand -- WaniKani has since added
    # art for it.
    row = await session.get(SubjectIllustration, subject_id)
    row.checked_at = datetime.now(timezone.utc) - timedelta(
        days=illustrations.RECHECK_AFTER_DAYS + 1
    )
    await session.commit()

    _install_transport(monkeypatch, _found_handler)
    body = (await client.get(f"/api/items/{subject_id}/illustration")).json()
    assert body["available"] is True


async def test_network_error_stores_nothing_and_the_next_view_retries(
    client, session, monkeypatch
):
    def failing(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    calls = _install_transport(monkeypatch, failing)
    subject_id = await seed_radical(session)

    body = (await client.get(f"/api/items/{subject_id}/illustration")).json()
    assert body == {"available": False, "alt": None}
    assert len(calls) == 1
    assert await session.get(SubjectIllustration, subject_id) is None

    # The next view is a fresh attempt, not something stuck on the failure.
    _install_transport(monkeypatch, _found_handler)
    body = (await client.get(f"/api/items/{subject_id}/illustration")).json()
    assert body["available"] is True


@pytest.mark.parametrize(
    "seed",
    [
        lambda session: seed_kanji(session),
        lambda session: seed_radical(session, wanikani_id=None, slug="hand-added"),
    ],
)
async def test_ineligible_subjects_are_never_fetched(client, session, monkeypatch, seed):
    calls = _install_transport(monkeypatch, _found_handler)
    subject_id = await seed(session)

    body = (await client.get(f"/api/items/{subject_id}/illustration")).json()

    assert body == {"available": False, "alt": None}
    assert calls == []


async def test_unknown_subject_is_a_404(client, monkeypatch):
    _install_transport(monkeypatch, _found_handler)
    response = await client.get("/api/items/999999/illustration")
    assert response.status_code == 404


# --- the .svg endpoint -------------------------------------------------------


async def test_svg_endpoint_serves_the_stored_svg_with_locked_down_headers(
    client, session, monkeypatch
):
    _install_transport(monkeypatch, _found_handler)
    subject_id = await seed_radical(session)
    await client.get(f"/api/items/{subject_id}/illustration")

    response = await client.get(f"/api/items/{subject_id}/illustration.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "private" in response.headers["cache-control"]
    assert "<svg" in response.text


async def test_svg_endpoint_never_fetches(client, session, monkeypatch):
    calls = _install_transport(monkeypatch, _found_handler)
    subject_id = await seed_radical(session)

    response = await client.get(f"/api/items/{subject_id}/illustration.svg")

    assert response.status_code == 404
    assert calls == [], "the .svg endpoint must never trigger a fetch"


# --- the queue invariant -----------------------------------------------------


async def test_the_review_queue_never_carries_or_fetches_an_illustration(
    client, session, monkeypatch
):
    calls = _install_transport(monkeypatch, _found_handler)
    subject_id = await seed_radical(session)
    from datetime import timedelta as _timedelta

    progress = await session.get(Progress, subject_id)
    progress.next_review_at = datetime.now(timezone.utc) - _timedelta(minutes=1)
    progress.state = ItemState.LEARNING.value
    progress.srs_stage = 1
    await session.commit()

    body = (await client.get("/api/reviews")).json()

    assert body["items"], "the seeded item should be due"
    for item in body["items"]:
        assert "illustration" not in item["subject"]
    assert calls == [], "the review queue must never trigger an illustration fetch"


# --- re-import ---------------------------------------------------------------


async def test_reimport_does_not_delete_a_stored_illustration(client, session, monkeypatch):
    _install_transport(monkeypatch, _found_handler)
    subject_id = await seed_radical(session, wanikani_id=1, slug="ground")
    await client.get(f"/api/items/{subject_id}/illustration")
    assert (await session.get(SubjectIllustration, subject_id)) is not None

    progress = await session.get(Progress, subject_id)
    progress.correct_count = 5
    await session.commit()

    # The importer re-upserts the subject on every (re-)import; it must never
    # touch subject_illustrations or progress.
    wk_item = {
        "id": 1,
        "object": "radical",
        "data": {
            "level": 1,
            "slug": "ground",
            "characters": "一",
            "meanings": [{"meaning": "Ground", "primary": True, "accepted_answer": True}],
            "meaning_mnemonic": "The ground, revised.",
        },
    }
    await _flush_subjects(session, [subject_row(wk_item)])
    await session.commit()

    illustration = await session.get(SubjectIllustration, subject_id)
    assert illustration is not None and illustration.svg is not None

    progress_after = await session.get(Progress, subject_id)
    assert progress_after.correct_count == 5

    subject_after = await session.get(Subject, subject_id)
    assert subject_after.meaning_mnemonic == "The ground, revised."
