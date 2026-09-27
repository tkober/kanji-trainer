"""Alembic environment.

Two callers, two ways of getting a connection:

* the app, at startup (`db.init_db`) -- it already holds an open owner-role
  connection (async, on the app's own event loop) and hands it in via
  `config.attributes["connection"]`. Migrations then run *on that connection*,
  inside the transaction `init_db` already began, rather than opening a second
  one.
* the CLI (`uv run alembic revision --autogenerate`, `uv run alembic upgrade
  head`) -- nothing is passed in, so this builds its own engine from the
  owner URL. The app's URLs are async (asyncpg / aiosqlite); Alembic's
  migration runner is synchronous, so the engine is async but every access
  goes through `AsyncConnection.run_sync`, exactly like `init_db` itself does.

`target_metadata = Base.metadata` is what makes `--autogenerate` and
`tests/test_migrations.py`'s `compare_metadata` check possible: both diff the
live schema against the same declarative models this app runs on.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db import Base, _new_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _render_kwargs(dialect_name: str) -> dict:
    """SQLite can't ALTER most things, so batch mode rewrites the table instead."""
    return {"render_as_batch": True} if dialect_name == "sqlite" else {}


def run_migrations_offline() -> None:
    url = str(get_settings().owner_database_url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_render_kwargs(get_settings().owner_database_url.get_backend_name()),
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **_render_kwargs(connection.dialect.name),
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """No connection was handed in (the CLI path) -- build our own engine."""
    engine: AsyncEngine = _new_engine(get_settings().owner_database_url)
    try:
        async with engine.connect() as async_connection:
            await async_connection.run_sync(_run_with_connection)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif config.attributes.get("connection") is not None:
    # Called from `init_db` via `conn.run_sync(...)`: this runs inside
    # `run_sync`'s worker thread, on a plain (sync) `Connection`, already
    # inside the transaction `init_db` began -- no event loop to run here, and
    # no second connection to open.
    _run_with_connection(config.attributes["connection"])
else:
    # The CLI: `uv run alembic upgrade head` etc. has no connection to reuse.
    asyncio.run(run_migrations_online())
