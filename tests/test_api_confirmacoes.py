"""Rota de confirmações por `TestClient`, com serviço injetado (LLM roteirizado)."""

import pytest
from fastapi.testclient import TestClient

from aurora import storage
from aurora.main import create_app
from tests.fakes import resposta_chamada, resposta_texto

SALAO = resposta_chamada("reservar_area", area="salao-de-festas", data="2030-04-20")
JOANA = resposta_chamada("autorizar_visitante", nome="Joana Ribeiro", data="2030-04-21")
DETAIL_404 = {"detail": "Sessão não encontrada."}
DETAIL_409 = {"detail": "Não existe confirmação pendente com esse id nesta sessão."}
DETAIL_502 = {"detail": "Falha ao consultar o modelo; tente novamente."}


def transferir(destino):
    return resposta_chamada("transfer_to_agent", agent_name=destino)


@pytest.fixture
def abrir(servico_factory):
    abertos = []

    def fabrica(roteiros, **kw):
        cliente = TestClient(create_app(servico_factory(roteiros, **kw)))
        cliente.__enter__()
        abertos.append(cliente)
        return cliente

    yield fabrica
    for cliente in abertos:
        cliente.__exit__(None, None, None)


def nova_sessao(cliente, apartamento="101"):
    return cliente.post("/sessoes", json={"apartamento": apartamento}).json()["session_id"]


def pedir(cliente, sid, texto="Reserve o salão de festas para 2030-04-20."):
    r = cliente.post(f"/sessoes/{sid}/mensagens", json={"texto": texto})
    assert r.status_code == 200
    return r.json()["confirmacoes_pendentes"][0]["id"]


def confirmar(cliente, sid, cid, confirmado):
    return cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": cid, "confirmado": confirmado})


def salao_listado(cliente, apto="101"):
    reservas = cliente.get(f"/apartamentos/{apto}/reservas").json()
    return [r for r in reservas if (r["area"], r["data"]) == ("salao-de-festas", "2030-04-20")]


def test_confirmar_200_formato(abrir):
    c = abrir(
        {"assistente": [transferir("reservas")], "reservas": [SALAO, resposta_texto("Feito.")]}
    )
    sid = nova_sessao(c)
    r = confirmar(c, sid, pedir(c, sid), True)
    assert r.status_code == 200
    corpo = r.json()
    assert set(corpo) == {"resposta", "confirmacoes_pendentes"}
    assert isinstance(corpo["resposta"], str) and isinstance(corpo["confirmacoes_pendentes"], list)


def test_fluxo_passos_7_8_9(abrir):
    roteiros = {
        "assistente": [transferir("reservas")],
        "reservas": [
            SALAO,
            resposta_texto("Reserva não confirmada; nada foi feito."),
            SALAO,
            resposta_texto("Reservado."),
        ],
    }
    c = abrir(roteiros)
    sid = nova_sessao(c)
    # passo 7: nega
    cid = pedir(c, sid)
    r = confirmar(c, sid, cid, False)
    assert r.status_code == 200 and r.json()["confirmacoes_pendentes"] == []
    assert salao_listado(c) == []
    # passo 8: novo pedido e aprovação (o especialista `reservas` segue ativo na sessão)
    cid2 = pedir(c, sid)
    assert cid2 != cid
    r = confirmar(c, sid, cid2, True)
    assert r.status_code == 200
    assert len(salao_listado(c)) == 1
    antes = c.get("/apartamentos/101/reservas").json()
    # reenvio e id inexistente
    r = confirmar(c, sid, cid2, True)
    assert (r.status_code, r.json()) == (409, DETAIL_409)
    r = confirmar(c, sid, "id-inexistente", True)
    assert (r.status_code, r.json()) == (409, DETAIL_409)
    assert c.get("/apartamentos/101/reservas").json() == antes
    assert len(salao_listado(c)) == 1


def test_confirmacao_sessao_inexistente_404(abrir):
    r = confirmar(abrir({}), "nao-existe", "x", True)
    assert (r.status_code, r.json()) == (404, DETAIL_404)


@pytest.mark.parametrize(
    "corpo",
    [
        {},
        {"id": ""},
        {"id": "  ", "confirmado": True},
        {"id": "x"},
        {"id": "x", "confirmado": "true"},
        {"id": "x", "confirmado": 1},
    ],
)
def test_confirmacao_corpo_invalido_422(abrir, corpo):
    c = abrir({})
    sid = nova_sessao(c)
    assert c.post(f"/sessoes/{sid}/confirmacoes", json=corpo).status_code == 422


def test_confirmacao_sem_chave_502(abrir):
    # a pendência nasce num app sem exigência de chave; um segundo app (mesmos arquivos) a exige
    c = abrir({"assistente": [transferir("reservas")], "reservas": [SALAO]})
    sid = nova_sessao(c)
    cid = pedir(c, sid)
    sem_chave = abrir({}, exige_chave=True)
    r = confirmar(sem_chave, sid, cid, True)
    assert (r.status_code, r.json()) == (502, DETAIL_502)
    with storage.conectar() as conn:
        status = conn.execute("SELECT status FROM confirmacoes WHERE id = ?", (cid,)).fetchone()
    assert status == ("pendente",)


def test_409_funciona_sem_chave(abrir):
    c = abrir({}, exige_chave=True)
    sid = nova_sessao(c)
    r = confirmar(c, sid, "id-inexistente", True)
    assert (r.status_code, r.json()) == (409, DETAIL_409)


def test_visitante_aprovado_aparece_na_verificacao(abrir):
    roteiros = {
        "assistente": [transferir("visitantes")],
        "visitantes": [JOANA, resposta_texto("Joana autorizada.")],
    }
    c = abrir(roteiros)
    sid = nova_sessao(c)
    cid = pedir(c, sid, "Libera a Joana Ribeiro em 2030-04-21")
    assert "Joana Ribeiro" not in [v["nome"] for v in c.get("/apartamentos/101/visitantes").json()]
    assert confirmar(c, sid, cid, True).status_code == 200
    visitantes = c.get("/apartamentos/101/visitantes").json()
    assert {"nome": "Joana Ribeiro", "data": "2030-04-21"} in visitantes


def test_reinicio_via_novo_app_aprova(abrir):
    app1 = abrir({"assistente": [transferir("reservas")], "reservas": [SALAO]})
    sid = nova_sessao(app1)
    cid = pedir(app1, sid)
    app2 = abrir({"reservas": [resposta_texto("Reservado.")]})
    assert confirmar(app2, sid, cid, True).status_code == 200
    assert len(salao_listado(app2)) == 1
    r = confirmar(app2, sid, cid, True)
    assert (r.status_code, r.json()) == (409, DETAIL_409)
