import sqlite3

import pytest

from aurora.storage import (
    apartamento_da_sessao_id,
    conectar,
    listar_pendentes,
    registrar_pendencia,
    registrar_sessao,
    restaurar,
)
from aurora.storage.db import criar_schema
from tests.conftest import ROOT


@pytest.fixture
def banco(db):
    with conectar(db) as conn:
        criar_schema(conn)
    return db


def test_schema_tem_sessoes_e_confirmacoes(banco):
    with conectar(banco) as conn:
        nomes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert {"sessoes", "confirmacoes", "ix_confirmacoes_sessao_status"} <= nomes
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_registrar_e_ler_apartamento_da_sessao(banco):
    registrar_sessao("s1", "101")
    assert apartamento_da_sessao_id("s1") == "101"
    assert apartamento_da_sessao_id("desconhecida") is None


def test_registrar_pendencia_idempotente(banco):
    registrar_sessao("s1", "101")
    registrar_pendencia("c1", "s1", "reservar_area", {"area": "quadra"})
    registrar_pendencia("c1", "s1", "reservar_area", {"area": "quadra"})
    with conectar(banco) as conn:
        assert conn.execute("SELECT COUNT(*) FROM confirmacoes").fetchone()[0] == 1


def test_pendencia_exige_sessao_conhecida(banco):
    with pytest.raises(sqlite3.IntegrityError):
        registrar_pendencia("c1", "inexistente", "reservar_area", {})


def test_listar_pendentes_so_da_sessao_e_status_pendente(banco):
    registrar_sessao("s1", "101")
    registrar_sessao("s2", "201")
    registrar_pendencia("c1", "s1", "reservar_area", {"n": 1})
    registrar_pendencia("c2", "s1", "autorizar_visitante", {"n": 2})
    registrar_pendencia("c3", "s1", "reservar_area", {"n": 3})
    registrar_pendencia("c4", "s2", "reservar_area", {"n": 4})
    with conectar(banco) as conn:
        conn.execute("UPDATE confirmacoes SET status = 'respondida' WHERE id = 'c2'")
    assert [p.id for p in listar_pendentes("s1")] == ["c1", "c3"]
    assert [p.id for p in listar_pendentes("s2")] == ["c4"]
    assert listar_pendentes("vazia") == []


def test_detalhes_roundtrip_json(banco):
    registrar_sessao("s1", "101")
    detalhes = {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}
    registrar_pendencia("c1", "s1", "reservar_area", detalhes)
    (pendente,) = listar_pendentes("s1")
    assert pendente.detalhes == detalhes
    assert pendente.para_dict() == {"id": "c1", "acao": "reservar_area", "detalhes": detalhes}


def test_restauracao_nao_apaga_sessoes_nem_confirmacoes(banco):
    registrar_sessao("s1", "101")
    registrar_pendencia("c1", "s1", "reservar_area", {"area": "quadra"})
    restaurar(banco, ROOT / "dados")
    assert apartamento_da_sessao_id("s1") == "101"
    assert [p.id for p in listar_pendentes("s1")] == ["c1"]
