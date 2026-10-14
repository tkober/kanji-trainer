"""The placeholder-illustration cleanup migration (0006) -- a data migration,
so `test_migrations.py`'s `compare_metadata` guard cannot see it. Runs the
migration's own `upgrade()` directly, the same way `test_known_at_backfill.py`
does for 0005.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.db import Subject, SubjectIllustration

NOW = datetime.now(timezone.utc)

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0006_drop_placeholder_illustrations.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_0006_drop_placeholder_illustrations", _MIGRATION_PATH
    )
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


async def _seed_row(session, *, slug: str, alt: str | None, svg: str | None) -> int:
    subject = Subject(
        object_type="radical",
        level=1,
        slug=slug,
        characters="一",
        meanings=[{"meaning": "Ground", "primary": True, "accepted_answer": True}],
    )
    session.add(subject)
    await session.flush()
    subject_id = subject.id
    session.add(
        SubjectIllustration(
            subject_id=subject_id,
            source_url="https://files.wanikani.com/x" if svg else None,
            alt=alt,
            svg=svg,
            checked_at=NOW,
        )
    )
    await session.commit()
    return subject_id


async def test_migration_drops_only_rows_cached_from_a_placeholder(session):
    poisoned = await _seed_row(session, slug="a", alt="${mnemonicImageDescription}", svg=None)
    genuine_none = await _seed_row(session, slug="b", alt=None, svg=None)
    found = await _seed_row(session, slug="c", alt="The ground.", svg="<svg/>")

    await _run_upgrade(session)
    session.expunge_all()

    assert await session.get(SubjectIllustration, poisoned) is None
    assert await session.get(SubjectIllustration, genuine_none) is not None
    assert await session.get(SubjectIllustration, found) is not None
