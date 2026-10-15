"""The read-only MCP server (issue #75): the known-items tools, the learning
summary and the lookup tool, plus the transport itself -- the endpoint is
exactly `/mcp` and a disallowed Host header is rejected before any of that
runs.

Most assertions call the tool functions directly (the `@mcp.tool()`
decorator returns the original function unchanged, so they are plain
coroutines here, same as any other module-level function) -- that is what
lets this file exercise every known-rule edge case without round-tripping
JSON-RPC for each one. One test still goes through a real MCP client over
HTTP (`mcp.client.streamable_http`, against the ASGI app via
`httpx.ASGITransport`, same transport the `client` fixture uses) to prove the
wiring in app/main.py actually holds end to end.
"""

from __future__ import annotations

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.db import Progress, Subject
from app.srs import ItemState


async def seed_subject(
    session,
    *,
    wanikani_id: int,
    object_type: str,
    level: int,
    characters: str,
    state: str,
    srs_stage: int,
    meanings: list[dict] | None = None,
    readings: list[dict] | None = None,
) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type=object_type,
        level=level,
        slug=characters,
        characters=characters,
        meanings=(
            meanings
            if meanings is not None
            else [{"meaning": f"meaning-{wanikani_id}", "primary": True, "accepted_answer": True}]
        ),
        readings=readings if readings is not None else [],
    )
    session.add(subject)
    await session.flush()
    session.add(Progress(subject_id=subject.id, state=state, srs_stage=srs_stage))
    await session.commit()
    return subject.id


async def seed_learner(session) -> None:
    """A small, deliberately mixed collection:

    - "上" is a KNOWN kanji (declared, any stage still counts) *and* a
      LEARNING vocabulary word below the known threshold -- same characters,
      different object types, so `lookup_items` has something to split.
    - "山" is LEARNING at exactly the Guru I threshold (known).
    - "火" is LEARNING below it (not known).
    - "水" is SUSPENDED at stage 9 -- never known, regardless of stage.
    - "木" and "口" are NEW, at level 2 -- the lowest level with anything
      left to start, so `lowest_open_level()` is 2 even though everything
      above is level 1, 3, 4 or 5.
    - "元気" is a KNOWN vocabulary word; "すし" a LEARNING kana_vocabulary
      word at the known threshold.
    - "一" is a LEARNING radical at the known threshold -- known, but never
      returned by `get_known_kanji`/`get_known_vocabulary`, which only look
      at their own object types.
    """
    await seed_subject(
        session,
        wanikani_id=101,
        object_type="kanji",
        level=3,
        characters="上",
        state=ItemState.KNOWN.value,
        srs_stage=9,
        meanings=[
            {"meaning": "Up", "primary": False, "accepted_answer": True},
            {"meaning": "Above", "primary": True, "accepted_answer": True},
            {"meaning": "Overhead (lit)", "primary": False, "accepted_answer": False},
        ],
        readings=[
            {"reading": "じょう", "primary": True, "accepted_answer": True, "type": "onyomi"},
            {"reading": "うえ", "primary": False, "accepted_answer": True, "type": "kunyomi"},
        ],
    )
    await seed_subject(
        session,
        wanikani_id=102,
        object_type="kanji",
        level=5,
        characters="山",
        state=ItemState.LEARNING.value,
        srs_stage=5,
        meanings=[{"meaning": "Mountain", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=103,
        object_type="kanji",
        level=1,
        characters="火",
        state=ItemState.LEARNING.value,
        srs_stage=3,
        meanings=[{"meaning": "Fire", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=104,
        object_type="kanji",
        level=1,
        characters="水",
        state=ItemState.SUSPENDED.value,
        srs_stage=9,
        meanings=[{"meaning": "Water", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=105,
        object_type="kanji",
        level=2,
        characters="木",
        state=ItemState.NEW.value,
        srs_stage=0,
        meanings=[{"meaning": "Tree", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=110,
        object_type="vocabulary",
        level=4,
        characters="元気",
        state=ItemState.KNOWN.value,
        srs_stage=9,
        meanings=[{"meaning": "Energy", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=111,
        object_type="vocabulary",
        level=3,
        characters="上",
        state=ItemState.LEARNING.value,
        srs_stage=2,
        meanings=[{"meaning": "Above (word)", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=112,
        object_type="kana_vocabulary",
        level=1,
        characters="すし",
        state=ItemState.LEARNING.value,
        srs_stage=6,
        meanings=[{"meaning": "Sushi", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=120,
        object_type="radical",
        level=1,
        characters="一",
        state=ItemState.LEARNING.value,
        srs_stage=5,
        meanings=[{"meaning": "Ground", "primary": True, "accepted_answer": True}],
    )
    await seed_subject(
        session,
        wanikani_id=121,
        object_type="radical",
        level=2,
        characters="口",
        state=ItemState.NEW.value,
        srs_stage=0,
        meanings=[{"meaning": "Mouth", "primary": True, "accepted_answer": True}],
    )


# --- the known rule, through the tools --------------------------------------


async def test_get_known_kanji_applies_the_known_rule(session):
    await seed_learner(session)
    from app.mcp_server import get_known_kanji

    items = await get_known_kanji()

    assert {item.characters for item in items} == {"上", "山"}
    by_char = {item.characters: item for item in items}
    assert by_char["上"].state == "known"
    assert by_char["上"].level == 3
    # Primary first, and the blacklisted ("accepted_answer": false) meaning
    # dropped entirely.
    assert [m.meaning for m in by_char["上"].meanings] == ["Above", "Up"]
    assert by_char["上"].meanings[0].primary is True
    assert [r.reading for r in by_char["上"].readings] == ["じょう", "うえ"]
    assert by_char["山"].srs_stage == 5


async def test_get_known_kanji_min_srs_stage_is_configurable(session):
    await seed_learner(session)
    from app.mcp_server import get_known_kanji

    # Below the default threshold (5): only the declared-known "上" survives
    # -- "山" at stage 5 and "火" at stage 3 both drop out under stage 6.
    assert {item.characters for item in await get_known_kanji(min_srs_stage=6)} == {"上"}
    # At stage 1, "火" (stage 3) now also counts, but suspended "水" (stage
    # 9) still never does.
    assert {item.characters for item in await get_known_kanji(min_srs_stage=1)} == {
        "上",
        "山",
        "火",
    }


async def test_get_known_vocabulary_includes_kana_vocabulary(session):
    await seed_learner(session)
    from app.mcp_server import get_known_vocabulary

    items = await get_known_vocabulary()

    assert {item.characters for item in items} == {"元気", "すし"}
    by_char = {item.characters: item for item in items}
    assert by_char["すし"].object_type == "kana_vocabulary"
    assert by_char["元気"].state == "known"


async def test_known_tools_never_return_radicals_even_though_they_can_be_known(session):
    await seed_learner(session)
    from app.mcp_server import get_known_kanji, get_known_vocabulary

    characters = {item.characters for item in await get_known_kanji()} | {
        item.characters for item in await get_known_vocabulary()
    }
    assert "一" not in characters


# --- the learning summary ----------------------------------------------------


async def test_get_learning_summary_counts_per_type_and_state(session):
    await seed_learner(session)
    from app.mcp_server import get_learning_summary

    summary = await get_learning_summary()

    # The lowest level with anything still new -- "木" and "口" at level 2 --
    # even though every other seeded item sits at level 1, 3, 4 or 5.
    assert summary.current_level == 2

    assert summary.counts["kanji"].new == 1
    assert summary.counts["kanji"].learning == 2
    assert summary.counts["kanji"].known == 1
    assert summary.counts["kanji"].suspended == 1

    assert summary.counts["vocabulary"].learning == 1
    assert summary.counts["vocabulary"].known == 1

    assert summary.counts["kana_vocabulary"].learning == 1
    assert summary.counts["kana_vocabulary"].new == 0

    assert summary.counts["radical"].new == 1
    assert summary.counts["radical"].learning == 1


async def test_get_learning_summary_with_nothing_imported_yet(session):
    from app.mcp_server import get_learning_summary

    summary = await get_learning_summary()

    assert summary.current_level is None
    assert summary.counts["kanji"].new == 0
    assert summary.counts["kanji"].learning == 0


# --- lookup -------------------------------------------------------------------


async def test_lookup_items_reports_found_and_not_found(session):
    await seed_learner(session)
    from app.mcp_server import lookup_items

    results = await lookup_items(["上", "存在しない", "元気"])
    by_query = {r.characters: r for r in results}

    assert [r.characters for r in results] == ["上", "存在しない", "元気"]

    up = by_query["上"]
    assert up.found is True
    assert len(up.matches) == 2
    by_type = {m.object_type: m for m in up.matches}
    assert by_type["kanji"].known is True
    assert by_type["kanji"].level == 3
    assert by_type["kanji"].primary_meaning == "Above"
    assert by_type["kanji"].primary_reading == "じょう"
    assert by_type["vocabulary"].known is False
    assert by_type["vocabulary"].srs_stage == 2

    missing = by_query["存在しない"]
    assert missing.found is False
    assert missing.matches == []

    genki = by_query["元気"]
    assert genki.found is True
    assert genki.matches[0].known is True


async def test_lookup_items_with_an_empty_list_returns_no_results(session):
    from app.mcp_server import lookup_items

    assert await lookup_items([]) == []


# --- the known rule's edge cases, at the unit level --------------------------
# (see tests/test_srs.py for `is_known` itself -- these two just confirm the
# MCP layer passes `min_srs_stage` through rather than hard-coding 5.)


async def test_lookup_items_known_flag_ignores_a_custom_threshold_elsewhere(session):
    """`lookup_items` takes no `min_srs_stage` -- it always uses the same
    default (Guru I) `get_known_kanji`/`get_known_vocabulary` default to,
    regardless of what a caller passed those other tools earlier."""
    await seed_subject(
        session,
        wanikani_id=200,
        object_type="kanji",
        level=1,
        characters="花",
        state=ItemState.LEARNING.value,
        srs_stage=4,
    )
    from app.mcp_server import lookup_items

    [result] = await lookup_items(["花"])
    assert result.matches[0].known is False


# --- the transport: the endpoint is exactly /mcp, and the Host is checked ----
#
# Both run inside one test, sharing one `async with mcp.session_manager.run()`
# block: that call starts an anyio task group bound to whichever task enters
# it, it can only be entered once per `FastMCP` instance (it raises on a
# second call), and the ASGI transport these tests drive the app through
# never sends lifespan events at all (only HTTP ones -- see conftest.py's
# `client` fixture docstring), so nothing else starts it. Splitting this
# across two tests sharing a fixture very nearly works, but pytest-asyncio
# runs a function-scoped fixture's teardown as a *different* task than the
# test body that used it, and anyio's cancel scopes refuse to close from a
# task other than the one that opened them -- so the fixture route fails with
# "Attempted to exit cancel scope in a different task than it was entered
# in" on teardown. Keeping entry and exit in the same coroutine avoids that.


async def test_mcp_transport_end_to_end():
    import app.main

    async with app.main.mcp.session_manager.run():
        # A disallowed Host header is rejected before any MCP method runs.
        bad_host_transport = httpx.ASGITransport(app=app.main.app)
        async with httpx.AsyncClient(
            transport=bad_host_transport, base_url="http://evil.example"
        ) as http:
            response = await http.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
        assert response.status_code == 421
        assert "Host" in response.text

        # A real MCP client, over HTTP against the ASGI app, with an
        # allowed Host -- initialize, tools/list, then tools/call -- proves
        # app/main.py's mount actually serves `/mcp` itself, not `/mcp/mcp`
        # or a redirect to it.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app.main.app),
            base_url="http://localhost",
            headers={"host": "localhost"},
        ) as mcp_http_client:
            async with streamable_http_client(
                "http://localhost/mcp", http_client=mcp_http_client
            ) as (read, write, _):
                async with ClientSession(read, write) as mcp_session:
                    await mcp_session.initialize()
                    tools = await mcp_session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    assert names == {
                        "get_known_kanji",
                        "get_known_vocabulary",
                        "get_learning_summary",
                        "lookup_items",
                    }

                    result = await mcp_session.call_tool("get_learning_summary", {})
                    assert result.isError is False
                    assert result.structuredContent is not None
                    assert result.structuredContent["current_level"] is None
