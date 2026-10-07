import re
import sqlite3
import threading

import pytest

from aurora.storage import (
    area_ocupada,
    cancelar_reserva,
    cancelar_reserva_por_codigo,
    conectar,
    criar_reserva,
    listar_reservas_ativas,
    obter_area,
)

INICIAIS = ("RSV-1377", "RSV-4821", "RSV-2950")


def _ativas(db, area, data):
    with conectar(db) as c:
        return c.execute(
            "SELECT COUNT(*) FROM reservas WHERE area=? AND data=? AND status='ativa'", (area, data)
        ).fetchone()[0]


def test_obter_area_taxa_vem_do_banco(db):
    assert obter_area("salao-de-festas").taxa == 150.0
    assert obter_area("churrasqueira").taxa == 80.0
    assert obter_area("quadra").taxa == 0.0
    assert obter_area("piscina") is None


def test_criar_reserva_gera_codigo_formato(db):
    r = criar_reserva("101", "quadra", "2030-04-06")
    assert r.status == "criada"
    assert re.fullmatch(r"RSV-[A-Z0-9]{6}", r.codigo)
    assert r.codigo in [x.codigo for x in listar_reservas_ativas("101")]


def test_segunda_reserva_ativa_ocupada_sem_excecao(db):
    r = criar_reserva("101", "salao-de-festas", "2030-03-16")
    assert r.status == "ocupada"
    assert _ativas(db, "salao-de-festas", "2030-03-16") == 1
    assert [x.codigo for x in listar_reservas_ativas("302")] == ["RSV-4821"]


def test_insert_direto_viola_indice_parcial(db):
    with conectar(db) as c, pytest.raises(sqlite3.IntegrityError) as exc:
        c.execute(
            "INSERT INTO reservas VALUES ('RSV-ZZZZZZ','101','salao-de-festas','2030-03-16','ativa')"
        )
    assert exc.value.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE"


def test_reservas_concorrentes_apenas_uma_vence(db):
    for rodada in range(20):
        data = f"2030-05-{rodada + 1:02d}"
        barreira = threading.Barrier(2)
        resultados, erros = [], []

        def tentar(apto, data=data, barreira=barreira, resultados=resultados, erros=erros):
            try:
                barreira.wait()
                resultados.append(criar_reserva(apto, "salao-de-festas", data).status)
            except Exception as exc:  # noqa: BLE001
                erros.append(exc)

        threads = [threading.Thread(target=tentar, args=(a,)) for a in ("101", "201")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not erros
        assert sorted(resultados) == ["criada", "ocupada"]
        assert _ativas(db, "salao-de-festas", data) == 1


def test_cancelar_nao_apaga_e_codigo_continua_ocupado(db):
    assert cancelar_reserva("101", "quadra", "2030-03-09") is True
    with conectar(db) as c:
        assert c.execute("SELECT status FROM reservas WHERE codigo='RSV-1377'").fetchone() == (
            "cancelada",
        )
        with pytest.raises(sqlite3.IntegrityError) as exc:
            c.execute(
                "INSERT INTO reservas VALUES ('RSV-1377','101','quadra','2030-07-01','ativa')"
            )
        assert exc.value.sqlite_errorname == "SQLITE_CONSTRAINT_PRIMARYKEY"
    assert listar_reservas_ativas("101") == []


def test_reserva_cancelada_libera_data(db):
    cancelar_reserva("101", "quadra", "2030-03-09")
    r = criar_reserva("101", "quadra", "2030-03-09")
    assert r.status == "criada" and r.codigo != "RSV-1377"


def test_cancelar_reserva_de_outro_apartamento(db):
    assert cancelar_reserva("101", "salao-de-festas", "2030-03-16") is False
    assert cancelar_reserva_por_codigo("101", "RSV-4821") is False
    assert [x.codigo for x in listar_reservas_ativas("302")] == ["RSV-4821"]


def test_cancelar_por_codigo(db):
    assert cancelar_reserva_por_codigo("101", "RSV-1377") is True
    assert cancelar_reserva_por_codigo("101", "RSV-1377") is False


def test_codigos_gerados_unicos_e_distintos_dos_iniciais(db):
    codigos = []
    for i in range(50):
        r = criar_reserva("101", "quadra", f"2031-{i // 25 + 1:02d}-{i % 25 + 1:02d}")
        assert r.status == "criada"
        codigos.append(r.codigo)
    assert len(set(codigos)) == 50
    assert not set(codigos) & set(INICIAIS)


def test_colisao_de_codigo_tenta_de_novo(db):
    codigos = iter(["RSV-1377", "RSV-1377", "RSV-AAAAAA"])
    r = criar_reserva("101", "quadra", "2030-04-06", gerar=lambda: next(codigos))
    assert r.status == "criada" and r.codigo == "RSV-AAAAAA"


def test_colisao_de_codigo_esgota_5_tentativas(db):
    cancelar_reserva("101", "quadra", "2030-03-09")
    chamadas = []

    def gerar():
        chamadas.append(1)
        return "RSV-1377"

    r = criar_reserva("101", "quadra", "2030-04-06", gerar=gerar)
    assert r.status == "erro"
    assert r.mensagem == "Não foi possível gerar um código de reserva; tente novamente."
    assert len(chamadas) == 5
    assert listar_reservas_ativas("101") == []


def test_banco_bloqueado_retorna_erro_controlado(db):
    bloqueio = sqlite3.connect(db, isolation_level=None)
    bloqueio.execute("BEGIN IMMEDIATE")
    try:
        r = criar_reserva("101", "quadra", "2030-04-06", busy_timeout_ms=100)
    finally:
        bloqueio.execute("ROLLBACK")
        bloqueio.close()
    assert r.status == "erro" and r.mensagem
    assert [x.data for x in listar_reservas_ativas("101")] == ["2030-03-09"]


def test_criar_reserva_valida_area_e_data(db):
    assert criar_reserva("101", "piscina", "2030-04-06").status == "area_invalida"
    assert criar_reserva("101", "quadra", "2030-4-6").status == "data_invalida"
    assert criar_reserva("101", "quadra", "2030-02-30").status == "data_invalida"
    assert len(listar_reservas_ativas("101")) == 1


def test_area_ocupada_so_booleano(db):
    assert area_ocupada("salao-de-festas", "2030-03-16") is True
    assert area_ocupada("salao-de-festas", "2030-03-17") is False


def test_listar_reservas_ativas_ordem_e_campos(db):
    criar_reserva("101", "churrasqueira", "2030-08-02", gerar=lambda: "RSV-BBBBBB")
    criar_reserva("101", "quadra", "2030-06-01", gerar=lambda: "RSV-AAAAAA")
    criar_reserva("101", "salao-de-festas", "2030-07-01", gerar=lambda: "RSV-CCCCCC")
    cancelar_reserva("101", "salao-de-festas", "2030-07-01")
    lista = listar_reservas_ativas("101")
    assert [(r.data, r.codigo) for r in lista] == [
        ("2030-03-09", "RSV-1377"),
        ("2030-06-01", "RSV-AAAAAA"),
        ("2030-08-02", "RSV-BBBBBB"),
    ]
    assert set(lista[0].para_dict()) == {"codigo", "area", "data"}
