import asyncio
import inspect
import json
import re
import sqlite3

import pytest
from google.adk.tools import FunctionTool

from aurora import storage
from aurora.agents.callbacks import handle_tool_error_solicitacao
from aurora.storage import repositorio as repo
from aurora.tools import reservas
from aurora.tools.reservas import (
    MSG_AREA_INVALIDA,
    MSG_DATA_INVALIDA,
    MSG_IDENTIFICACAO_CANCELAMENTO,
    MSG_NAO_CONFIRMADA,
    MSG_NAO_ENCONTREI_AREA_DATA,
    MSG_NAO_ENCONTREI_CODIGO,
    MSG_OCUPADA,
    MSG_OCUPADA_APOS_APROVACAO,
    RESERVAS_TOOLS,
    cancelar_reserva,
    consultar_disponibilidade,
    listar_areas,
    listar_minhas_reservas,
    reservar_area,
    resolver_area,
)
from aurora.tools.sessao import ApartamentoAusenteError
from tests.fakes import contexto_reservas


def run(coro):
    return asyncio.run(coro)


def ativas(apto="101"):
    return [(r.codigo, r.area, r.data) for r in storage.listar_reservas_ativas(apto)]


def total_ativas():
    with storage.conectar() as conn:
        return conn.execute("SELECT COUNT(*) FROM reservas WHERE status = 'ativa'").fetchone()[0]


def status_de(codigo):
    with storage.conectar() as conn:
        return conn.execute("SELECT status FROM reservas WHERE codigo = ?", (codigo,)).fetchone()[0]


def sem_vazamento(resposta):
    texto = json.dumps(resposta, ensure_ascii=False)
    assert "RSV-4821" not in texto
    assert not re.search(r"\b302\b", texto)
    assert "Rafael" not in texto


# --- garantia 2: apartamento só vem do state ---


def test_tools_sem_parametro_apartamento():
    for tool in RESERVAS_TOOLS:
        assert not any("apart" in nome for nome in inspect.signature(tool).parameters)
        schema = FunctionTool(tool)._get_declaration().parameters_json_schema or {}
        propriedades = schema.get("properties", {})
        assert not any("apart" in nome for nome in propriedades)
        assert "tool_context" not in propriedades


def test_tools_nao_escrevem_apartamento_no_state(db):
    ctx = contexto_reservas("101")
    run(consultar_disponibilidade("quadra", "2030-04-06", ctx))
    run(listar_minhas_reservas(ctx))
    run(listar_areas())
    run(reservar_area("quadra", "2030-04-06", ctx))
    run(reservar_area("salao-de-festas", "2030-04-20", ctx))
    run(cancelar_reserva(ctx, area="quadra", data="2030-03-09"))
    assert ctx.state == {"apartamento": "101"}


def test_tools_usam_apartamento_do_state(db):
    resposta = run(listar_minhas_reservas(contexto_reservas("201")))
    assert [r["codigo"] for r in resposta["reservas"]] == ["RSV-2950"]


def test_tool_sem_apartamento_no_state_propaga_erro_de_infra(db):
    with pytest.raises(ApartamentoAusenteError):
        run(listar_minhas_reservas(contexto_reservas(None)))


def test_listar_minhas_reservas_so_do_apartamento_da_sessao(db):
    resposta = run(listar_minhas_reservas(contexto_reservas("101")))
    assert [r["codigo"] for r in resposta["reservas"]] == ["RSV-1377"]
    sem_vazamento(resposta)
    assert "RSV-2950" not in json.dumps(resposta)


# --- consulta ---


def test_consultar_disponibilidade_so_area_data_disponivel(db):
    ctx = contexto_reservas("101")
    ocupada = run(consultar_disponibilidade("salao-de-festas", "2030-03-16", ctx))
    livre = run(consultar_disponibilidade("salao-de-festas", "2030-03-17", ctx))
    assert set(ocupada) == {"status", "area", "data", "disponivel"}
    assert ocupada["disponivel"] is False
    assert livre["disponivel"] is True
    sem_vazamento(ocupada)
    sem_vazamento(livre)


def test_consultar_area_e_data_invalidas(db):
    ctx = contexto_reservas("101")
    area = run(consultar_disponibilidade("piscina", "2030-03-16", ctx))
    assert area["message"] == MSG_AREA_INVALIDA
    assert area["areas_validas"] == ["churrasqueira", "quadra", "salao-de-festas"]
    data = run(consultar_disponibilidade("quadra", "16/03/2030", ctx))
    assert data["message"] == MSG_DATA_INVALIDA


def test_resolver_area_por_id_e_nome(db):
    assert resolver_area("salão de festas").id == "salao-de-festas"
    assert resolver_area("Quadra poliesportiva").id == "quadra"
    assert resolver_area("QUADRA").id == "quadra"
    assert resolver_area("piscina") is None


def test_listar_areas_catalogo_do_banco(db):
    areas = run(listar_areas())["areas"]
    assert {a["id"]: a["taxa"] for a in areas} == {
        "churrasqueira": 80.0,
        "quadra": 0.0,
        "salao-de-festas": 150.0,
    }
    assert all(a["nome"] for a in areas)


# --- reservar ---


def test_reservar_quadra_grava_sem_confirmacao(db):
    ctx = contexto_reservas("101")
    resposta = run(reservar_area("quadra", "2030-04-06", ctx))
    assert resposta["status"] == "success"
    assert re.fullmatch(r"RSV-[A-Z0-9]{6}", resposta["codigo"])
    assert ctx.confirmacoes_pedidas == []
    assert ("quadra", "2030-04-06") in [(a, d) for _, a, d in ativas("101")]


def test_reservar_salao_pede_confirmacao_sem_gravar(db):
    ctx = contexto_reservas("101")
    antes = total_ativas()
    resposta = run(reservar_area("salao-de-festas", "2030-04-20", ctx))
    assert resposta["status"] == "pending"
    assert len(ctx.confirmacoes_pedidas) == 1
    assert ctx.confirmacoes_pedidas[0]["payload"] == {
        "acao": "reservar_area",
        "detalhes": {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0},
    }
    assert "R$ 150,00" in ctx.confirmacoes_pedidas[0]["hint"]
    assert ctx.actions.skip_summarization is True
    assert total_ativas() == antes


def test_reservar_churrasqueira_pede_confirmacao(db):
    ctx = contexto_reservas("101")
    resposta = run(reservar_area("churrasqueira", "2030-04-20", ctx))
    assert resposta["status"] == "pending"
    assert resposta["detalhes"]["taxa"] == 80.0


def test_reservar_negado_nao_grava(db):
    ctx = contexto_reservas("101", confirmado=False)
    antes = total_ativas()
    resposta = run(reservar_area("salao-de-festas", "2030-04-20", ctx))
    assert resposta == {"status": "cancelled", "message": MSG_NAO_CONFIRMADA}
    assert ctx.confirmacoes_pedidas == []
    assert total_ativas() == antes


def test_reservar_aprovado_grava_uma_vez(db):
    ctx = contexto_reservas("101", confirmado=True)
    resposta = run(reservar_area("salao-de-festas", "2030-04-20", ctx))
    assert resposta["status"] == "success"
    assert [(a, d) for _, a, d in ativas("101") if a == "salao-de-festas"] == [
        ("salao-de-festas", "2030-04-20")
    ]


def test_confirmacao_decidida_pela_taxa_do_banco(db):
    with storage.conectar() as conn:
        conn.execute("UPDATE areas SET taxa = 0 WHERE id = 'churrasqueira'")
        conn.execute("UPDATE areas SET taxa = 10 WHERE id = 'quadra'")
        conn.commit()
    ctx = contexto_reservas("101")
    assert run(reservar_area("churrasqueira", "2030-06-01", ctx))["status"] == "success"
    assert ctx.confirmacoes_pedidas == []
    ctx = contexto_reservas("101")
    assert run(reservar_area("quadra", "2030-06-02", ctx))["status"] == "pending"
    assert len(ctx.confirmacoes_pedidas) == 1


def test_reservar_data_ocupada_por_outro_sem_vazar(db):
    ctx = contexto_reservas("101")
    resposta = run(reservar_area("salao-de-festas", "2030-03-16", ctx))
    assert resposta["message"] == MSG_OCUPADA
    assert ctx.confirmacoes_pedidas == []
    sem_vazamento(resposta)
    assert ("RSV-4821", "salao-de-festas", "2030-03-16") in ativas("302")


def test_reservar_aprovado_data_ocupada_sem_vazar(db):
    antes = total_ativas()
    resposta = run(reservar_area("salao-de-festas", "2030-03-16", contexto_reservas("101", True)))
    assert resposta["message"] == MSG_OCUPADA_APOS_APROVACAO
    assert total_ativas() == antes
    sem_vazamento(resposta)


def test_reservar_quadra_ocupada_pelo_proprio(db):
    resposta = run(reservar_area("quadra", "2030-03-09", contexto_reservas("101")))
    assert resposta["message"] == MSG_OCUPADA
    assert "codigo" not in resposta


def test_reservar_area_invalida_nao_grava(db):
    antes = total_ativas()
    resposta = run(reservar_area("piscina", "2030-04-06", contexto_reservas("101")))
    assert resposta["message"] == MSG_AREA_INVALIDA
    assert total_ativas() == antes


@pytest.mark.parametrize("data", ["2030-4-6", "2030-02-30", "amanhã"])
def test_reservar_data_invalida_nao_grava(db, data):
    ctx = contexto_reservas("101")
    antes = total_ativas()
    resposta = run(reservar_area("salao-de-festas", data, ctx))
    assert resposta["message"] == MSG_DATA_INVALIDA
    assert total_ativas() == antes
    assert ctx.confirmacoes_pedidas == []


def test_reservar_erro_da_f02_repassa_mensagem(db, monkeypatch):
    monkeypatch.setattr(
        repo,
        "criar_reserva",
        lambda *a, **k: storage.ResultadoReserva(status="erro", mensagem="falhou feio"),
    )
    resposta = run(reservar_area("quadra", "2030-04-06", contexto_reservas("101")))
    assert resposta == {"status": "error", "message": "falhou feio"}


def test_aprovacoes_concorrentes_so_uma_grava(db):
    async def duas():
        return await asyncio.gather(
            reservar_area("salao-de-festas", "2030-05-11", contexto_reservas("101", True)),
            reservar_area("salao-de-festas", "2030-05-11", contexto_reservas("201", True)),
        )

    respostas = run(duas())
    assert sorted(r["status"] for r in respostas) == ["error", "success"]
    perdedora = next(r for r in respostas if r["status"] == "error")
    assert perdedora["message"] == MSG_OCUPADA_APOS_APROVACAO
    soma = [x for apto in ("101", "201") for x in ativas(apto) if x[2] == "2030-05-11"]
    assert len(soma) == 1


# --- cancelar ---


def test_cancelar_por_area_data_sem_confirmacao(db):
    ctx = contexto_reservas("101")
    resposta = run(cancelar_reserva(ctx, area="quadra", data="2030-03-09"))
    assert resposta["status"] == "success"
    assert ctx.confirmacoes_pedidas == []
    assert status_de("RSV-1377") == "cancelada"


def test_cancelar_por_codigo(db):
    ctx = contexto_reservas("101")
    assert run(cancelar_reserva(ctx, codigo=" rsv-1377 "))["status"] == "success"
    segunda = run(cancelar_reserva(ctx, codigo="RSV-1377"))
    assert segunda["message"] == MSG_NAO_ENCONTREI_CODIGO


def test_cancelar_reserva_alheia_mesma_mensagem_de_inexistente(db):
    ctx = contexto_reservas("101")
    alheia = run(cancelar_reserva(ctx, area="salao-de-festas", data="2030-03-16"))
    inexistente = run(cancelar_reserva(ctx, area="salao-de-festas", data="2030-03-17"))
    assert alheia == inexistente
    assert alheia["message"] == MSG_NAO_ENCONTREI_AREA_DATA
    alheia_c = run(cancelar_reserva(ctx, codigo="RSV-4821"))
    inexistente_c = run(cancelar_reserva(ctx, codigo="RSV-ZZZZZZ"))
    assert alheia_c == inexistente_c
    for r in (alheia, alheia_c):
        sem_vazamento(r)
    assert ("RSV-4821", "salao-de-festas", "2030-03-16") in ativas("302")


def test_cancelar_sem_identificacao(db):
    ctx = contexto_reservas("101")
    assert run(cancelar_reserva(ctx))["message"] == MSG_IDENTIFICACAO_CANCELAMENTO
    assert run(cancelar_reserva(ctx, area="quadra"))["message"] == MSG_IDENTIFICACAO_CANCELAMENTO


def test_cancelar_data_ou_area_invalida(db):
    ctx = contexto_reservas("101")
    assert run(cancelar_reserva(ctx, area="piscina", data="2030-03-09"))["message"] == (
        MSG_AREA_INVALIDA
    )
    assert run(cancelar_reserva(ctx, area="quadra", data="2030/03/09"))["message"] == (
        MSG_DATA_INVALIDA
    )


def test_cancelar_banco_bloqueado_erro_controlado(db, monkeypatch):
    def bloqueado(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "cancelar_reserva", bloqueado)
    resposta = run(cancelar_reserva(contexto_reservas("101"), area="quadra", data="2030-03-09"))
    assert resposta == {"status": "error", "message": repo.MSG_BANCO_OCUPADO}


# --- callback ---


def test_callback_erro_inesperado_mensagem_generica():
    esperado = {
        "status": "error",
        "message": "Ocorreu um erro inesperado ao processar sua solicitação.",
    }
    for erro in (sqlite3.DatabaseError("tabela x corrompida"), ApartamentoAusenteError("sem apto")):
        assert handle_tool_error_solicitacao(reservas.reservar_area, {}, None, erro) == esperado
