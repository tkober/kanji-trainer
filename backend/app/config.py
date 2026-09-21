"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    """Runtime configuration for the backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- WaniKani ---
    # Only ever used for the import. A read-only personal access token is
    # enough and is what the Settings screen asks for: nothing here writes
    # back to WaniKani, and the trainer's own SRS is the source of truth
    # afterwards.
    wanikani_api_token: str = ""
    wanikani_api_base: str = "https://api.wanikani.com/v2"
    # Revision pin. WaniKani promises not to break a revision, so an
    # unattended import cannot be surprised by a schema change.
    wanikani_revision: str = "20170710"
    # SRS stage 5 is "Guru I". Everything at or above it is imported as
    # already known, which is the whole point of the import -- see
    # importer.py. Overridable per import run from the UI.
    wanikani_known_srs_stage: int = 5

    # --- SRS ---
    # Hours until the review that *enters* each stage. Index 0 is unused (an
    # item at stage 0 has not been learned yet); index 9 is burned and never
    # scheduled. Defaults match WaniKani so the pacing feels familiar, but
    # unlike WaniKani they are configuration rather than doctrine.
    srs_interval_hours: str = "0,4,8,24,48,168,336,720,2880,0"
    # Where "das kann ich" puts an item. 9 retires it outright; 8 leaves one
    # four-month check-up before it burns, which is the safer default for
    # someone who is *fairly* sure.
    known_srs_stage: int = 9
    # Reviews scheduled within this many minutes are served as due. WaniKani
    # rounds down to the hour, which means a 4h item is answerable after
    # ~3h01m; without something similar a session that runs long keeps
    # dangling items just out of reach.
    review_grace_minutes: int = 59
    # Items per lesson batch, read together and then quizzed together.
    # Five is WaniKani's default and is about as much as can be held in
    # mind between reading a mnemonic and being asked to produce it.
    lesson_batch_size: int = 5
    # Hold an answer that is about to be marked wrong and ask once whether it
    # was meant that way. A typo otherwise costs exactly as much as not
    # knowing the item -- and at three characters there is no typo tolerance
    # at all, so "en" for "end" is simply a demotion.
    soft_answer_enabled: bool = True

    # --- Answer checking ---
    # Allowed Levenshtein distance for a meaning, per this many characters of
    # the expected answer. WaniKani's own rule of thumb; it lets "vegtable"
    # through for "vegetable" without accepting "water" for "wafer".
    meaning_typo_tolerance_divisor: int = 7

    # --- Database ---
    # DB_URL carries only host/port/database; credentials come per role, the
    # same split the other stacks on postgres-core use. The owner role runs
    # DDL at startup, the app role serves every request.
    #
    # A `sqlite://` URL switches the whole thing to a local file instead --
    # for a machine with no Postgres to point at. The roles are then ignored:
    # SQLite's access control is the filesystem's.
    db_url: str = "postgresql://localhost:5432/kanji_trainer"
    db_user: str = "kanji_trainer_app"
    db_password: str = ""
    db_owner_user: str = "kanji_trainer_owner"
    db_owner_password: str = ""
    # postgres-core lives in its own compose stack, so `depends_on` cannot
    # order this one after it. On a host reboot both come up at once and the
    # database may not accept connections for a while yet.
    db_connect_attempts: int = 30
    db_connect_delay_seconds: float = 2.0

    # --- Server ---
    cors_origins: str = "http://localhost:4200"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def srs_intervals(self) -> tuple[int, ...]:
        """The interval table as numbers, padded/truncated to ten stages.

        Parsed rather than validated away: a malformed value should fall back
        to something that still schedules reviews, not keep the app from
        starting with the one screen that could fix it.
        """
        try:
            values = [int(part) for part in self.srs_interval_hours.split(",")]
        except ValueError:
            values = []
        if len(values) < 10:
            values += _DEFAULT_INTERVALS[len(values) :]
        return tuple(values[:10])

    @property
    def uses_sqlite(self) -> bool:
        """True when DB_URL points at a file rather than at a Postgres server."""
        return make_url(self.db_url).get_backend_name() == "sqlite"

    @property
    def sqlite_path(self) -> Path | None:
        """Where the SQLite file lives, or None when Postgres is configured.

        None also covers the in-memory forms (``sqlite://`` and
        ``sqlite:///:memory:``) -- there is no file to create a directory for.
        """
        if not self.uses_sqlite:
            return None
        database = make_url(self.db_url).database
        if not database or database == ":memory:":
            return None
        return Path(database)

    def _role_url(self, user: str, password: str) -> URL:
        """Build an async SQLAlchemy URL for one role from the base DB_URL.

        SQLite has no roles, so both roles resolve to the same file: the
        owner/app split is a Postgres privilege boundary, and there is nothing
        on the SQLite side to enforce it with. What the split protects against
        -- a request path quietly issuing DDL -- is still caught by the tests,
        which run the Postgres roles for real.
        """
        url = make_url(self.db_url)
        if url.get_backend_name() == "sqlite":
            return url.set(drivername="sqlite+aiosqlite")
        return url.set(
            drivername="postgresql+asyncpg",
            username=user or None,
            password=password or None,
        )

    @property
    def app_database_url(self) -> URL:
        """The role serving requests -- CRUD only, no DDL."""
        return self._role_url(self.db_user, self.db_password)

    @property
    def owner_database_url(self) -> URL:
        """The role used at startup for DDL and seeding."""
        return self._role_url(self.db_owner_user, self.db_owner_password)


_DEFAULT_INTERVALS = (0, 4, 8, 24, 48, 168, 336, 720, 2880, 0)


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings singleton."""
    return Settings()
