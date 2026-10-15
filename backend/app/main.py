"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import get_settings
from .db import init_db
from .mcp_server import mcp, mcp_app

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    # The MCP session manager (issue #75) owns the task group that every
    # in-flight Streamable HTTP request runs under, and has to be running
    # for exactly as long as the app is. Only `mcp_app`'s route is used
    # below, never its own lifespan, so it is driven from here instead,
    # nested with the rest of startup.
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Kanji Trainer", version="0.1.0", lifespan=lifespan)

# Only needed for `ng serve`, which runs on a different origin. In the
# deployment the SPA and the API are same-origin behind nginx, so nothing here
# applies -- see the frontend's nginx.conf.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# Read-only MCP server (issue #75). `mcp_app` is a Starlette app with a single
# route at "/mcp" (`streamable_http_path` in app/mcp_server.py) and no
# middleware of its own, so its route is added to this app's router directly.
# A mount would not do: under a "/mcp" prefix the route becomes "/mcp/mcp" (or
# "/mcp" redirects to "/mcp/"), and a mount at "/" catches every unmatched
# path, turning FastAPI's JSON 404 into the sub-app's plain-text one.
# DNS-rebinding protection (allowed Host headers) lives in
# app/mcp_server.py's `transport_security` setting.
app.router.routes.extend(mcp_app.routes)
