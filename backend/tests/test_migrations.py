"""The Alembic guard: a model change without a matching revision fails here.

Two things are worth proving, and neither is covered by the rest of the
suite, which only ever sees a database `init_db` has already brought to head:

* the migrations, run from nothing, produce exactly what `Base.metadata`
  describes -- `compare_metadata` is the same check `alembic revision
  --autogenerate` uses to decide whether there is anything left to generate.
* a database that predates Alembic entirely (every real deployment, the live
  one included) is bridged rather than left behind: the legacy
  `migrate_schema()` completes it to the 0001 baseline, it gets stamped, and
  `upgrade head` takes it the rest of the way -- with its data untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db import Base, Progress, Subject, _alembic_config, _new_engine, init_db
from app.srs import ItemState

NOW = datetime.now(timezone.utc)


def _compare(sync_conn) -> list:
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    context = MigrationContext.configure(sync_conn)
    return compare_metadata(context, Base.metadata)


def _head_revision() -> str | None:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


async def test_migrated_schema_matches_the_models() -> None:
    """`fresh_schema` already ran `init_db`, so this is the schema every test
    (and every real deployment) actually runs against."""
    engine = _new_engine(get_settings().owner_database_url)
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(_compare)
    finally:
        await engine.dispose()

    assert diffs == [], (
        "the schema Alembic produces differs from Base.metadata -- add a "
        f"revision for it: {diffs}"
    )


async def test_legacy_database_is_bridged_without_losing_data() -> None:
    """A database created the old way -- `create_all`, no Alembic, possibly
    missing a column `ADDED_COLUMNS` would have added -- gets finished to the
    baseline and migrated the rest of the way, and the row already in it
    survives.
    """
    owner_engine = _new_engine(get_settings().owner_database_url)
    try:
        await _reset_to_legacy_schema(owner_engine)
        subject_id = await _seed_legacy_row(owner_engine)
    finally:
        await owner_engine.dispose()

    await init_db()

    engine = _new_engine(get_settings().owner_database_url)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
            columns = await conn.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns("app_settings")}
            )
            version = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
    finally:
        await engine.dispose()

    assert "subject_illustrations" in tables
    assert "lesson_batch_size" in columns, "migrate_schema should have added the missing column"
    assert version == _head_revision()

    from app.db import get_sessionmaker, reset_engines

    await reset_engines()
    async with get_sessionmaker()() as session:
        subject = await session.get(Subject, subject_id)
        progress = await session.get(Progress, subject_id)
        assert subject is not None and subject.slug == "ground"
        assert progress is not None and progress.srs_stage == 3


async def _reset_to_legacy_schema(owner_engine: AsyncEngine) -> None:
    """Drop everything `fresh_schema` set up, and recreate the schema as it
    would have looked right before Alembic existed: no `subject_illustrations`
    table, no `alembic_version`, and one `ADDED_COLUMNS` entry missing.
    """
    async with owner_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

    legacy_metadata = MetaData()
    for table in Base.metadata.tables.values():
        if table.name != "subject_illustrations":
            table.to_metadata(legacy_metadata)

    async with owner_engine.begin() as conn:
        await conn.run_sync(legacy_metadata.create_all)
        # `create_all` has no notion of "one column short" -- the column is
        # created like every other and then dropped, so the legacy database
        # ends up missing exactly the one `ADDED_COLUMNS` entry it predates.
        # Both backends support `DROP COLUMN` (SQLite only since 3.35, which
        # is old enough that this needing a fallback is not worth it here).
        await conn.execute(text("ALTER TABLE app_settings DROP COLUMN lesson_batch_size"))


async def _seed_legacy_row(owner_engine: AsyncEngine) -> int:
    """One subject + progress row, inserted directly against the legacy
    tables (they are unmodified clones, so the ORM models still fit)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
        subject = Subject(
            wanikani_id=1,
            object_type="radical",
            level=1,
            slug="ground",
            characters="一",
            meanings=[{"meaning": "Ground", "primary": True, "accepted_answer": True}],
            meaning_mnemonic="The ground.",
        )
        session.add(subject)
        await session.flush()
        session.add(
            Progress(subject_id=subject.id, state=ItemState.LEARNING.value, srs_stage=3)
        )
        await session.commit()
        return subject.id
