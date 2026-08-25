"""FastAPI app factory.

Phase 5: mounts the coupons / merchants / health routers. DB access is via the
`api.deps.get_db` dependency, which tests override to point at SQLite.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.logging import get_logger
from api.routers import coupons, health, merchants

log = get_logger("api")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="CouponLive API", version="0.1.0")

    # The static frontend calls this API cross-origin; allow its origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.include_router(health.router)
    app.include_router(coupons.router)
    app.include_router(merchants.router)

    @app.get("/", tags=["meta"])
    def root() -> dict:
        return {"service": "couponlive", "environment": settings.environment.value}

    return app


app = create_app()
