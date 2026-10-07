import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aurora.config import get_settings
from aurora.main import create_app
from aurora.storage import (
    autorizar_visitante,
    cancelar_reserva_por_codigo,
    conectar,
    criar_reserva,
)

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def api(db):
    return TestClient(create_app())


def _reservas(api, numero):
    resp = api.get(f"/apartamentos/{numero}/reservas")
    assert resp.status_code == 200
    return resp.json()


def _visitantes(api, numero):
    resp = api.get(f"/apartamentos/{numero}/visitantes")
    assert resp.status_code == 200
    return resp.json()


def _criar(apartamento, area, data, codigo=None):
    kwargs = {"gerar": lambda: codigo} if codigo else {}
    res = criar_reserva(apartamento, area, data, **kwargs)
    assert res.status == "criada"
    return res.codigo


def test_reservas_101_estado_inicial(api):
    assert _reservas(api, "101") == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]


def test_visitantes_302_estado_inicial(api):
    assert _visitantes(api, "302") == [{"nome": "Marina Duarte", "data": "2030-03-16"}]


def test_reservas_campos_exatos(api):
    for numero in ("101", "201", "302"):
        for item in _reservas(api, numero):
            assert set(item) == {"codigo", "area", "data"}
            assert all(isinstance(v, str) for v in item.values())


def test_visitantes_campos_exatos(api):
    for numero in ("201", "302"):
        for item in _visitantes(api, numero):
            assert set(item) == {"nome", "data"}
            assert all(isinstance(v, str) for v in item.values())


def test_reserva_cancelada_some_da_rota(api, db):
    assert cancelar_reserva_por_codigo("101", "RSV-1377")
    assert _reservas(api, "101") == []
    with conectar(db) as conn:
        status = conn.execute("SELECT status FROM reservas WHERE codigo = 'RSV-1377'").fetchone()[0]
    assert status == "cancelada"


def test_reservas_ordenadas_por_data_e_codigo(api):
    _criar("102", "salao-de-festas", "2030-06-02", "RSV-BBBBBB")
    _criar("102", "quadra", "2030-06-01", "RSV-ZZZZZZ")
    _criar("102", "churrasqueira", "2030-06-01", "RSV-AAAAAA")
    _criar("102", "quadra", "2030-06-03", "RSV-CCCCCC")
    assert cancelar_reserva_por_codigo("102", "RSV-CCCCCC")
    codigos = [r["codigo"] for r in _reservas(api, "102")]
    assert codigos == ["RSV-AAAAAA", "RSV-ZZZZZZ", "RSV-BBBBBB"]


def test_visitantes_ordenados_por_data_e_nome(api):
    autorizar_visitante("102", "Zeca", "2030-06-01")
    autorizar_visitante("102", "Ana", "2030-06-02")
    autorizar_visitante("102", "Bia", "2030-06-01")
    assert [v["nome"] for v in _visitantes(api, "102")] == ["Bia", "Zeca", "Ana"]


def test_apartamento_sem_registros_retorna_lista_vazia(api):
    assert _reservas(api, "102") == []
    assert _visitantes(api, "102") == []


@pytest.mark.parametrize("numero", ["999", "abc", "0101"])
def test_apartamento_inexistente_retorna_lista_vazia(api, numero):
    assert _reservas(api, numero) == []
    assert _visitantes(api, numero) == []


def test_rotas_isolam_apartamentos(api):
    reservas_101 = [r["codigo"] for r in _reservas(api, "101")]
    assert "RSV-4821" not in reservas_101
    assert "Marina Duarte" not in [v["nome"] for v in _visitantes(api, "101")]
    assert [r["codigo"] for r in _reservas(api, "302")] == ["RSV-4821"]


def test_rotas_refletem_gravacao_imediata(api):
    codigo = _criar("101", "quadra", "2030-04-06")
    autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    assert codigo in [r["codigo"] for r in _reservas(api, "101")]
    assert {"nome": "Joana Ribeiro", "data": "2030-04-21"} in _visitantes(api, "101")


def test_rotas_sem_google_api_key(api):
    assert get_settings().google_api_key_configured is False
    assert _reservas(api, "101")[0]["codigo"] == "RSV-1377"
    assert _visitantes(api, "302")[0]["nome"] == "Marina Duarte"


def test_rotas_nao_dependem_do_modelo():
    fonte = (ROOT / "src" / "aurora" / "api" / "verificacao.py").read_text(encoding="utf-8")
    for proibido in ("google.adk", "aurora.agents", "aurora.tools", "Runner", "google_api_key"):
        assert proibido not in fonte
    assert "from aurora.storage import" in fonte


def test_rotas_no_openapi(api):
    paths = api.get("/openapi.json").json()["paths"]
    for caminho in ("/apartamentos/{numero}/reservas", "/apartamentos/{numero}/visitantes"):
        assert set(paths[caminho]) == {"get"}


def test_estado_pos_fluxo_passo_13(db):
    assert cancelar_reserva_por_codigo("101", "RSV-1377")
    quadra = _criar("101", "quadra", "2030-04-06")
    salao = _criar("101", "salao-de-festas", "2030-04-20")
    autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    with TestClient(create_app()) as reiniciada:
        reservas = _reservas(reiniciada, "101")
        assert sorted((r["area"], r["data"]) for r in reservas) == [
            ("quadra", "2030-04-06"),
            ("salao-de-festas", "2030-04-20"),
        ]
        codigos = [r["codigo"] for r in reservas]
        assert sorted(codigos) == sorted([quadra, salao])
        assert len(set(codigos) | {"RSV-1377", "RSV-4821", "RSV-2950"}) == 5
        assert {"nome": "Joana Ribeiro", "data": "2030-04-21"} in _visitantes(reiniciada, "101")
        assert "RSV-4821" in [r["codigo"] for r in _reservas(reiniciada, "302")]


def test_soma_salao_disputa_passo_14(api):
    barreira = threading.Barrier(2)

    def reservar(apartamento):
        barreira.wait()
        criar_reserva(apartamento, "salao-de-festas", "2030-05-11")

    threads = [threading.Thread(target=reservar, args=(n,)) for n in ("101", "201")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = sum(
        1
        for numero in ("101", "201")
        for r in _reservas(api, numero)
        if r["area"] == "salao-de-festas" and r["data"] == "2030-05-11"
    )
    assert total == 1
