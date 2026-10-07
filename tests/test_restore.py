import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from aurora.main import create_app
from aurora.storage import (
    ErroRestauracao,
    Reserva,
    autorizar_visitante,
    cancelar_reserva,
    carga,
    conectar,
    criar_reserva,
    inicializar_banco,
    listar_reservas_ativas,
    listar_visitantes,
    restaurar,
)
from tests.conftest import ROOT

DADOS = ROOT / "dados"


def _snapshot(db):
    with conectar(db) as c:
        return {
            t: c.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
            for t in ("apartamentos", "areas", "reservas", "visitantes")
        }


def _hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DADOS.iterdir())}


def test_restaurar_carrega_estado_inicial(settings_env, tmp_path):
    db = tmp_path / "novo.db"
    resumo = restaurar(db, DADOS)
    assert (resumo.apartamentos, resumo.areas, resumo.reservas, resumo.visitantes) == (6, 3, 3, 2)
    assert listar_reservas_ativas("101", db) == [Reserva("RSV-1377", "quadra", "2030-03-09")]
    assert [v.para_dict() for v in listar_visitantes("302", db)] == [
        {"nome": "Marina Duarte", "data": "2030-03-16"}
    ]
    with conectar(db) as c:
        assert c.execute("SELECT COUNT(*) FROM reservas WHERE status != 'ativa'").fetchone()[0] == 0


def test_restaurar_desfaz_mudancas_do_fluxo(db):
    inicial = _snapshot(db)
    cancelar_reserva("101", "quadra", "2030-03-09")
    assert criar_reserva("101", "quadra", "2030-04-06").status == "criada"
    assert autorizar_visitante("101", "Joana Ribeiro", "2030-04-21").status == "autorizado"
    restaurar(db, DADOS)
    assert _snapshot(db) == inicial


def test_restaurar_nao_altera_dados(db):
    antes = _hashes()
    restaurar(db, DADOS)
    criar_reserva("101", "quadra", "2030-04-06")
    autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    restaurar(db, DADOS)
    assert _hashes() == antes


def test_json_invalido_aborta_sem_alterar_banco(db, dados_copia):
    criar_reserva("101", "quadra", "2030-04-06")
    antes = _snapshot(db)
    (dados_copia / "reservas.json").write_text('[\n  {"codigo": "RSV-1",\n', encoding="utf-8")
    with pytest.raises(ErroRestauracao) as exc:
        restaurar(db, dados_copia)
    assert str(exc.value) == (
        "Falha ao restaurar: dados/reservas.json inválido (linha 3). Nenhuma alteração aplicada."
    )
    assert _snapshot(db) == antes


def test_arquivo_ausente_aborta_sem_alterar_banco(db, dados_copia):
    antes = _snapshot(db)
    (dados_copia / "areas.json").unlink()
    with pytest.raises(ErroRestauracao, match="dados/areas.json ausente"):
        restaurar(db, dados_copia)
    assert _snapshot(db) == antes


def test_registro_invalido_aborta(db, dados_copia):
    antes = _snapshot(db)
    arq = dados_copia / "reservas.json"
    original = arq.read_text(encoding="utf-8")
    arq.write_text(original.replace("2030-03-09", "2030/03/09"), encoding="utf-8")
    with pytest.raises(ErroRestauracao, match=r"reservas.json inválido \(registro 1: .*AAAA-MM-DD"):
        restaurar(db, dados_copia)
    arq.write_text(original.replace('"quadra"', '"piscina"'), encoding="utf-8")
    with pytest.raises(ErroRestauracao, match=r"registro 1: área 'piscina' inexistente"):
        restaurar(db, dados_copia)
    assert _snapshot(db) == antes


def test_restaurar_preserva_tabelas_de_sessao(db, tmp_path):
    with conectar(db) as c:
        c.execute("CREATE TABLE sessoes_teste (id TEXT)")
        c.execute("INSERT INTO sessoes_teste VALUES ('s1')")
    irmao = tmp_path / "aurora_sessions.db"
    irmao.write_bytes(b"sessoes")
    antes = (hashlib.sha256(irmao.read_bytes()).hexdigest(), irmao.stat().st_mtime_ns)
    restaurar(db, DADOS)
    with conectar(db) as c:
        assert c.execute("SELECT id FROM sessoes_teste").fetchall() == [("s1",)]
    assert (hashlib.sha256(irmao.read_bytes()).hexdigest(), irmao.stat().st_mtime_ns) == antes


def test_restaurar_rollback_em_falha_no_meio(db, monkeypatch):
    criar_reserva("101", "quadra", "2030-04-06")
    antes = _snapshot(db)

    def falha(conn, visitantes):
        raise sqlite3.DatabaseError("falha forçada")

    monkeypatch.setattr(carga, "_inserir_visitantes", falha)
    with pytest.raises(sqlite3.DatabaseError):
        restaurar(db, DADOS)
    assert _snapshot(db) == antes


def test_main_imprime_resumo(db, capsys):
    assert carga.main(DADOS) == 0
    out = capsys.readouterr().out.strip()
    assert out == "Restaurado: 6 apartamentos, 3 áreas, 3 reservas, 2 visitantes."


def test_main_falha_retorna_exit_1(db, dados_copia, capsys):
    antes = _snapshot(db)
    (dados_copia / "visitantes.json").write_text("{", encoding="utf-8")
    assert carga.main(dados_copia) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Falha ao restaurar: dados/visitantes.json inválido (linha 1)" in captured.err
    assert _snapshot(db) == antes


def test_lifespan_cria_e_popula_banco(settings_env, tmp_path, monkeypatch):
    db = tmp_path / "sub" / "life.db"
    monkeypatch.setenv("AURORA_DB_PATH", str(db))
    monkeypatch.chdir(ROOT)
    from aurora.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()):
        assert db.exists()
        assert "RSV-1377" in [r.codigo for r in listar_reservas_ativas("101")]


def test_reinicio_preserva_dados_sem_restaurar(settings_env, monkeypatch):
    monkeypatch.chdir(ROOT)
    with TestClient(create_app()):
        assert cancelar_reserva("101", "quadra", "2030-03-09")
        nova = criar_reserva("101", "quadra", "2030-04-06")
        autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    with TestClient(create_app()):
        codigos = [r.codigo for r in listar_reservas_ativas("101")]
        assert codigos == [nova.codigo]
        assert [v.nome for v in listar_visitantes("101")] == ["Joana Ribeiro"]
    assert inicializar_banco(dados_dir=DADOS) is False


def test_inicializar_banco_nao_recarrega_banco_populado(db):
    cancelar_reserva("101", "quadra", "2030-03-09")
    assert inicializar_banco(db, DADOS) is False
    assert listar_reservas_ativas("101") == []


def test_inicializar_banco_popula_banco_vazio(settings_env, tmp_path):
    assert inicializar_banco(tmp_path / "v.db", DADOS) is True
