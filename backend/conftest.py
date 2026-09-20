"""Test fixtures.

Postgres via testcontainers is the default, reproducing the owner/app role
split so that a stray DDL statement in a request path fails here rather than
at deploy time. ``TEST_DB=sqlite`` points the same suite at a temporary file
for a machine without Docker.

HTTP tests go through ``httpx.ASGITransport`` rather than ``TestClient``: the
latter runs the app on its own event loop in a worker thread, which the shared
SQLAlchemy engine cannot be used from.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

DB_NAME = "kanji_trainer_test"
OWNER = "kanji_trainer_owner"
APP = "kanji_trainer_app"
PASSWORD = "testing"


@pytest.fixture(scope="session", autouse=True)
def database_url() -> Iterator[str]:
    """Point the process at a throwaway database for the whole run."""
    if os.environ.get("TEST_DB") == "sqlite":
        with tempfile.TemporaryDirectory() as directory:
            url = f"sqlite:///{Path(directory) / 'test.db'}"
            _apply_env(url, sqlite=True)
            yield url
        return

    try:
        # Moved in testcontainers 4.14; the old path still works but warns.
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # pragma: no cover - older testcontainers
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:17", dbname=DB_NAME) as container:
        _bootstrap_roles(container)
        url = (
            f"postgresql://{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(5432)}/{DB_NAME}"
        )
        _apply_env(url, sqlite=False)
        yield url


def _apply_env(url: str, *, sqlite: bool) -> None:
    os.environ["DB_URL"] = url
    os.environ["DB_OWNER_USER"] = "" if sqlite else OWNER
    os.environ["DB_OWNER_PASSWORD"] = "" if sqlite else PASSWORD
    os.environ["DB_USER"] = "" if sqlite else APP
    os.environ["DB_PASSWORD"] = "" if sqlite else PASSWORD
    os.environ["WANIKANI_API_TOKEN"] = ""

    from app.config import get_settings

    get_settings.cache_clear()


def _bootstrap_roles(container: object) -> None:
    """Create the two roles the app expects, as the deployment's SQL does."""
    import psycopg

    dsn = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"CREATE ROLE {OWNER} WITH LOGIN PASSWORD '{PASSWORD}'")
        conn.execute(f"CREATE ROLE {APP} WITH LOGIN PASSWORD '{PASSWORD}'")
        conn.execute(f"GRANT CONNECT ON DATABASE {DB_NAME} TO {APP}")
        conn.execute(f"GRANT CREATE, USAGE ON SCHEMA public TO {OWNER}")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {APP}")
        # The app role's table access comes from the owner's default
        # privileges, exactly as the deployment's grant_privileges.sql sets it
        # up -- no GRANT is ever issued from application code.
        conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP}"
        )
        conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {APP}"
        )


@pytest_asyncio.fixture(autouse=True)
async def fresh_schema(database_url: str) -> AsyncIterator[None]:
    """Drop and recreate the schema around every test."""
    from app.config import get_settings
    from app.db import Base, _new_engine, init_db, reset_engines

    await reset_engines()
    engine = _new_engine(get_settings().owner_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

    await init_db()
    yield
    await reset_engines()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[object]:
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def client() -> AsyncIterator[object]:
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
