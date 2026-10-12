"""The `known_at` backfill migration (0005) -- a data migration, not a
schema change, so `test_migrations.py`'s `compare_metadata` guard cannot see
it. Runs the migration's own `upgrade()` function directly (the module is
loaded straight off disk, since alembic version files are not an importable
package) against a seeded database, rather than duplicating its SQL here.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.db import ImportRun, Progress, Subject
from app.srs import ItemState

NOW = datetime.now(timezone.utc)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0005_backfill_known_at.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0005_backfill_known_at", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_upgrade(session) -> None:
    migration = _load_migration()
    connection = await session.connection()

    def run(sync_conn):
        context = MigrationContext.configure(sync_conn)
        with Operations.context(context):
            migration.upgrade()

    await connection.run_sync(run)
    await session.commit()


async def _seed_known_subject(session, *, wanikani_id: int, known_at: datetime | None) -> int:
    subject = Subject(
        wanikani_id=wanikani_id,
        object_type="kanji",
        level=1,
        slug=f"s{wanikani_id}",
        characters="上",
        meanings=[{"meaning": "Above", "primary": True, "accepted_answer": True}],
    )
    session.add(subject)
    await session.flush()
    session.add(
        Progress(subject_id=subject.id, state=ItemState.KNOWN.value, srs_stage=9, known_at=known_at)
    )
    await session.commit()
    return subject.id


async def test_migration_backfills_known_at_from_the_first_succeeded_import(session):
    finished_at = NOW - timedelta(days=60)
    session.add(
        ImportRun(
            status="succeeded",
            known_srs_stage=5,
            started_at=finished_at - timedelta(minutes=5),
            finished_at=finished_at,
        )
    )
    await session.commit()
    subject_id = await _seed_known_subject(session, wanikani_id=1, known_at=None)

    await _run_upgrade(session)

    progress = await session.get(Progress, subject_id)
    assert progress.known_at == finished_at


async def test_migration_leaves_an_existing_known_at_alone(session):
    finished_at = NOW - timedelta(days=60)
    own_known_at = NOW - timedelta(days=5)
    session.add(
        ImportRun(
            status="succeeded",
            known_srs_stage=5,
            started_at=finished_at - timedelta(minutes=5),
            finished_at=finished_at,
        )
    )
    await session.commit()
    subject_id = await _seed_known_subject(session, wanikani_id=1, known_at=own_known_at)

    await _run_upgrade(session)

    progress = await session.get(Progress, subject_id)
    assert progress.known_at == own_known_at


async def test_migration_is_a_noop_without_a_succeeded_import(session):
    """Only a `running`/`failed` run on record -- `upgrade()` must not treat
    either as "the first succeeded import"."""
    session.add(
        ImportRun(status="running", known_srs_stage=5, started_at=NOW - timedelta(days=1))
    )
    await session.commit()
    subject_id = await _seed_known_subject(session, wanikani_id=1, known_at=None)

    await _run_upgrade(session)

    progress = await session.get(Progress, subject_id)
    assert progress.known_at is None
