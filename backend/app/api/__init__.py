"""HTTP routes, mounted under /api by :mod:`app.main`."""

from __future__ import annotations

from fastapi import APIRouter

from . import forecast, health, imports, items, settings, stats, study

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(settings.router, prefix="/settings", tags=["settings"])
router.include_router(imports.router, prefix="/import", tags=["import"])
router.include_router(study.router, tags=["study"])
router.include_router(items.router, prefix="/items", tags=["items"])
router.include_router(stats.router, prefix="/stats", tags=["stats"])
router.include_router(forecast.router, prefix="/forecast", tags=["forecast"])

__all__ = ["router"]
