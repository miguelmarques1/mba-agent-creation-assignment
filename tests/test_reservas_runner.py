"""Ida e volta da confirmação pelo Runner do ADK, com LLM roteirizado (sem Gemini)."""

import asyncio
import json

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types

from aurora import storage
from aurora.agents.callbacks import handle_tool_error_solicitacao
from aurora.tools.reservas import MSG_NAO_CONFIRMADA, MSG_OCUPADA_APOS_APROVACAO, RESERVAS_TOOLS
from tests.fakes import novo_llm, resposta_chamada, resposta_texto

SALAO = resposta_chamada("reservar_area", area="salao-de-festas", data="2030-04-20")
SOBRA = [resposta_texto("ok")] * 3
PAYLOAD = {
    "acao": "reservar_area",
    "detalhes": {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0},
}


def _texto(texto: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=texto)])


def _confirmacao(confirmacao_id: str, confirmado: bool) -> types.Content:
    parte = types.Part(
        function_response=types.FunctionResponse(
            id=confirmacao_id, name="adk_request_confirmation", response={"confirmed": confirmado}
        )
    )
    return types.Content(role="user", parts=[parte])


class Sessao:
    def __init__(self, roteiro, apartamento="101"):
        self.llm = novo_llm(roteiro)
        agente = Agent(
            name="reservas_teste",
            model=self.llm,
            tools=list(RESERVAS_TOOLS),
            on_tool_error_callback=handle_tool_error_solicitacao,
        )
        self.runner = InMemoryRunner(agent=agente, app_name="reservas_teste")
        self.apartamento = apartamento
        self.eventos: list = []

    async def abrir(self):
        sessao = await self.runner.session_service.create_session(
            app_name="reservas_teste", user_id="u", state={"apartamento": self.apartamento}
        )
        self.id = sessao.id
        return self

    async def enviar(self, mensagem: types.Content) -> list:
        novos = []
        async for evento in self.runner.run_async(
            user_id="u", session_id=self.id, new_message=mensagem
        ):
            novos.append(evento)
        self.eventos.extend(novos)
        return novos


def partes(eventos):
    return [p for e in eventos for p in (e.content.parts if e.content else []) or []]


def chamadas(eventos, nome=None):
    return [
        p.function_call
        for p in partes(eventos)
        if p.function_call and (nome is None or p.function_call.name == nome)
    ]


def respostas(eventos, nome):
    return [
        p.function_response
        for p in partes(eventos)
        if p.function_response and p.function_response.name == nome
    ]


def ativas(apto="101"):
    return [(r.area, r.data) for r in storage.listar_reservas_ativas(apto)]


def test_runner_quadra_sem_adk_request_confirmation(db):
    async def cenario():
        sessao = await Sessao(
            [resposta_chamada("reservar_area", area="quadra", data="2030-04-06"), *SOBRA]
        ).abrir()
        return await sessao.enviar(_texto("Reserve a quadra para 2030-04-06."))

    eventos = asyncio.run(cenario())
    assert chamadas(eventos, "adk_request_confirmation") == []
    assert ("quadra", "2030-04-06") in ativas("101")


def test_runner_salao_gera_adk_request_confirmation(db):
    async def cenario():
        sessao = await Sessao([SALAO, *SOBRA]).abrir()
        eventos = await sessao.enviar(_texto("Reserve o salão de festas para 2030-04-20."))
        return sessao, eventos

    sessao, eventos = asyncio.run(cenario())
    pedidos = chamadas(eventos, "adk_request_confirmation")
    assert len(pedidos) == 1
    assert pedidos[0].args["originalFunctionCall"]["name"] == "reservar_area"
    assert pedidos[0].args["toolConfirmation"]["payload"] == PAYLOAD
    assert ("salao-de-festas", "2030-04-20") not in ativas("101")
    assert len(sessao.llm.pedidos) == 1


def _pendente_e_resposta(confirmado: bool):
    async def cenario():
        sessao = await Sessao([SALAO, *SOBRA]).abrir()
        eventos = await sessao.enviar(_texto("Reserve o salão de festas para 2030-04-20."))
        confirmacao_id = chamadas(eventos, "adk_request_confirmation")[0].id
        depois = await sessao.enviar(_confirmacao(confirmacao_id, confirmado))
        return respostas(depois, "reservar_area")

    return asyncio.run(cenario())


def test_runner_salao_negado_nao_grava(db):
    finais = _pendente_e_resposta(False)
    assert [r.response["status"] for r in finais] == ["cancelled"]
    assert finais[0].response["message"] == MSG_NAO_CONFIRMADA
    assert ("salao-de-festas", "2030-04-20") not in ativas("101")


def test_runner_salao_aprovado_grava_uma_vez(db):
    finais = _pendente_e_resposta(True)
    assert [r.response["status"] for r in finais] == ["success"]
    assert ativas("101").count(("salao-de-festas", "2030-04-20")) == 1


def test_runner_aprovado_com_data_tomada_nao_grava(db):
    async def cenario():
        sessao = await Sessao([SALAO, *SOBRA]).abrir()
        eventos = await sessao.enviar(_texto("Reserve o salão de festas para 2030-04-20."))
        confirmacao_id = chamadas(eventos, "adk_request_confirmation")[0].id
        assert storage.criar_reserva("201", "salao-de-festas", "2030-04-20").status == "criada"
        depois = await sessao.enviar(_confirmacao(confirmacao_id, True))
        return respostas(depois, "reservar_area")

    finais = asyncio.run(cenario())
    assert finais[0].response["message"] == MSG_OCUPADA_APOS_APROVACAO
    assert ("salao-de-festas", "2030-04-20") not in ativas("101")


def test_runner_eventos_sem_dados_de_outro_apartamento(db):
    async def cenario():
        sessao = await Sessao(
            [
                resposta_chamada("cancelar_reserva", area="salao-de-festas", data="2030-03-16"),
                resposta_chamada("reservar_area", area="salao-de-festas", data="2030-03-16"),
                *SOBRA,
            ]
        ).abrir()
        await sessao.enviar(_texto("Cancele a reserva do salão de festas do dia 2030-03-16."))
        await sessao.enviar(_texto("Reserve o salão de festas para 2030-03-16."))
        return sessao.eventos

    eventos = asyncio.run(cenario())
    texto = json.dumps([e.model_dump(mode="json") for e in eventos], ensure_ascii=False)
    assert "RSV-4821" not in texto
    assert ("RSV-4821", "salao-de-festas", "2030-03-16") in [
        (r.codigo, r.area, r.data) for r in storage.listar_reservas_ativas("302")
    ]
