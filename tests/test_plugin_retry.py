"""ModelRetryPlugin com LLM roteirizado (sem Gemini)."""

import asyncio
import logging

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from aurora.agents.plugins import MENSAGEM_FALHA_TECNICA, NUDGE, ModelRetryPlugin
from tests.fakes import novo_llm, resposta_texto

VAZIA = LlmResponse(content=types.Content(role="model", parts=[]))
MALFORMADA = LlmResponse(error_code="MALFORMED_RESPONSE", error_message="x")


def _rodar(roteiro):
    llm = novo_llm(roteiro)
    plugin = ModelRetryPlugin()
    app = App(name="retry", root_agent=Agent(name="ag", model=llm), plugins=[plugin])

    async def cenario():
        runner = InMemoryRunner(app=app)
        sessao = await runner.session_service.create_session(app_name="retry", user_id="u")
        msg = types.Content(role="user", parts=[types.Part(text="oi")])
        return [
            e async for e in runner.run_async(user_id="u", session_id=sessao.id, new_message=msg)
        ]

    eventos = asyncio.run(cenario())
    textos = [p.text for e in eventos if e.content for p in e.content.parts or [] if p.text]
    return llm, plugin, textos


def test_retry_resposta_vazia_recupera():
    llm, _, textos = _rodar([VAZIA, resposta_texto("ok")])
    assert textos == ["ok"]
    assert len(llm.pedidos) == 2
    assert llm.pedidos[1].contents[-1] == NUDGE


def test_retry_malformed_recupera():
    llm, _, textos = _rodar([MALFORMADA, resposta_texto("depois")])
    assert textos == ["depois"]
    assert len(llm.pedidos) == 2


def test_retry_esgotado_degrada():
    llm, _, textos = _rodar([VAZIA] * 4)
    assert textos == [MENSAGEM_FALHA_TECNICA]
    assert len(llm.pedidos) == 4


def test_resposta_normal_sem_retry(caplog):
    with caplog.at_level(logging.WARNING, logger="aurora"):
        llm, _, textos = _rodar([resposta_texto("direto")])
    assert textos == ["direto"]
    assert len(llm.pedidos) == 1
    assert not [r for r in caplog.records if "retry" in r.getMessage()]


def test_plugin_limpa_estado():
    _, plugin, _ = _rodar([VAZIA, resposta_texto("ok")])
    assert plugin._pending_requests == {}
