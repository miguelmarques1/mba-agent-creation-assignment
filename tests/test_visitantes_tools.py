import asyncio
import functools
import inspect
import json
import sqlite3

import pytest
from google.adk.tools import FunctionTool
from google.adk.tools.tool_confirmation import ToolConfirmation

from aurora import storage
from aurora.agents.callbacks import handle_tool_error
from aurora.storage import repositorio
from aurora.tools import visitantes as v
from tests.visitantes_utils import FakeConfirmContext

NOME, DATA = "Joana Ribeiro", "2030-04-21"
APROVADO = ToolConfirmation(confirmed=True)
NEGADO = ToolConfirmation(confirmed=False)


def rodar(coro):
    return asyncio.run(coro)


def autorizar(ctx, nome=NOME, data=DATA):
    return rodar(v.autorizar_visitante(nome, data, ctx))


def listar(apto):
    return [x.para_dict() for x in storage.listar_visitantes(apto)]


def total():
    with storage.conectar() as conn:
        return conn.execute("SELECT COUNT(*) FROM visitantes").fetchone()[0]


def test_tools_sem_parametro_apartamento():
    assert list(inspect.signature(v.autorizar_visitante).parameters) == [
        "nome",
        "data",
        "tool_context",
    ]
    assert list(inspect.signature(v.listar_meus_visitantes).parameters) == ["tool_context"]
    esquema = FunctionTool(v.autorizar_visitante)._get_declaration().parameters_json_schema
    assert set(esquema["properties"]) == {"nome", "data"}
    vazio = FunctionTool(v.listar_meus_visitantes)._get_declaration()
    assert not (vazio.parameters_json_schema or {}).get("properties")
    for func in v.TOOLS_VISITANTES:
        for nome in inspect.signature(func).parameters:
            assert "apart" not in nome and "confirm" not in nome


def test_argumento_apartamento_extra_e_ignorado(db):
    ctx = FakeConfirmContext("101")
    ferramenta = FunctionTool(v.autorizar_visitante)
    antes = total()
    retorno = rodar(
        ferramenta.run_async(
            args={"nome": NOME, "data": DATA, "apartamento": "302"}, tool_context=ctx
        )
    )
    assert retorno["status"] == "pending"
    assert ctx.pedidos[0][1]["detalhes"] == {"nome": NOME, "data": DATA}
    assert total() == antes


def test_autorizar_sempre_pede_confirmacao(db):
    ctx = FakeConfirmContext("101")
    retorno = autorizar(ctx)
    assert retorno["status"] == "pending"
    assert ctx.pedidos == [
        (
            "Confirmar a autorização de entrada de Joana Ribeiro em 2030-04-21?",
            {"acao": "autorizar_visitante", "detalhes": {"nome": NOME, "data": DATA}},
        )
    ]
    assert listar("101") == []


def test_autorizar_sem_confirmacao_nunca_grava(db):
    ctx = FakeConfirmContext("101")
    antes = total()
    for _ in range(3):
        autorizar(ctx)
    assert len(ctx.pedidos) == 3
    assert total() == antes


def test_autorizar_apos_aprovacao_grava(db):
    ctx = FakeConfirmContext("101", APROVADO)
    retorno = autorizar(ctx)
    assert retorno == {
        "status": "success",
        "message": "Entrada de Joana Ribeiro autorizada para 2030-04-21.",
        "visitante": {"nome": NOME, "data": DATA},
    }
    assert listar("101") == [{"nome": NOME, "data": DATA}]
    assert ctx.pedidos == []


def test_autorizar_negado_nao_grava(db):
    antes = total()
    retorno = autorizar(FakeConfirmContext("101", NEGADO))
    assert retorno["status"] == "cancelled"
    assert retorno["message"] == "Autorização não confirmada; ninguém foi liberado."
    assert total() == antes


def test_autorizar_grava_no_apartamento_da_sessao(db):
    autorizar(FakeConfirmContext("201", APROVADO))
    assert {"nome": NOME, "data": DATA} in listar("201")
    assert listar("101") == []
    assert all(x["nome"] != NOME for x in listar("302"))


def test_autorizar_duplicado_nao_pede_confirmacao(db):
    ctx = FakeConfirmContext("302")
    antes = total()
    for nome in ("Marina Duarte", " marina  duarte "):
        retorno = autorizar(ctx, nome, "2030-03-16")
        assert retorno == {"status": "success", "message": v.MSG_JA_AUTORIZADO}
    assert ctx.pedidos == []
    assert total() == antes


def test_autorizar_duplicado_de_outro_apartamento_pede_confirmacao(db):
    ctx = FakeConfirmContext("101")
    assert autorizar(ctx, "Marina Duarte", "2030-03-16")["status"] == "pending"


def test_autorizar_aprovado_com_duplicata_gravada_em_paralelo(db, monkeypatch):
    async def nao_existe(*args):
        return False

    monkeypatch.setattr(v, "_ja_autorizado", nao_existe)
    autorizar(FakeConfirmContext("101", APROVADO))
    retorno = autorizar(FakeConfirmContext("101", APROVADO))
    assert retorno == {"status": "success", "message": v.MSG_JA_AUTORIZADO}
    assert listar("101") == [{"nome": NOME, "data": DATA}]


@pytest.mark.parametrize(
    "nome, mensagem",
    [
        ("", v.MSG_NOME_INVALIDO),
        ("   ", v.MSG_NOME_INVALIDO),
        ("A", v.MSG_NOME_INVALIDO),
        ("x" * 81, v.MSG_NOME_LONGO),
    ],
)
def test_autorizar_valida_nome(db, nome, mensagem):
    ctx = FakeConfirmContext("101")
    antes = total()
    assert autorizar(ctx, nome) == {"status": "error", "message": mensagem}
    assert ctx.pedidos == [] and total() == antes


@pytest.mark.parametrize("data", ["21/04/2030", "2030-02-30", "2030-4-1"])
def test_autorizar_valida_data(db, data):
    ctx = FakeConfirmContext("101")
    assert autorizar(ctx, data=data) == {"status": "error", "message": v.MSG_DATA_INVALIDA}
    assert ctx.pedidos == []


def test_autorizar_revalida_na_reexecucao(db):
    antes = total()
    retorno = autorizar(FakeConfirmContext("101", APROVADO), data="2030-02-30")
    assert retorno["status"] == "error"
    assert total() == antes


def test_normalizacao_igual_a_do_repositorio(db):
    for nome in ("  Ana   Paula  ", "Ana\tPaula", "Ana Paula"):
        assert v._normalizar_nome(nome) == repositorio._normalizar_nome(nome)
    autorizar(FakeConfirmContext("101", APROVADO), "  Ana   Paula  ")
    assert {"nome": "Ana Paula", "data": DATA} in listar("101")
    pedido = FakeConfirmContext("101")
    autorizar(pedido, "  Ana   Paula  ", "2030-05-01")
    assert pedido.pedidos[0][1]["detalhes"]["nome"] == "Ana Paula"


def test_listar_meus_visitantes_so_da_sessao(db):
    autorizar(FakeConfirmContext("101", APROVADO))
    retorno = rodar(v.listar_meus_visitantes(FakeConfirmContext("101")))
    assert retorno["visitantes"] == [{"nome": NOME, "data": DATA}]
    bruto = json.dumps(retorno)
    assert "Marina Duarte" not in bruto and "Paulo Nogueira" not in bruto


def test_listar_meus_visitantes_vazio(db):
    retorno = rodar(v.listar_meus_visitantes(FakeConfirmContext("101")))
    assert retorno["visitantes"] == []
    assert retorno["message"] == v.MSG_SEM_VISITANTES


def test_retornos_sem_apartamento(db):
    retornos = [
        autorizar(FakeConfirmContext("101")),
        autorizar(FakeConfirmContext("101", APROVADO)),
        autorizar(FakeConfirmContext("101", NEGADO)),
        autorizar(FakeConfirmContext("101", APROVADO)),
        autorizar(FakeConfirmContext("101"), "", DATA),
        rodar(v.listar_meus_visitantes(FakeConfirmContext("101"))),
    ]
    for retorno in retornos:
        assert "apartamento" not in retorno
        bruto = json.dumps(retorno)
        assert not any(n in bruto for n in ("201", "302"))


def test_state_sem_apartamento_falha_sem_efeito(db):
    ctx = FakeConfirmContext(apartamento=None, confirmacao=APROVADO)
    antes = total()
    with pytest.raises(RuntimeError):
        autorizar(ctx)
    with pytest.raises(RuntimeError):
        rodar(v.listar_meus_visitantes(ctx))
    assert ctx.pedidos == [] and total() == antes


def test_tools_nao_escrevem_state(db):
    ctx = FakeConfirmContext("101", APROVADO)
    autorizar(ctx)
    rodar(v.listar_meus_visitantes(ctx))
    assert ctx.state == {"apartamento": "101"}


def test_erro_de_banco_vira_mensagem_generica(db, monkeypatch):
    def falha(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked: detalhe interno")

    monkeypatch.setattr(storage, "listar_visitantes", falha)
    ctx = FakeConfirmContext("101")
    callback = functools.partial(handle_tool_error, mensagem_generica=v.MSG_ERRO_INESPERADO)
    with pytest.raises(sqlite3.OperationalError) as info:
        rodar(v.listar_meus_visitantes(ctx))
    retorno = callback(v.listar_meus_visitantes, {}, ctx, info.value)
    assert retorno == {
        "status": "error",
        "message": "Ocorreu um erro inesperado ao processar sua solicitação.",
    }


def test_resultado_erro_do_repositorio(db, monkeypatch):
    monkeypatch.setattr(
        storage,
        "autorizar_visitante",
        lambda *a, **k: storage.ResultadoVisitante("erro", mensagem="Falha genérica da F02."),
    )
    retorno = autorizar(FakeConfirmContext("101", APROVADO))
    assert retorno == {"status": "error", "message": "Falha genérica da F02."}
