"""`ServicoConversa.responder_confirmacao` com LLM roteirizado e sessões em `tmp_path`."""

import asyncio
import logging
import re
from pathlib import Path

import pytest
from google.adk.events import Event
from google.genai import errors as genai_errors
from google.genai import types

from aurora import storage
from aurora.agents.assistente import APP_NAME
from aurora.conversa import (
    MSG_RETOMADA_FALHOU,
    ConfirmacaoNaoPendente,
    ErroModelo,
    agente_ativo,
)
from tests.fakes import LlmPorAgente, resposta_chamada, resposta_texto

SRC = Path(__file__).resolve().parent.parent / "src" / "aurora"
SALAO = resposta_chamada("reservar_area", area="salao-de-festas", data="2030-04-20")
SALAO_MAIO = resposta_chamada("reservar_area", area="salao-de-festas", data="2030-05-11")
JOANA = resposta_chamada("autorizar_visitante", nome="Joana Ribeiro", data="2030-04-21")
NADA_FEITO = "Reserva não confirmada; nada foi feito."


def transferir(destino):
    return resposta_chamada("transfer_to_agent", agent_name=destino)


def ativas(apto="101"):
    return [(r.area, r.data) for r in storage.listar_reservas_ativas(apto)]


def roteiro_salao(*depois):
    return {"assistente": [transferir("reservas")], "reservas": [SALAO, *depois]}


async def _pedir_salao(servico, apto="101"):
    sid = await servico.criar_sessao(apto)
    r = await servico.enviar_mensagem(sid, "Reserve o salão de festas para 2030-04-20.")
    return sid, r.confirmacoes_pendentes[0]["id"]


def _status(confirmacao_id):
    with storage.conectar() as conn:
        return conn.execute(
            "SELECT status, confirmado FROM confirmacoes WHERE id = ?", (confirmacao_id,)
        ).fetchone()


def _respostas_de(eventos, nome):
    return [
        p["function_response"]
        for e in eventos
        for p in e.get("content", {}).get("parts", [])
        if p.get("function_response", {}).get("name") == nome
    ]


def test_negar_nao_grava_e_responde_mensagem(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao(resposta_texto(NADA_FEITO)))
        sid, cid = await _pedir_salao(servico)
        return cid, await servico.responder_confirmacao(sid, cid, False)

    cid, r = asyncio.run(cenario())
    assert ("salao-de-festas", "2030-04-20") not in ativas()
    assert "nada foi feito" in r.resposta
    assert _status(cid) == ("respondida", 0)
    assert r.confirmacoes_pendentes == []


def test_aprovar_grava_exatamente_uma_reserva(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao(resposta_texto("Reservado.")))
        sid, cid = await _pedir_salao(servico)
        r = await servico.responder_confirmacao(sid, cid, True)
        return cid, r, await servico.eventos(sid)

    cid, r, eventos = asyncio.run(cenario())
    assert ativas().count(("salao-de-festas", "2030-04-20")) == 1
    assert r.resposta == "Reservado."
    with storage.conectar() as conn:
        (chamada,) = conn.execute(
            "SELECT chamada_original_id FROM confirmacoes WHERE id = ?", (cid,)
        ).fetchone()
    respostas = [r for r in _respostas_de(eventos, "reservar_area") if r["id"] == chamada]
    assert [r["response"]["status"] for r in respostas] == ["pending", "success"]


def test_reenvio_mesmo_id_409_sem_efeito(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao(resposta_texto("Reservado.")))
        sid, cid = await _pedir_salao(servico)
        await servico.responder_confirmacao(sid, cid, True)
        antes = len(await servico.eventos(sid))
        with pytest.raises(ConfirmacaoNaoPendente):
            await servico.responder_confirmacao(sid, cid, True)
        return antes, len(await servico.eventos(sid))

    antes, depois = asyncio.run(cenario())
    assert antes == depois
    assert ativas().count(("salao-de-festas", "2030-04-20")) == 1


def test_id_inexistente_409(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao())
        sid, _ = await _pedir_salao(servico)
        antes = len(await servico.eventos(sid)), ativas()
        with pytest.raises(ConfirmacaoNaoPendente):
            await servico.responder_confirmacao(sid, "id-inexistente", True)
        return antes, (len(await servico.eventos(sid)), ativas())

    antes, depois = asyncio.run(cenario())
    assert antes == depois


def test_id_de_outra_sessao_409(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao())
        _s1, cid = await _pedir_salao(servico)
        s2 = await servico.criar_sessao("101")
        with pytest.raises(ConfirmacaoNaoPendente):
            await servico.responder_confirmacao(s2, cid, True)
        return cid

    cid = asyncio.run(cenario())
    assert _status(cid) == ("pendente", None)
    assert ("salao-de-festas", "2030-04-20") not in ativas()


def test_aprovar_visitante_grava_joana(servico_factory):
    roteiros = {
        "assistente": [transferir("visitantes")],
        "visitantes": [JOANA, resposta_texto("Joana autorizada.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid = await servico.criar_sessao("101")
        r = await servico.enviar_mensagem(sid, "Libera a Joana Ribeiro em 2030-04-21")
        return await servico.responder_confirmacao(sid, r.confirmacoes_pendentes[0]["id"], True)

    asyncio.run(cenario())
    assert [(v.nome, v.data) for v in storage.listar_visitantes("101")].count(
        ("Joana Ribeiro", "2030-04-21")
    ) == 1


def test_texto_nao_resolve_so_a_rota(servico_factory):
    roteiros = {
        "assistente": [transferir("visitantes")],
        "visitantes": [JOANA, resposta_texto("Preciso da confirmação."), resposta_texto("Pronto.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid = await servico.criar_sessao("101")
        r1 = await servico.enviar_mensagem(sid, "Libera a Joana Ribeiro em 2030-04-21")
        r2 = await servico.enviar_mensagem(sid, "Já estou confirmando aqui")
        assert [p["id"] for p in r2.confirmacoes_pendentes] == [
            p["id"] for p in r1.confirmacoes_pendentes
        ]
        assert "Joana Ribeiro" not in [v.nome for v in storage.listar_visitantes("101")]
        await servico.responder_confirmacao(sid, r1.confirmacoes_pendentes[0]["id"], True)

    asyncio.run(cenario())
    assert "Joana Ribeiro" in [v.nome for v in storage.listar_visitantes("101")]


def test_aprovacao_com_especialista_ainda_ativo_executa(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas")],
        "reservas": [SALAO, resposta_texto("Aguardo sua confirmação."), resposta_texto("Feito.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid, cid = await _pedir_salao(servico)
        r2 = await servico.enviar_mensagem(sid, "Ainda dá tempo?")
        assert [p["id"] for p in r2.confirmacoes_pendentes] == [cid]
        return await servico.responder_confirmacao(sid, cid, True)

    asyncio.run(cenario())
    assert ativas().count(("salao-de-festas", "2030-04-20")) == 1


def test_mudanca_de_assunto_expira_pendencia(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas"), resposta_texto("Olá!")],
        "reservas": [SALAO, transferir("assistente")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid, cid = await _pedir_salao(servico)
        r2 = await servico.enviar_mensagem(sid, "Oi")
        with pytest.raises(ConfirmacaoNaoPendente):
            await servico.responder_confirmacao(sid, cid, True)
        return cid, r2

    cid, r2 = asyncio.run(cenario())
    assert r2.confirmacoes_pendentes == []
    assert _status(cid) == ("expirada", None)
    assert ("salao-de-festas", "2030-04-20") not in ativas()


def test_retomada_sem_tool_original_avisa(servico_factory, caplog):
    async def cenario():
        servico = servico_factory(roteiro_salao())
        sid, cid = await _pedir_salao(servico)

        async def so_texto(**kw):
            content = types.Content(role="model", parts=[types.Part(text="Reservado!")])
            yield Event(author="reservas", content=content)

        servico.runner.run_async = so_texto
        with caplog.at_level(logging.ERROR, logger="aurora.conversa"):
            return cid, await servico.responder_confirmacao(sid, cid, True)

    cid, r = asyncio.run(cenario())
    assert r.resposta == MSG_RETOMADA_FALHOU
    assert any(cid in rec.getMessage() for rec in caplog.records)
    assert _status(cid) == ("respondida", 1)
    assert ("salao-de-festas", "2030-04-20") not in ativas()


def test_resposta_vazia_usa_message_da_tool(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao(resposta_texto(" ")))
        sid, cid = await _pedir_salao(servico)
        return await servico.responder_confirmacao(sid, cid, False)

    assert asyncio.run(cenario()).resposta == NADA_FEITO


def test_sem_chave_502_preserva_pendencia(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao())
        sid, cid = await _pedir_salao(servico)
        exigente = servico_factory({}, exige_chave=True)
        antes = len(await servico.eventos(sid))
        with pytest.raises(ErroModelo):
            await exigente.responder_confirmacao(sid, cid, True)
        return cid, antes, len(await servico.eventos(sid))

    cid, antes, depois = asyncio.run(cenario())
    assert _status(cid) == ("pendente", None)
    assert antes == depois


class _LlmQueQuebraNaRetomada(LlmPorAgente):
    """Roteiro normal; na 2ª chamada de `reservas` (depois da tool) lança `ServerError`."""

    async def generate_content_async(self, llm_request, stream=False):
        if self._agente(llm_request) == "reservas" and len(self.pedidos.get("reservas", [])) >= 1:
            raise genai_errors.ServerError(500, {"error": {"message": "boom"}})
        async for r in super().generate_content_async(llm_request, stream):
            yield r


def test_api_error_na_retomada_deixa_respondida(servico_factory):
    llm = _LlmQueQuebraNaRetomada(roteiros=roteiro_salao(), pedidos={})

    async def cenario():
        servico = servico_factory({}, llm=llm)
        sid, cid = await _pedir_salao(servico)
        with pytest.raises(ErroModelo):
            await servico.responder_confirmacao(sid, cid, True)
        return cid

    cid = asyncio.run(cenario())
    assert _status(cid) == ("respondida", 1)
    assert ativas().count(("salao-de-festas", "2030-04-20")) <= 1


def test_aprovar_apos_reinicio_executa_uma_vez(servico_factory):
    async def cenario():
        servico = servico_factory(roteiro_salao())
        sid, cid = await _pedir_salao(servico)
        novo = servico_factory({"reservas": [resposta_texto("Reservado.")]})
        await novo.responder_confirmacao(sid, cid, True)
        with pytest.raises(ConfirmacaoNaoPendente):
            await novo.responder_confirmacao(sid, cid, True)

    asyncio.run(cenario())
    assert ativas().count(("salao-de-festas", "2030-04-20")) == 1


@pytest.mark.parametrize("rodada", range(10))
def test_aprovacoes_simultaneas_sessoes_diferentes_uma_reserva(servico_factory, rodada):
    roteiros = {
        "assistente": [transferir("reservas"), transferir("reservas")],
        "reservas": [SALAO_MAIO, SALAO_MAIO, resposta_texto("ok"), resposta_texto("ok")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        s3, s4 = await servico.criar_sessao("101"), await servico.criar_sessao("201")
        ids = []
        for sid in (s3, s4):
            r = await servico.enviar_mensagem(sid, "Reserve o salão para 2030-05-11")
            ids.append(r.confirmacoes_pendentes[0]["id"])
        await asyncio.gather(
            servico.responder_confirmacao(s3, ids[0], True),
            servico.responder_confirmacao(s4, ids[1], True),
        )
        return [await servico.eventos(s) for s in (s3, s4)]

    eventos = asyncio.run(cenario())
    total = sum(ativas(apto).count(("salao-de-festas", "2030-05-11")) for apto in ("101", "201"))
    assert total == 1
    mensagens = [
        r["response"].get("message", "")
        for ev in eventos
        for r in _respostas_de(ev, "reservar_area")
    ]
    assert sum("acabou de ser ocupada" in m for m in mensagens) == 1


def test_lock_serializa_mensagem_e_confirmacao(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas")],
        "reservas": [SALAO, resposta_texto("Segue pendente."), resposta_texto("Feito.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid, cid = await _pedir_salao(servico)
        original = servico.runner.run_async
        marcas = []

        async def instrumentado(**kw):
            marcas.append("ini")
            await asyncio.sleep(0.2)
            async for e in original(**kw):
                yield e
            marcas.append("fim")

        servico.runner.run_async = instrumentado
        await asyncio.gather(
            servico.enviar_mensagem(sid, "Ainda dá tempo?"),
            servico.responder_confirmacao(sid, cid, True),
        )
        return marcas

    assert asyncio.run(cenario()) == ["ini", "fim", "ini", "fim"]


def test_agente_ativo_espelha_router_do_adk(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas"), resposta_texto("Olá!"), transferir("visitantes")],
        "reservas": [SALAO, transferir("assistente")],
        "visitantes": [JOANA],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        sid = await servico.criar_sessao("101")
        root = servico.runner.agent
        vistos = []
        for texto in ("Reserve o salão", "Oi", "Libera a Joana Ribeiro em 2030-04-21"):
            await servico.enviar_mensagem(sid, texto)
            sessao = await servico.session_service.get_session(
                app_name=APP_NAME, user_id="101", session_id=sid
            )
            vistos.append(
                (
                    agente_ativo(sessao.events, root),
                    servico.runner._find_agent_to_run(sessao, root).name,
                )
            )
        return vistos

    vistos = asyncio.run(cenario())
    assert all(meu == adk for meu, adk in vistos)
    assert [meu for meu, _ in vistos] == ["reservas", "assistente", "visitantes"]


def test_function_response_so_em_conteudo_confirmacao():
    achados = []
    for p in SRC.rglob("*.py"):
        for n, linha in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bFunctionResponse\(", linha):
                achados.append(f"{p.relative_to(SRC).as_posix()}:{n}")
    assert len(achados) == 1 and achados[0].startswith("conversa.py:")
    fonte = (SRC / "conversa.py").read_text(encoding="utf-8")
    chamadas = re.findall(r"conteudo_confirmacao\(", fonte)
    assert len(chamadas) == 2  # definição + única chamada
    corpo = fonte[fonte.index("async def responder_confirmacao") :]
    assert "conteudo_confirmacao(" in corpo.split("async def pendentes")[0]
    for p in SRC.rglob("*.py"):
        if p.name != "conversa.py":
            assert "conteudo_confirmacao" not in p.read_text(encoding="utf-8")


def test_integracao_id_listado_e_id_aceito(servico_factory):
    roteiros = {
        "assistente": [transferir("reservas"), transferir("visitantes")],
        "reservas": [SALAO, resposta_texto("Reservado.")],
        "visitantes": [JOANA, resposta_texto("Autorizada.")],
    }

    async def cenario():
        servico = servico_factory(roteiros)
        s1, id_salao = await _pedir_salao(servico)
        s2 = await servico.criar_sessao("101")
        r = await servico.enviar_mensagem(s2, "Libera a Joana Ribeiro em 2030-04-21")
        id_joana = r.confirmacoes_pendentes[0]["id"]
        await servico.responder_confirmacao(s1, id_salao, True)
        await servico.responder_confirmacao(s2, id_joana, True)
        with pytest.raises(ConfirmacaoNaoPendente):
            await servico.responder_confirmacao(s1, id_salao, True)

    asyncio.run(cenario())
    assert ativas().count(("salao-de-festas", "2030-04-20")) == 1
    assert "Joana Ribeiro" in [v.nome for v in storage.listar_visitantes("101")]
