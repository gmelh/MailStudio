"""Application factory."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from . import __version__, errors, public, signup, web
from .accounts import LoginThrottle
from .api import assets, auth, campaigns, contacts, lists, sending, setup, suppressions, system, users
from .api import settings as settings_api
from .config import Settings
from .db import migrate
from .providers import configured_provider
from .worker import Worker

log = logging.getLogger("mailstudio")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        applied = migrate(settings)
        if applied:
            log.info("Applied migrations: %s", ", ".join(applied))
        stop = threading.Event()
        thread = None
        if settings.run_worker:
            worker = Worker(settings, configured_provider)
            thread = threading.Thread(target=worker.run, args=(stop,), name="mailstudio-worker", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=30)

    app = FastAPI(
        title="Mail Studio API",
        version=__version__,
        summary="Self-hosted email campaigns without the marketing machinery.",
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.login_throttle = LoginThrottle()
    errors.install(app)

    v1 = APIRouter(prefix="/api/v1")
    for module in (
        system,
        auth,
        users,
        settings_api,
        sending,
        contacts,
        lists,
        suppressions,
        campaigns,
        assets,
        setup,
    ):
        v1.include_router(module.router)
    app.include_router(v1)
    app.include_router(public.router)
    app.include_router(signup.router)
    app.include_router(web.router)  # last: it answers every other GET with the web app
    return app
