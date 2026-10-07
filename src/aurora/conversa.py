"""Serviço de conversa: Runner do ADK, sessões persistidas e registro de confirmações pendentes."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from google.adk.events import Event
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import errors as genai_errors
from google.genai import types

from aurora import storage
from aurora.agents.assistente import APP_NAME, criar_app
from aurora.config import Settings, get_settings
from aurora.tools.sessao import APARTAMENTO_KEY

logger = logging.getLogger("aurora.conversa")


class SessaoNaoEncontrada(Exception):
    """`session_id` ausente da tabela `sessoes`."""


class ApartamentoInexistente(Exception):
    """Apartamento fora de `apartamentos`."""


class ErroModelo(Exception):
    """Falha do modelo (APIError) ou chave ausente com modelos reais."""


@dataclass(frozen=True)
class Pendencia:
    id: str
    acao: str
    detalhes: dict


@dataclass(frozen=True)
class ResultadoTurno:
    resposta: str
    confirmacoes_pendentes: list[dict] = field(default_factory=list)


def extrair_pendencias(event: Event) -> list[Pendencia]:
    """Pedidos `adk_request_confirmation` do evento, com `acao`/`detalhes` do payload da tool."""
    pendencias = []
    for chamada in event.get_function_calls():
        if chamada.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME or not chamada.id:
            continue
        args = chamada.args or {}
        confirmacao = args.get("toolConfirmation") or {}
        original = args.get("originalFunctionCall") or {}
        payload = confirmacao.get("payload")
        if isinstance(payload, dict) and payload.get("acao"):
            acao = str(payload["acao"])
            detalhes = payload.get("detalhes") or {}
        else:
            acao = str(original.get("name", ""))
            detalhes = original.get("args") or {}
        pendencias.append(Pendencia(chamada.id, acao, dict(detalhes)))
    return pendencias


def texto_final(event: Event) -> str | None:
    """Texto (sem `thought`) de uma resposta final de agente; `None` se não houver."""
    if event.author == "user" or not event.is_final_response() or not event.content:
        return None
    texto = "".join(p.text for p in event.content.parts or [] if p.text and not p.thought)
    return texto if texto.strip() else None


class ServicoConversa:
    def __init__(
        self,
        app: Any,
        session_service: BaseSessionService,
        exige_chave: bool = True,
    ) -> None:
        self.session_service = session_service
        self.exige_chave = exige_chave
        self.runner = Runner(app=app, session_service=session_service)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    async def criar_sessao(self, apartamento: str) -> str:
        apartamento = str(apartamento).strip()
        if not apartamento or not await asyncio.to_thread(storage.apartamento_existe, apartamento):
            raise ApartamentoInexistente(apartamento)
        # Única escrita da chave `apartamento` do projeto (Garantia 2).
        sessao = await self.session_service.create_session(
            app_name=APP_NAME, user_id=apartamento, state={APARTAMENTO_KEY: apartamento}
        )
        try:
            await asyncio.to_thread(storage.registrar_sessao, sessao.id, apartamento)
        except Exception:
            logger.exception("Falha ao registrar a sessão %s; removendo.", sessao.id)
            try:
                await self.session_service.delete_session(
                    app_name=APP_NAME, user_id=apartamento, session_id=sessao.id
                )
            except Exception:
                logger.exception("Falha ao remover a sessão órfã %s.", sessao.id)
            raise
        return sessao.id

    async def apartamento(self, session_id: str) -> str:
        apartamento = await asyncio.to_thread(storage.apartamento_da_sessao_id, session_id)
        if apartamento is None:
            raise SessaoNaoEncontrada(session_id)
        return apartamento

    async def enviar_mensagem(self, session_id: str, texto: str) -> ResultadoTurno:
        # Garantia 1: a rota só monta parts de texto; nenhuma frase vira function_response.
        content = types.Content(role="user", parts=[types.Part(text=texto)])
        return await self.executar_turno(session_id, content)

    async def executar_turno(self, session_id: str, content: types.Content) -> ResultadoTurno:
        apartamento = await self.apartamento(session_id)
        if self.exige_chave and not get_settings().google_api_key_configured:
            raise ErroModelo("GOOGLE_API_KEY não configurada.")
        resposta = ""
        async with self._lock(session_id):
            try:
                async for event in self.runner.run_async(
                    user_id=apartamento, session_id=session_id, new_message=content
                ):
                    for p in extrair_pendencias(event):
                        await asyncio.to_thread(
                            storage.registrar_pendencia, p.id, session_id, p.acao, p.detalhes
                        )
                    texto = texto_final(event)
                    if texto is not None:
                        resposta = texto
            except genai_errors.APIError as exc:
                raise ErroModelo(str(exc)) from exc
        pendentes = await self.pendentes(session_id)
        return ResultadoTurno(resposta=resposta, confirmacoes_pendentes=pendentes)

    async def pendentes(self, session_id: str) -> list[dict]:
        lista = await asyncio.to_thread(storage.listar_pendentes, session_id)
        return [p.para_dict() for p in lista]

    async def eventos(self, session_id: str) -> list[dict]:
        apartamento = await self.apartamento(session_id)
        sessao = await self.session_service.get_session(
            app_name=APP_NAME, user_id=apartamento, session_id=session_id
        )
        if sessao is None:
            raise SessaoNaoEncontrada(session_id)
        return [e.model_dump(mode="json", exclude_none=True) for e in sessao.events]


def criar_servico(settings: Settings | None = None) -> ServicoConversa:
    settings = settings or get_settings()
    settings.sessions_db_path.parent.mkdir(parents=True, exist_ok=True)
    return ServicoConversa(
        criar_app(),
        SqliteSessionService(str(settings.sessions_db_path)),
        exige_chave=True,
    )
