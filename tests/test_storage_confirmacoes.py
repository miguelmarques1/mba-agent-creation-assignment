import sqlite3
import threading

import pytest

from aurora.storage import (
    conectar,
    expirar_pendencias,
    listar_pendentes,
    obter_pendente,
    registrar_pendencia,
    registrar_sessao,
    responder_pendencia,
)
from aurora.storage.db import criar_schema

DDL_V2 = """
CREATE TABLE sessoes (
    session_id  TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    criada_em   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE confirmacoes (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessoes(session_id),
    acao          TEXT NOT NULL,
    detalhes      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'respondida')),
    confirmado    INTEGER CHECK (confirmado IN (0, 1)),
    criada_em     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    respondida_em TEXT
);
CREATE INDEX ix_confirmacoes_sessao_status ON confirmacoes(session_id, status, criada_em);
PRAGMA user_version = 2;
"""


@pytest.fixture
def banco(db):
    with conectar(db) as conn:
        criar_schema(conn)
    registrar_sessao("s1", "101")
    registrar_sessao("s2", "201")
    return db


def _linha(banco, confirmacao_id):
    with conectar(banco) as conn:
        return conn.execute(
            "SELECT status, confirmado, respondida_em FROM confirmacoes WHERE id = ?",
            (confirmacao_id,),
        ).fetchone()


def test_schema_v3_confirmacoes(banco):
    with conectar(banco) as conn:
        colunas = {r[1] for r in conn.execute("PRAGMA table_info(confirmacoes)")}
        assert {"agente", "chamada_original_id"} <= colunas
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    registrar_pendencia("c1", "s1", "reservar_area", {})
    with conectar(banco) as conn:
        conn.execute("UPDATE confirmacoes SET status = 'expirada' WHERE id = 'c1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE confirmacoes SET status = 'outro' WHERE id = 'c1'")


def test_migracao_v2_para_v3_preserva_linhas(tmp_path):
    caminho = tmp_path / "v2.db"
    with conectar(caminho) as conn:
        conn.executescript(DDL_V2)
        conn.execute("INSERT INTO sessoes (session_id, apartamento) VALUES ('s1', '101')")
        conn.execute(
            "INSERT INTO confirmacoes (id, session_id, acao, detalhes) "
            "VALUES ('c1', 's1', 'reservar_area', '{\"a\": 1}')"
        )
    with conectar(caminho) as conn:
        criar_schema(conn)
        linha = conn.execute(
            "SELECT id, status, agente, chamada_original_id, detalhes FROM confirmacoes"
        ).fetchone()
        assert linha == ("c1", "pendente", None, None, '{"a": 1}')
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        criar_schema(conn)  # segunda chamada não faz nada
        assert conn.execute("SELECT COUNT(*) FROM confirmacoes").fetchone()[0] == 1
        conn.execute("UPDATE confirmacoes SET status = 'expirada'")
    assert [p.id for p in listar_pendentes("s1", caminho)] == []


def test_responder_pendencia_uma_vez(banco):
    registrar_pendencia("c1", "s1", "reservar_area", {})
    assert responder_pendencia("c1", "s1", True) is True
    status, confirmado, respondida_em = _linha(banco, "c1")
    assert (status, confirmado) == ("respondida", 1)
    assert respondida_em
    assert responder_pendencia("c1", "s1", False) is False
    assert _linha(banco, "c1") == (status, confirmado, respondida_em)


def test_responder_pendencia_negada_grava_zero(banco):
    registrar_pendencia("c1", "s1", "reservar_area", {})
    assert responder_pendencia("c1", "s1", False) is True
    assert _linha(banco, "c1")[:2] == ("respondida", 0)


def test_responder_pendencia_outra_sessao(banco):
    registrar_pendencia("c1", "s1", "reservar_area", {})
    assert responder_pendencia("c1", "s2", True) is False
    assert _linha(banco, "c1")[0] == "pendente"


def test_responder_pendencia_concorrente_threads(banco):
    registrar_pendencia("c1", "s1", "reservar_area", {})
    resultados = []
    barreira = threading.Barrier(2)

    def tentar():
        barreira.wait()
        resultados.append(responder_pendencia("c1", "s1", True))

    threads = [threading.Thread(target=tentar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(resultados) == [False, True]


def test_obter_pendente_filtra_status_e_sessao(banco):
    registrar_pendencia("c1", "s1", "reservar_area", {"x": 1}, "reservas", "fc-1")
    registrar_pendencia("c2", "s1", "reservar_area", {})
    registrar_pendencia("c3", "s1", "reservar_area", {}, "reservas")
    responder_pendencia("c2", "s1", True)
    expirar_pendencias("s1", "visitantes")
    c1 = obter_pendente("c1", "s1")
    assert c1 is None  # expirada junto com c3 (agente reservas != visitantes)
    assert obter_pendente("c2", "s1") is None  # respondida
    assert obter_pendente("c3", "s1") is None  # expirada
    assert obter_pendente("c1", "s2") is None
    registrar_pendencia("c4", "s1", "autorizar_visitante", {"n": 1}, "visitantes", "fc-4")
    c4 = obter_pendente("c4", "s1")
    assert (c4.acao, c4.detalhes, c4.status, c4.agente, c4.chamada_original_id) == (
        "autorizar_visitante",
        {"n": 1},
        "pendente",
        "visitantes",
        "fc-4",
    )
    assert obter_pendente("c4", "s2") is None


def test_expirar_pendencias_so_de_outros_agentes(banco):
    registrar_pendencia("r1", "s1", "reservar_area", {}, "reservas")
    registrar_pendencia("v1", "s1", "autorizar_visitante", {}, "visitantes")
    registrar_pendencia("l1", "s1", "reservar_area", {})  # legado: agente NULL
    registrar_pendencia("o1", "s2", "reservar_area", {}, "reservas")
    assert expirar_pendencias("s1", "visitantes") == ["r1"]
    assert _linha(banco, "r1")[0] == "expirada"
    assert _linha(banco, "r1")[1] is None
    assert _linha(banco, "r1")[2]
    assert [p.id for p in listar_pendentes("s1")] == ["v1", "l1"]
    assert _linha(banco, "o1")[0] == "pendente"
    assert expirar_pendencias("s1", "visitantes") == []


def test_listar_pendentes_ignora_expiradas(banco):
    registrar_pendencia("r1", "s1", "reservar_area", {}, "reservas")
    expirar_pendencias("s1", "assistente")
    assert listar_pendentes("s1") == []
