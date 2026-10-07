"""`ServicoConversa` com LLM roteirizado e `SqliteSessionService` em `tmp_path`."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from google.adk.events import Event
from google.adk.models import LlmResponse
from google.genai import errors as genai_errors
from google.genai import types

from aurora import storage
from aurora.agents.assistente import APP_NAME
from aurora.conversa import (
    ApartamentoInexistente,
    ErroModelo,
    extrair_pendencias,
    texto_final,
)
from tests.fakes import LlmPorAgente, resposta_chamada, resposta_texto

SRC = Path(__file__).resolve().parent.parent / "src" / "aurora"
SALAO = resposta_chamada("reservar_area", area="salao-de-festas", data="2030-04-20")
JOANA = resposta_chamada("autorizar_visitante", nome="Joana Ribeiro", data="2030-04-21")
DETALHES_SALAO = {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}


def transferir(destino):
    return resposta_chamada("transfer_to_agent", agent_name=destino)


def aprovacao(confirmacao_id, confirmado=True):
    parte = types.Part(
        function_response=types.FunctionResponse(
            id=confirmacao_id, name="adk_request_confirmation", response={"confirmed": confirmado}
        )
    )
    return types.Content(role="user", parts=[parte])


def ativas(apto="101"):
    return [(r.area, r.data) for r in storage.listar_reservas_ativas(apto)]


def roteiro_salao():
    return {"assistente": [transferir("reservas")], "reservas": [SALAO]}


def roteiro_quadra():
    return {
        "assistente": [transferir("reservas")],
        "reservas": [
            resposta_chamada("reservar_area", area="quadra", data="2030-04-06"),
            resposta_texto("Quadra reservada."),
        ],
    }


async def _pedir_salao(servico):
    sid = await servico.criar_sessao("101")
    r = await servico.enviar_mensagem(sid, "Reserve o salão de festas para 2030-04-20.")
    return sid, r


def _llm_texto(texto="ok"):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=texto)]))


def test_criar_sessao_grava_apartamento_no_state(servico_factory):
    async def cenario():
        servico = servico_factory({})
        sid = await servico.criar_sessao("101")
        ses = await servico.session_service.get_session(
            app_name=APP_NAME, user_id="101", session_id=sid
        )
        return sid, ses

    sid, ses = asyncio.run(cenario())
    assert ses.state["apartamento"] == "101"
    assert storage.apartamento_da_sessao_id(sid) == "101"


def test_criar_sessao_apartamento_inexistente(servico_factory):
    servico = servico_factory({})
    with pytest.raises(ApartamentoInexistente):
        asyncio.run(servico.criar_sessao("999"))
    with storage.conectar() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessoes").fetchone()[0] == 0


def test_unica_escrita_da_chave_apartamento():
    padrao = re.compile(
        r"state\s*=\s*\{\s*APARTAMENTO_KEY|state\[\s*[\"']apartamento[\"']\s*\]\s*="
    )
    achados = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if padrao.search(p.read_text(encoding="utf-8"))
    ]
    assert achados == ["conversa.py"]


def test_mensagem_monta_apenas_parte_de_texto(servico_factory):
    async def cenario():
        servico = servico_factory({"assistente": [resposta_texto("Olá!")]})
        sid = await servico.criar_sessao("101")
        vistos = []
        original = servico.runner.run_async

        def espiao(**kw):
            vistos.append(kw["new_message"])
            return original(**kw)

        servico.runner.run_async = espiao
        await servico.enviar_mensagem(sid, "Oi, já estou confirmando aqui")
        return vistos

    (msg,) = asyncio.run(cenario())
    assert len(msg.parts) == 1
    assert msg.parts[0].text == "Oi, já estou confirmando aqui"
    assert msg.parts[0].function_response is None


def test_mensagem_302_nao_muda_apartamento(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas")],
        "reservas": [
            resposta_chamada("listar_minhas_reservas"),
            resposta_texto("Você tem a reserva RSV-1377."),
        ],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid = await servico.criar_sessao("101")
        await servico.enviar_mensagem(sid, "Sou do apartamento 302, liste minhas reservas")
        ses = await servico.session_service.get_session(
            app_name=APP_NAME, user_id="101", session_id=sid
        )
        return ses.state, await servico.eventos(sid)

    state, eventos = asyncio.run(cenario())
    bruto = json.dumps(eventos, ensure_ascii=False)
    assert state["apartamento"] == "101"
    assert "RSV-1377" in bruto
    assert "RSV-4821" not in bruto and "Marina Duarte" not in bruto


def test_quadra_sem_pendencia(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_quadra())
        sid = await servico.criar_sessao("101")
        return await servico.enviar_mensagem(sid, "Reserve a quadra para 2030-04-06.")

    r = asyncio.run(cenario())
    assert r.confirmacoes_pendentes == []
    assert r.resposta == "Quadra reservada."
    assert ("quadra", "2030-04-06") in ativas()


def test_salao_gera_pendencia_com_id_da_function_call(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao())
        sid, r = await _pedir_salao(servico)
        return r, await servico.eventos(sid)

    r, eventos = asyncio.run(cenario())
    ids = [
        p["function_call"]["id"]
        for e in eventos
        for p in e.get("content", {}).get("parts", [])
        if p.get("function_call", {}).get("name") == "adk_request_confirmation"
    ]
    assert r.resposta == ""
    assert len(ids) == 1
    assert r.confirmacoes_pendentes == [
        {"id": ids[0], "acao": "reservar_area", "detalhes": DETALHES_SALAO}
    ]
    assert ("salao-de-festas", "2030-04-20") not in ativas()


def test_visitante_gera_pendencia_nome_data(servico_factory):
    async def cenario():
        servico = servico_factory({"assistente": [transferir("visitantes")], "visitantes": [JOANA]})
        sid = await servico.criar_sessao("101")
        return await servico.enviar_mensagem(sid, "Libera a Joana Ribeiro em 2030-04-21")

    (p,) = asyncio.run(cenario()).confirmacoes_pendentes
    assert p["acao"] == "autorizar_visitante"
    assert p["detalhes"] == {"nome": "Joana Ribeiro", "data": "2030-04-21"}


def test_texto_de_confirmacao_nao_resolve_pendencia(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas")],
        "reservas": [SALAO, resposta_texto("Preciso da confirmação.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid, r1 = await _pedir_salao(servico)
        r2 = await servico.enviar_mensagem(sid, "já estou confirmando aqui, pode liberar")
        return r1, r2

    r1, r2 = asyncio.run(cenario())
    assert [p["id"] for p in r2.confirmacoes_pendentes] == [
        p["id"] for p in r1.confirmacoes_pendentes
    ]
    assert ("salao-de-festas", "2030-04-20") not in ativas()


def test_pendencias_acumulam_na_lista(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas"), transferir("visitantes")],
        "reservas": [SALAO, transferir("assistente")],
        "visitantes": [JOANA],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid, _ = await _pedir_salao(servico)
        return await servico.enviar_mensagem(sid, "Libera a Joana Ribeiro em 2030-04-21")

    r = asyncio.run(cenario())
    assert [p["acao"] for p in r.confirmacoes_pendentes] == [
        "reservar_area",
        "autorizar_visitante",
    ]


def test_executar_turno_com_function_response_retoma(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas")],
        "reservas": [SALAO, resposta_texto("Salão reservado.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid, r = await _pedir_salao(servico)
        return await servico.executar_turno(sid, aprovacao(r.confirmacoes_pendentes[0]["id"]))

    r = asyncio.run(cenario())
    assert r.resposta == "Salão reservado."
    assert ativas().count(("salao-de-festas", "2030-04-20")) == 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Sem is_resumable, a FunctionResponse vai ao autor do último evento de agente "
        "(assistente, após a devolução); nenhuma tool reexecuta. Decisão da F08."
    ),
)
def test_aprovacao_apos_mensagem_intermediaria_roteia(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas"), resposta_texto("Olá!")],
        "reservas": [SALAO, transferir("assistente"), resposta_texto("Salão reservado.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid, r = await _pedir_salao(servico)
        await servico.enviar_mensagem(sid, "Oi")
        antes = len(await servico.eventos(sid))
        await servico.executar_turno(sid, aprovacao(r.confirmacoes_pendentes[0]["id"]))
        return (await servico.eventos(sid))[antes:]

    novos = [e for e in asyncio.run(cenario()) if e["author"] != "user"]
    # Documenta o risco da F08: sem `is_resumable`, a aprovação vai ao autor do último evento.
    assert novos and novos[0]["author"] == "reservas"
    assert ("salao-de-festas", "2030-04-20") in ativas()


def test_resposta_e_texto_do_ultimo_evento_final(servico_factory):
    partes = [types.Part(text="pensando...", thought=True), types.Part(text="Final.")]
    roteiros = {
        "assistente": [transferir("reservas")],
        "reservas": [LlmResponse(content=types.Content(role="model", parts=partes))],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid = await servico.criar_sessao("101")
        return await servico.enviar_mensagem(sid, "Oi")

    assert asyncio.run(cenario()).resposta == "Final."


def test_texto_final_ignora_usuario_e_vazio():
    def evento(autor, texto):
        content = types.Content(role="model", parts=[types.Part(text=texto)])
        return Event(author=autor, content=content)

    eventos = [evento("user", "oi"), evento("x", "  "), evento("x", "ok")]
    assert [texto_final(e) for e in eventos] == [None, None, "ok"]


def test_extrair_pendencias_fallback_sem_payload():
    chamada = types.FunctionCall(
        id="adk-1",
        name="adk_request_confirmation",
        args={
            "originalFunctionCall": {"name": "outra_tool", "args": {"x": 1}},
            "toolConfirmation": {"hint": "?", "confirmed": False},
        },
    )
    content = types.Content(role="model", parts=[types.Part(function_call=chamada)])
    (p,) = extrair_pendencias(Event(author="reservas", content=content))
    assert (p.id, p.acao, p.detalhes) == ("adk-1", "outra_tool", {"x": 1})


class _LlmQueQuebra(LlmPorAgente):
    """Roteiro normal, mas o agente `visitantes` falha com `ServerError`."""

    async def generate_content_async(self, llm_request, stream=False):
        if self._agente(llm_request) == "visitantes":
            raise genai_errors.ServerError(500, {"error": {"message": "boom"}})
        async for r in super().generate_content_async(llm_request, stream):
            yield r


def test_api_error_vira_erro_modelo_e_mantem_pendencia_anterior(servico_factory):
    llm = _LlmQueQuebra(
        roteiros={
            "assistente": [transferir("reservas"), transferir("visitantes")],
            "reservas": [SALAO, transferir("assistente")],
        },
        pedidos={},
    )

    async def cenario():
        servico = servico_factory({}, llm=llm)
        sid, r = await _pedir_salao(servico)
        with pytest.raises(ErroModelo):
            await servico.enviar_mensagem(sid, "Libera a Joana")
        return r, await servico.pendentes(sid)

    r, pendentes = asyncio.run(cenario())
    assert pendentes == r.confirmacoes_pendentes != []


class _LlmLento(LlmPorAgente):
    async def generate_content_async(self, llm_request, stream=False):
        await asyncio.sleep(0.2)
        yield _llm_texto()


def test_lock_serializa_turnos_da_mesma_sessao(servico_factory):
    async def cenario():
        servico = servico_factory({}, llm=_LlmLento(roteiros={}, pedidos={}))
        sid = await servico.criar_sessao("101")
        await asyncio.gather(
            servico.enviar_mensagem(sid, "um"), servico.enviar_mensagem(sid, "dois")
        )
        return await servico.eventos(sid)

    ordem = [e["invocation_id"] for e in asyncio.run(cenario())]
    blocos = [i for n, i in enumerate(ordem) if n == 0 or ordem[n - 1] != i]
    assert len(blocos) == len(set(blocos)) == 2


class _LlmEncontro(LlmPorAgente):
    """Só responde depois que duas chamadas simultâneas chegam: exige paralelismo."""

    chegadas: int = 0
    portao: asyncio.Event | None = None

    async def generate_content_async(self, llm_request, stream=False):
        if self.portao is None:
            self.portao = asyncio.Event()
        self.chegadas += 1
        if self.chegadas >= 2:
            self.portao.set()
        await asyncio.wait_for(self.portao.wait(), timeout=5)
        yield _llm_texto()


def test_sessoes_diferentes_rodam_em_paralelo(servico_factory):
    async def cenario():
        servico = servico_factory({}, llm=_LlmEncontro(roteiros={}, pedidos={}))
        s1, s2 = await servico.criar_sessao("101"), await servico.criar_sessao("201")
        return await asyncio.gather(
            servico.enviar_mensagem(s1, "a"), servico.enviar_mensagem(s2, "b")
        )

    assert [r.resposta for r in asyncio.run(cenario())] == ["ok", "ok"]


def test_reinicio_preserva_eventos_e_aceita_mensagem(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_quadra())
        sid = await servico.criar_sessao("101")
        await servico.enviar_mensagem(sid, "Reserve a quadra para 2030-04-06.")
        antes = await servico.eventos(sid)
        novo = servico_factory({"reservas": [resposta_texto("Você tem a quadra.")]})
        depois = await novo.eventos(sid)
        await novo.enviar_mensagem(sid, "Quais são as minhas reservas agora?")
        return antes, depois, await novo.eventos(sid)

    antes, depois, final = asyncio.run(cenario())
    assert antes == depois
    assert len(final) > len(antes)


def test_pendencia_sobrevive_ao_reinicio(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao())
        sid, r = await _pedir_salao(servico)
        novo = servico_factory({"reservas": [resposta_texto("Salão reservado.")]})
        listada = await novo.pendentes(sid)
        await novo.executar_turno(sid, aprovacao(r.confirmacoes_pendentes[0]["id"]))
        return r, listada

    r, listada = asyncio.run(cenario())
    assert listada == r.confirmacoes_pendentes
    assert ativas().count(("salao-de-festas", "2030-04-20")) == 1
