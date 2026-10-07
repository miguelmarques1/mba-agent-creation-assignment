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

MSG_RETOMADA_FALHOU = "Não consegui concluir a ação confirmada; peça novamente, por favor."


class SessaoNaoEncontrada(Exception):
    """`session_id` ausente da tabela `sessoes`."""


class ApartamentoInexistente(Exception):
    """Apartamento fora de `apartamentos`."""


class ErroModelo(Exception):
    """Falha do modelo (APIError) ou chave ausente com modelos reais."""


class ConfirmacaoNaoPendente(Exception):
    """Sem confirmação `pendente` com esse `id` na sessão (inexistente, de outra, respondida ou expirada)."""


@dataclass(frozen=True)
class Pendencia:
    id: str
    acao: str
    detalhes: dict
    agente: str | None = None
    chamada_original_id: str | None = None


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
        pendencias.append(
            Pendencia(chamada.id, acao, dict(detalhes), event.author, original.get("id") or None)
        )
    return pendencias


def conteudo_confirmacao(confirmacao_id: str, confirmado: bool) -> types.Content:
    """Resposta de confirmação ao Runner. Único lugar do projeto que cria `FunctionResponse`."""
    parte = types.Part(
        function_response=types.FunctionResponse(
            id=confirmacao_id,
            name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
            response={"confirmed": confirmado},
        )
    )
    return types.Content(role="user", parts=[parte])


def _transferivel(agente: Any) -> bool:
    """O agente e seus ancestrais podem devolver o controle ao pai (como o roteador do ADK)."""
    while agente is not None:
        if not hasattr(agente, "disallow_transfer_to_parent"):
            return False
        if agente.disallow_transfer_to_parent:
            return False
        agente = agente.parent_agent
    return True


def agente_ativo(eventos: list[Event], root_agent: Any) -> str:
    """Agente que o Runner escolhe para a próxima mensagem.

    ⚠️ Espelha `_agent_router.find_agent_to_run` do ADK 2.11.0 sem resumabilidade: percorre os
    eventos do fim ao começo, ignora `user` e eventos de estado de agente, e devolve o primeiro
    autor que for o root ou um sub-agente transferível; senão, o root.
    """
    for evento in reversed(eventos):
        if evento.author == "user":
            continue
        if evento.actions.agent_state is not None or evento.actions.end_of_agent:
            continue
        if evento.author == root_agent.name:
            return root_agent.name
        agente = root_agent.find_sub_agent(evento.author)
        if agente is not None and _transferivel(agente):
            return agente.name
    return root_agent.name


def texto_final(event: Event) -> str | None:
    """Texto (sem `thought`) de uma resposta final de agente; `None` se não houver."""
    if event.author == "user" or not event.is_final_response() or not event.content:
        return None
    texto = "".join(p.text for p in event.content.parts or [] if p.text and not p.thought)
    return texto if texto.strip() else None


def _checar_retomada(eventos: list[Event], confirmacao: storage.Confirmacao) -> dict | None:
    """`response` do `function_response` da tool original nos eventos da retomada, se houver."""
    for evento in eventos:
        for resp in evento.get_function_responses():
            if confirmacao.chamada_original_id:
                achou = resp.id == confirmacao.chamada_original_id
            else:
                achou = resp.name == confirmacao.acao
            if achou:
                return dict(resp.response or {})
    return None


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

    def _exigir_chave(self) -> None:
        if self.exige_chave and not get_settings().google_api_key_configured:
            raise ErroModelo("GOOGLE_API_KEY não configurada.")

    async def executar_turno(self, session_id: str, content: types.Content) -> ResultadoTurno:
        apartamento = await self.apartamento(session_id)
        self._exigir_chave()
        async with self._lock(session_id):
            resposta, _ = await self._rodar_turno(session_id, apartamento, content)
            await self._expirar_inativas(session_id)
            pendentes = await self.pendentes(session_id)
        return ResultadoTurno(resposta=resposta, confirmacoes_pendentes=pendentes)

    async def _rodar_turno(
        self, session_id: str, apartamento: str, content: types.Content
    ) -> tuple[str, list[Event]]:
        """Roda o Runner (o chamador segura o lock), registra pendências e extrai a resposta."""
        resposta = ""
        eventos: list[Event] = []
        try:
            async for event in self.runner.run_async(
                user_id=apartamento, session_id=session_id, new_message=content
            ):
                eventos.append(event)
                for p in extrair_pendencias(event):
                    await asyncio.to_thread(
                        storage.registrar_pendencia,
                        p.id,
                        session_id,
                        p.acao,
                        p.detalhes,
                        p.agente,
                        p.chamada_original_id,
                    )
                texto = texto_final(event)
                if texto is not None:
                    resposta = texto
        except genai_errors.APIError as exc:
            raise ErroModelo(str(exc)) from exc
        return resposta, eventos

    async def _expirar_inativas(self, session_id: str) -> list[str]:
        """Expira as pendências cujo solicitante não é o agente ativo da sessão."""
        apartamento = await self.apartamento(session_id)
        sessao = await self.session_service.get_session(
            app_name=APP_NAME, user_id=apartamento, session_id=session_id
        )
        if sessao is None:
            return []
        ativo = agente_ativo(sessao.events, self.runner.agent)
        return await asyncio.to_thread(storage.expirar_pendencias, session_id, ativo)

    async def responder_confirmacao(
        self, session_id: str, confirmacao_id: str, confirmado: bool
    ) -> ResultadoTurno:
        """Garantia 1: responde uma única vez a uma pendência da sessão e retoma o turno."""
        apartamento = await self.apartamento(session_id)
        async with self._lock(session_id):
            await self._expirar_inativas(session_id)
            confirmacao = await asyncio.to_thread(
                storage.obter_pendente, confirmacao_id, session_id
            )
            if confirmacao is None:
                raise ConfirmacaoNaoPendente(confirmacao_id)
            self._exigir_chave()
            aceita = await asyncio.to_thread(
                storage.responder_pendencia, confirmacao_id, session_id, confirmado
            )
            if not aceita:
                raise ConfirmacaoNaoPendente(confirmacao_id)
            resposta, eventos = await self._rodar_turno(
                session_id, apartamento, conteudo_confirmacao(confirmacao_id, confirmado)
            )
            retorno = _checar_retomada(eventos, confirmacao)
            if retorno is None:
                logger.error(
                    "Retomada não reexecutou a tool original: sessão=%s id=%s chamada_original=%s",
                    session_id,
                    confirmacao_id,
                    confirmacao.chamada_original_id,
                )
                resposta = MSG_RETOMADA_FALHOU
            elif not resposta.strip():
                resposta = str(retorno.get("message") or "")
            await self._expirar_inativas(session_id)
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
