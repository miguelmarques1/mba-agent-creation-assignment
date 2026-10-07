"""Aplicação FastAPI e entry point `aurora-api`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from aurora.api import health
from aurora.config import get_settings

logger = logging.getLogger("aurora")

MISSING_KEY_WARNING = "GOOGLE_API_KEY não configurada: as rotas de conversa vão falhar."


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not get_settings().google_api_key_configured:
        logger.warning(MISSING_KEY_WARNING)
    yield


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    logger.setLevel(logging.INFO)
    app = FastAPI(title="Residencial Aurora", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("aurora.main:app", host=settings.host, port=settings.port)
