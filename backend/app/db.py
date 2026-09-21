"""Persistence: ORM models, engines and schema management.

Single-user application, so ``app_settings`` holds exactly one row (id = 1).

Postgres is the deployment target. Two roles are used (see :mod:`app.config`):
the *owner* role runs DDL at startup, the *app* role serves every request. The
app role's access to the owner-created tables comes from server-side
``ALTER DEFAULT PRIVILEGES`` (see the bootstrap SQL in the deployment stack),
so no GRANT is issued here.

A ``sqlite://`` DB_URL runs the same schema out of a local file instead, for a
machine that has no Postgres to point at. Everything that differs between the
two backends is collected here rather than sprinkled through the request paths:
the column types (:data:`JSONColumn`, :class:`UtcDateTime`), the upsert
(:func:`_upsert`), the schema migration and the connection setup. Nothing above
this module needs to know which one is in use.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    TypeDecorator,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import get_settings

log = logging.getLogger(__name__)

SETTINGS_ROW_ID = 1

# Errors that will never resolve by waiting: the roles, the password or the
# database itself are wrong, so retrying only delays a clear failure.
FATAL_SQLSTATES = {
    # Postgres deliberately reports a missing role and a wrong password with
    # the same code, so that an attacker cannot enumerate users. The message
    # has to name both causes -- claiming only "wrong password" sends someone
    # looking for a role that was never created.
    "28P01": (
        "authentication as {user} failed. Either the role does not exist, or its "
        "password differs from DB_OWNER_PASSWORD -- Postgres reports both the "
        "same way. Check with dbeaver/verify.sql; if the roles are missing, run "
        "dbeaver/create_users_and_db.sql and grant_privileges.sql"
    ),
    "28000": (
        "role {user} does not exist. Run dbeaver/create_users_and_db.sql "
        "followed by dbeaver/grant_privileges.sql"
    ),
    "3D000": (
        "database {database} does not exist. Run dbeaver/create_users_and_db.sql "
        "followed by dbeaver/grant_privileges.sql"
    ),
}


class DatabaseUnavailable(RuntimeError):
    """The database could not be used, with a reason worth reading."""


def _fatal_reason(exc: BaseException) -> str | None:
    """Return a human explanation when the error cannot be fixed by waiting."""
    seen: list[BaseException | None] = [exc, getattr(exc, "orig", None), exc.__cause__]
    for candidate in seen:
        code = getattr(candidate, "sqlstate", None)
        if code in FATAL_SQLSTATES:
            settings = get_settings()
            return FATAL_SQLSTATES[code].format(
                user=settings.db_owner_user, database=make_url(settings.db_url).database
            )
    return None


# --- portable column types -------------------------------------------------

# JSONB where it exists, JSON where it does not. Declared this way round so a
# Postgres deployment gets JSONB and the SQLite one still runs the same DDL.
JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class UtcDateTime(TypeDecorator):
    """A timestamp that is always timezone-aware UTC, on both backends.

    SQLite has no timestamp type: ``DateTime(timezone=True)`` writes an ISO
    string without an offset and reads a *naive* datetime back. FastAPI then
    serialises it without a zone, and the browser reads it as local time -- a
    review due at 18:00 UTC would show as due at 18:00 local and the queue
    would look wrong by the UTC offset. Attaching UTC on the way out fixes
    that; ``CURRENT_TIMESTAMP`` is UTC in SQLite, so the assumption holds for
    server-side defaults too.

    On Postgres the value already arrives aware and only gets normalised.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    """User-editable configuration, exactly one row.

    Every column is nullable: a NULL means "not configured here", and the
    corresponding environment variable is used instead. That keeps a fresh
    deployment working from its .env alone while letting the Settings screen
    override anything without a redeploy.
    """

    __tablename__ = "app_settings"
    __table_args__ = (CheckConstraint("id = 1", name="app_settings_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    wanikani_api_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The threshold the *next* import uses to decide what counts as known.
    wanikani_known_srs_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Where "I know this" parks an item.
    known_srs_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    srs_interval_hours: Mapped[str | None] = mapped_column(String, nullable=True)
    # 0 means unlimited. NULL means "not set here" -- the two are different,
    # which is why this is not simply 0-as-unset.
    daily_lesson_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lesson_batch_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    soft_answer_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )


class Subject(Base):
    """One thing to learn: a radical, a kanji or a vocabulary word.

    ``wanikani_id`` is the import key and is unique where set, but it is *not*
    the primary key: the trainer is meant to outlive the WaniKani
    subscription that seeded it, and hand-added items have no WaniKani id to
    take one from. The importer maps WaniKani ids onto these local ids in a
    second pass, so ``component_subject_ids`` always holds local ids.
    """

    __tablename__ = "subjects"
    __table_args__ = (
        Index("idx_subjects_wanikani", "wanikani_id", unique=True),
        Index("idx_subjects_order", "sort_order", "id"),
        Index("idx_subjects_level_type", "level", "object_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wanikani_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    object_type: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    slug: Mapped[str] = mapped_column(String, nullable=False, server_default="")

    # NULL for the handful of radicals WaniKani has no character for; those
    # carry an image instead and the UI falls back to it.
    characters: Mapped[str | None] = mapped_column(String, nullable=True)
    character_image_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # [{"meaning": "water", "primary": true, "accepted_answer": true}, ...]
    meanings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONColumn, nullable=False, server_default="[]"
    )
    # [{"meaning": "...", "type": "whitelist"|"blacklist"}, ...] -- the
    # blacklist entries are why this is stored rather than folded into
    # `meanings`: they are answers that must be *rejected*.
    auxiliary_meanings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONColumn, nullable=False, server_default="[]"
    )
    # [{"reading": "すい", "primary": true, "accepted_answer": true,
    #   "type": "onyomi"}, ...] -- empty for radicals.
    readings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONColumn, nullable=False, server_default="[]"
    )
    # Local subject ids, resolved by the importer.
    component_subject_ids: Mapped[list[int]] = mapped_column(
        JSONColumn, nullable=False, server_default="[]"
    )
    parts_of_speech: Mapped[list[str]] = mapped_column(
        JSONColumn, nullable=False, server_default="[]"
    )

    meaning_mnemonic: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    meaning_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    reading_mnemonic: Mapped[str | None] = mapped_column(Text, nullable=True)
    reading_hint: Mapped[str | None] = mapped_column(Text, nullable=True)

    # WaniKani's teaching order, preserved so lessons can follow it.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )


class Progress(Base):
    """What the learner has done with one subject. One row per subject.

    ``state`` records intent and ``srs_stage`` records position, which is why
    both exist: an item at stage 9 reached it either by being answered
    correctly eight times or by the learner saying "I know this", and the
    statistics are worth nothing if those look the same.

    ``pending_meaning`` / ``pending_reading`` are NULL while no review is in
    flight and hold the outstanding questions once one is. They are persisted
    rather than kept in the session so that closing the tab mid-review does
    not hand back a free pass on the half of the item already answered.
    """

    __tablename__ = "progress"
    __table_args__ = (
        Index("idx_progress_due", "next_review_at"),
        Index("idx_progress_state", "state"),
    )

    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )

    state: Mapped[str] = mapped_column(String, nullable=False, server_default="new")
    srs_stage: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_review_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    pending_meaning: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pending_reading: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Wrong answers accumulated within the review currently in flight. The
    # stage penalty is computed from this when the item completes, so two
    # slips on the same item cost more than one -- WaniKani's rule, in srs.py.
    session_incorrect: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    incorrect_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Times the item fell back out of Guru after having reached it.
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    passed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    burned_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    # When the learner declared it known, or the import did on their behalf.
    known_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )


class ReviewLog(Base):
    """One answered question. Append-only; the statistics read from here.

    Kept per *question* rather than per item so that "I keep failing readings,
    not meanings" is answerable -- which is the diagnosis that decides whether
    an item needs re-learning or just more exposure.
    """

    __tablename__ = "review_log"
    __table_args__ = (
        Index("idx_review_log_answered", "answered_at"),
        Index("idx_review_log_subject", "subject_id", "answered_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    question_type: Mapped[str] = mapped_column(String, nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    given_answer: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    srs_stage_before: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    srs_stage_after: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    answered_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )


class ImportRun(Base):
    """One WaniKani import, kept so the dashboard can explain the numbers.

    The counts are the interesting part: "3,412 taken over as known"
    is the sentence that tells the learner the import did what it promised.
    """

    __tablename__ = "import_runs"
    __table_args__ = (Index("idx_import_runs_started", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="running")
    # The threshold this run used, recorded because changing it and importing
    # again is the expected way to correct a mapping that felt wrong.
    known_srs_stage: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")

    subjects_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    subjects_imported: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    assignments_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    marked_known: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    marked_learning: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    marked_new: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    started_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


# --- engines ---------------------------------------------------------------

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _new_engine(url: URL) -> AsyncEngine:
    """Create an engine, with SQLite's per-connection setup attached."""
    engine = create_async_engine(url, future=True)
    if url.get_backend_name() == "sqlite":
        event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine


def _configure_sqlite_connection(connection: Any, _record: Any) -> None:
    """The three PRAGMAs a SQLite file needs to behave like the Postgres one.

    * ``foreign_keys`` is off by default, and without it the ``ON DELETE``
      clauses on ``progress`` and ``review_log`` are silently ignored --
      deleting a subject would leave progress rows pointing at nothing.
    * ``journal_mode=WAL`` lets a read run while a write is in flight, which
      the default rollback journal does not. An import writes for minutes.
    * ``busy_timeout`` turns the remaining overlaps into a short wait instead
      of an immediate "database is locked".
    """
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _upsert(table: type[Base] | Table) -> Any:
    """``INSERT .. ON CONFLICT``, from whichever dialect is in use.

    Both dialects offer it with the same arguments, but the constructs come
    from different modules and neither accepts the other's.
    """
    if get_settings().uses_sqlite:
        return sqlite_insert(table)
    return postgresql_insert(table)


def get_engine() -> AsyncEngine:
    """The request-time engine (app role), created on first use."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = _new_engine(get_settings().app_database_url)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def reset_engines() -> None:
    """Drop the cached engine so the next use re-reads the configuration.

    Production never needs this; the tests do, because they point the process
    at a throwaway database between cases.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one app-role session per request."""
    async with get_sessionmaker()() as session:
        yield session


# --- schema ----------------------------------------------------------------


async def init_db() -> None:
    """Create the schema and ensure the settings row (run on startup).

    DDL requires the owner role, so this opens a short-lived owner connection.
    """
    settings = get_settings()
    _prepare_sqlite_directory(settings.sqlite_path)
    owner_engine = _new_engine(settings.owner_database_url)
    try:
        await _wait_for_database(owner_engine)
        async with owner_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await migrate_schema(conn)
        async with async_sessionmaker(owner_engine, expire_on_commit=False)() as session:
            await ensure_settings_row(session)
            await session.commit()
    finally:
        await owner_engine.dispose()


def _prepare_sqlite_directory(path: Any) -> None:
    """Create the directory the SQLite file lives in, if it is missing.

    SQLite will create the *file* but not the folder above it, and reports the
    missing folder as "unable to open database file" -- which reads like a
    permission problem and sends you looking in the wrong place.
    """
    if path is None or path.parent == path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)


async def _wait_for_database(engine: AsyncEngine) -> None:
    """Block until the database accepts a connection, or fail with a reason.

    Waiting is right for a database that is merely not up yet, and wrong for
    one that will never let us in: a bad password or a missing role stays bad,
    so those raise immediately with an explanation instead of a stack trace
    repeated once per restart.
    """
    settings = get_settings()

    if settings.uses_sqlite:
        # Nothing to wait for: a local file is either openable now or it never
        # will be. Retrying a read-only directory for a minute helps nobody.
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - re-raised with the path named
            location = settings.sqlite_path or ":memory:"
            reason = f"the SQLite database at {location} could not be opened: {exc}"
            log.error("Cannot use the database: %s.", reason)
            raise DatabaseUnavailable(reason) from None
        return

    attempts = max(1, settings.db_connect_attempts)

    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if attempt > 1:
                log.info("Database reachable after %d attempts", attempt)
            return
        except Exception as exc:  # noqa: BLE001 - re-raised below unless retryable
            reason = _fatal_reason(exc)
            if reason is not None:
                log.error("Cannot use the database: %s.", reason)
                # `from None`: the driver traceback adds nothing to a message
                # that already says exactly what to change.
                raise DatabaseUnavailable(reason) from None

            if attempt == attempts:
                log.error(
                    "Database still unreachable after %d attempts (%.0fs)",
                    attempts,
                    attempts * settings.db_connect_delay_seconds,
                )
                raise

            log.warning(
                "Database not reachable yet (attempt %d/%d): %s", attempt, attempts, exc
            )
            await asyncio.sleep(settings.db_connect_delay_seconds)


# Columns added after their table first shipped. Append-only: an existing
# database carries real review history, so a line here is never edited or
# removed, only added to.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("app_settings", "lesson_batch_size", "INTEGER"),
    ("app_settings", "soft_answer_enabled", "BOOLEAN"),
)


async def migrate_schema(conn: AsyncConnection) -> None:
    """Add columns that ``create_all`` cannot: it only creates missing *tables*.

    Idempotence comes from asking which columns exist rather than from
    ``ADD COLUMN IF NOT EXISTS``, which SQLite does not have. Reflecting first
    works the same on both backends and reads as what it is.
    """
    if not ADDED_COLUMNS:
        return

    existing = await conn.run_sync(_existing_columns, {table for table, _, _ in ADDED_COLUMNS})

    for table, column, definition in ADDED_COLUMNS:
        if column in existing[table]:
            continue
        log.info("Adding missing column %s.%s", table, column)
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def _existing_columns(sync_conn: Any, tables: set[str]) -> dict[str, set[str]]:
    """Which columns each table currently has (runs on a sync connection)."""
    inspector = inspect(sync_conn)
    return {
        table: {column["name"] for column in inspector.get_columns(table)} for table in tables
    }


async def ensure_settings_row(session: AsyncSession) -> None:
    await session.execute(
        _upsert(AppSettings)
        .values(id=SETTINGS_ROW_ID)
        .on_conflict_do_nothing(index_elements=[AppSettings.id])
    )


async def load_settings(session: AsyncSession) -> AppSettings | None:
    """Read the single settings row, if it exists yet."""
    return await session.scalar(select(AppSettings).where(AppSettings.id == SETTINGS_ROW_ID))
