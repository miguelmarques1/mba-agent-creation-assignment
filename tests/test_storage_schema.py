import sqlite3

import pytest

from aurora.storage.db import conectar, criar_schema, transacao_escrita


@pytest.fixture
def conn(tmp_path):
    with conectar(tmp_path / "x.db") as c:
        criar_schema(c)
        c.execute("INSERT INTO apartamentos VALUES ('101', 'A')")
        c.execute("INSERT INTO areas VALUES ('quadra', 'Quadra', 0)")
        yield c


def test_conectar_aplica_pragmas(tmp_path):
    caminho = tmp_path / "novo" / "sub" / "x.db"
    with conectar(caminho) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert caminho.parent.is_dir()


def test_schema_tem_indice_unico_parcial(conn):
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'ux_reservas_area_data_ativa'"
    ).fetchone()[0]
    assert "UNIQUE" in sql.upper()
    assert "WHERE status = 'ativa'" in sql
    lista = conn.execute("PRAGMA index_list(reservas)").fetchall()
    assert any(r[1] == "ux_reservas_area_data_ativa" and r[2] == 1 and r[4] == 1 for r in lista)


def test_schema_idempotente(conn):
    criar_schema(conn)
    criar_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_check_rejeita_data_fora_do_formato(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reservas VALUES ('RSV-AAAAAA', '101', 'quadra', '06/04/2030', 'ativa')"
        )


def test_check_rejeita_status_invalido(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO reservas VALUES ('RSV-AAAAAA', '101', 'quadra', '2030-04-06', 'apagada')"
        )


def test_transacao_escrita_faz_rollback(conn):
    with pytest.raises(RuntimeError), transacao_escrita(conn):
        conn.execute("INSERT INTO apartamentos VALUES ('102', 'B')")
        raise RuntimeError("falha")
    assert conn.execute("SELECT COUNT(*) FROM apartamentos WHERE numero = '102'").fetchone()[0] == 0
