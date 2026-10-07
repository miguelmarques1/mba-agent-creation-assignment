"""Aplicação FastAPI e entry point `aurora-api`."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from aurora.api import health, sessoes, verificacao
from aurora.config import get_settings
from aurora.conversa import ServicoConversa, criar_servico
from aurora.storage import inicializar_banco
from aurora.tools.regulamento import indice_regulamento

logger = logging.getLogger("aurora")

MISSING_KEY_WARNING = "GOOGLE_API_KEY não configurada: as rotas de conversa vão falhar."


def _lifespan(servico: ServicoConversa | None):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        if inicializar_banco():
            logger.info("Banco inicializado com os dados de dados/.")
        if not settings.google_api_key_configured:
            logger.warning(MISSING_KEY_WARNING)
        settings.sessions_db_path.parent.mkdir(parents=True, exist_ok=True)
        indice_regulamento()  # falha cedo se dados/regulamento.md estiver malformado
        app.state.servico = servico or criar_servico(settings)
        yield

    return lifespan


def create_app(servico: ServicoConversa | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    logger.setLevel(logging.INFO)
    app = FastAPI(title="Residencial Aurora", lifespan=_lifespan(servico))
    if servico is not None:
        app.state.servico = servico
    app.include_router(health.router)
    app.include_router(verificacao.router)
    app.include_router(sessoes.router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("aurora.main:app", host=settings.host, port=settings.port)
