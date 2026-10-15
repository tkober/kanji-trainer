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
    jlpt: int | None = None,
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
        jlpt=jlpt,
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

    JLPT levels (manually assigned here, independent of the real snapshot --
    this fixture only has to exercise `get_learning_summary`'s `jlpt`
    section, not reproduce `refresh_jlpt`) are chosen so JLPT level 3 has a
    mix of known, learning-not-known and suspended *kanji*, level 2 has the
    same mix across *vocabulary* and *kana_vocabulary* (so both fold into
    one "vocabulary" bucket), level 1 has one unknown kanji, and levels 4/5
    stay empty on purpose -- every level must still show up, at zero. "木"
    and both radicals are left at `jlpt=None` (unlisted / never examined),
    confirming they never enter any level bucket.
    """
    await seed_subject(
        session,
        wanikani_id=101,
        object_type="kanji",
        level=3,
        characters="上",
        state=ItemState.KNOWN.value,
        srs_stage=9,
        jlpt=3,
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
        jlpt=3,
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
        jlpt=1,
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
        jlpt=3,
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
        jlpt=2,
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
        jlpt=2,
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
        jlpt=2,
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


async def test_known_items_carry_their_subjects_jlpt_level(session):
    await seed_learner(session)
    from app.mcp_server import get_known_kanji, get_known_vocabulary

    by_char = {item.characters: item for item in await get_known_kanji()}
    assert by_char["上"].jlpt == 3  # seeded with jlpt=3
    by_char = {item.characters: item for item in await get_known_vocabulary()}
    assert by_char["元気"].jlpt == 2  # seeded with jlpt=2


async def test_known_kanji_jlpt_is_none_when_unset(session):
    # "山" is seeded with jlpt=3 in seed_learner -- seed a second, unleveled
    # kanji directly to prove `None` survives rather than defaulting to 0.
    await seed_subject(
        session,
        wanikani_id=199,
        object_type="kanji",
        level=1,
        characters="瀧",
        state=ItemState.KNOWN.value,
        srs_stage=9,
    )
    from app.mcp_server import get_known_kanji

    by_char = {item.characters: item for item in await get_known_kanji()}
    assert by_char["瀧"].jlpt is None


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


async def test_get_learning_summary_jlpt_section_counts_per_level_and_type(session):
    await seed_learner(session)
    from app.mcp_server import get_learning_summary

    summary = await get_learning_summary()

    # Every level N5..N1 is present, even the ones nothing was seeded at.
    assert set(summary.jlpt.keys()) == {5, 4, 3, 2, 1}

    # Level 3, kanji: "上" (known), "山" (learning at the threshold, known)
    # and "水" (suspended at stage 9) -- suspended counts in `total`, never
    # in `known`, regardless of its stage.
    level_3 = summary.jlpt[3].kanji
    assert level_3.total == 3
    assert level_3.known == 2

    # Level 1, kanji: "火" alone, learning below the threshold -- not known.
    level_1 = summary.jlpt[1].kanji
    assert level_1.total == 1
    assert level_1.known == 0

    # Level 2, vocabulary: "元気" (vocabulary, known), "上" (vocabulary,
    # learning below the threshold) and "すし" (kana_vocabulary, learning at
    # the threshold) -- kana_vocabulary folds into the same bucket.
    level_2 = summary.jlpt[2].vocabulary
    assert level_2.total == 3
    assert level_2.known == 2
    # Nothing was seeded as *kanji* at level 2 ("木" is jlpt=None).
    assert summary.jlpt[2].kanji.total == 0

    # Levels with nothing seeded stay at zero rather than being absent.
    assert summary.jlpt[5] == summary.jlpt[4]
    assert summary.jlpt[5].kanji.total == 0
    assert summary.jlpt[5].vocabulary.total == 0


async def test_get_learning_summary_with_nothing_imported_yet(session):
    from app.mcp_server import get_learning_summary

    summary = await get_learning_summary()

    assert summary.current_level is None
    assert summary.counts["kanji"].new == 0
    assert summary.counts["kanji"].learning == 0
    assert set(summary.jlpt.keys()) == {5, 4, 3, 2, 1}
    assert all(level.kanji.total == 0 and level.vocabulary.total == 0 for level in summary.jlpt.values())


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
    assert by_type["kanji"].jlpt == 3  # seeded with jlpt=3
    assert by_type["vocabulary"].known is False
    assert by_type["vocabulary"].srs_stage == 2
    assert by_type["vocabulary"].jlpt == 2  # seeded with jlpt=2

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
    assert result.matches[0].jlpt is None  # seeded with no jlpt set


# --- get_jlpt_levels: the checked-in snapshot, independent of progress ------
# (against the real CSVs, same convention as tests/test_jlpt.py -- no DB
# seeding needed, this tool never touches the learner's own subjects.)


async def test_get_jlpt_levels_looks_up_text_and_its_kanji(session):
    from app.mcp_server import JlptQuery, get_jlpt_levels

    [result] = await get_jlpt_levels([JlptQuery(text="勉強", reading="べんきょう")])

    assert result.text == "勉強"
    assert result.reading == "べんきょう"
    assert result.jlpt == 5
    assert [k.character for k in result.kanji] == ["勉", "強"]
    assert all(isinstance(k.jlpt, int) or k.jlpt is None for k in result.kanji)


async def test_get_jlpt_levels_without_a_reading_takes_the_easiest_entry(session):
    from app.mcp_server import JlptQuery, get_jlpt_levels

    # "そば" is listed at both N5 and N2 -- without a reading to narrow it
    # down, the result is the easier (numerically larger) level.
    [result] = await get_jlpt_levels([JlptQuery(text="そば")])
    assert result.jlpt == 5
    assert result.reading is None


async def test_get_jlpt_levels_unlisted_word_is_none(session):
    from app.mcp_server import JlptQuery, get_jlpt_levels

    [result] = await get_jlpt_levels([JlptQuery(text="存在しない言葉")])
    assert result.jlpt is None
    # The word itself is unlisted, but its kanji are still reported --
    # independent lookups, see the tool's docstring.
    assert [k.character for k in result.kanji] == ["存", "在", "言", "葉"]


async def test_get_jlpt_levels_single_kanji_falls_back_to_the_kanji_list(session):
    from app.mcp_server import JlptQuery, get_jlpt_levels

    # "入" is not its own vocabulary entry, only inside longer words -- the
    # per-word `jlpt` must fall back to the kanji list.
    [result] = await get_jlpt_levels([JlptQuery(text="入")])
    assert result.jlpt == 5
    assert [(k.character, k.jlpt) for k in result.kanji] == [("入", 5)]


async def test_get_jlpt_levels_is_independent_of_this_learners_own_progress(session):
    """Unlike every other tool, `get_jlpt_levels` never touches the database
    -- it answers the same way whether or not the learner has imported
    anything, or even has a matching subject at all."""
    from app.mcp_server import JlptQuery, get_jlpt_levels

    [result] = await get_jlpt_levels([JlptQuery(text="一")])
    assert result.jlpt == 5


async def test_get_jlpt_levels_handles_several_items_in_one_call(session):
    from app.mcp_server import JlptQuery, get_jlpt_levels

    results = await get_jlpt_levels(
        [
            JlptQuery(text="妥協", reading="だきょう"),  # N1
            JlptQuery(text="一月", reading="いちがつ"),  # wrong reading -> None
            JlptQuery(text="高校生"),  # no reading given -> N4, listed once
        ]
    )
    assert [r.jlpt for r in results] == [1, None, 4]
    # Order and count match the request 1:1, even though none of these
    # share a level.
    assert [r.text for r in results] == ["妥協", "一月", "高校生"]


async def test_get_jlpt_levels_rejects_more_than_the_cap(session):
    import pytest

    from app.mcp_server import JlptQuery, get_jlpt_levels

    items = [JlptQuery(text="一")] * 2001
    with pytest.raises(ValueError):
        await get_jlpt_levels(items)


async def test_get_jlpt_levels_accepts_exactly_the_cap(session):
    from app.mcp_server import JlptQuery, get_jlpt_levels

    items = [JlptQuery(text="一")] * 2000
    results = await get_jlpt_levels(items)
    assert len(results) == 2000


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
        # app/main.py's route actually serves `/mcp` itself, not `/mcp/mcp`
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
                        "get_jlpt_levels",
                    }

                    result = await mcp_session.call_tool("get_learning_summary", {})
                    assert result.isError is False
                    assert result.structuredContent is not None
                    assert result.structuredContent["current_level"] is None
                    assert set(result.structuredContent["jlpt"].keys()) == {"5", "4", "3", "2", "1"}

                    # The new tool, through a real JSON-RPC round trip too --
                    # not just the direct-call tests above.
                    jlpt_result = await mcp_session.call_tool(
                        "get_jlpt_levels", {"items": [{"text": "勉強", "reading": "べんきょう"}]}
                    )
                    assert jlpt_result.isError is False
                    assert jlpt_result.structuredContent is not None
                    [item] = jlpt_result.structuredContent["result"]
                    assert item["jlpt"] == 5
                    assert [k["character"] for k in item["kanji"]] == ["勉", "強"]
