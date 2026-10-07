"""Rotas de sessão por `TestClient`, com serviço injetado (LLM roteirizado)."""

import pytest
from fastapi.testclient import TestClient
from google.adk.models import LlmResponse
from google.genai import errors as genai_errors
from google.genai import types

from aurora.main import create_app
from tests.fakes import LlmPorAgente, resposta_chamada, resposta_texto

SALAO = resposta_chamada("reservar_area", area="salao-de-festas", data="2030-04-20")
QUADRA = resposta_chamada("reservar_area", area="quadra", data="2030-04-06")
DETAIL_404 = {"detail": "Sessão não encontrada."}
DETAIL_502 = {"detail": "Falha ao consultar o modelo; tente novamente."}


def transferir(destino):
    return resposta_chamada("transfer_to_agent", agent_name=destino)


def roteiro_quadra():
    return {
        "assistente": [transferir("reservas")],
        "reservas": [QUADRA, resposta_texto("Quadra reservada.")],
    }


def roteiro_salao():
    return {"assistente": [transferir("reservas")], "reservas": [SALAO]}


@pytest.fixture
def abrir(servico_factory):
    """`abrir(roteiros)` devolve um `TestClient` já dentro do lifespan, com o serviço injetado."""
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
    r = cliente.post("/sessoes", json={"apartamento": apartamento})
    assert r.status_code == 201
    return r.json()["session_id"]


def test_post_sessoes_201(abrir):
    r = abrir({}).post("/sessoes", json={"apartamento": "101"})
    assert r.status_code == 201
    assert set(r.json()) == {"session_id"}
    assert isinstance(r.json()["session_id"], str) and r.json()["session_id"]


def test_post_sessoes_aceita_inteiro(abrir):
    assert abrir({}).post("/sessoes", json={"apartamento": 101}).status_code == 201


def test_post_sessoes_apartamento_inexistente_422(abrir):
    r = abrir({}).post("/sessoes", json={"apartamento": "999"})
    assert r.status_code == 422
    assert r.json() == {"detail": "Apartamento inexistente."}


@pytest.mark.parametrize(
    "corpo", [{}, {"apartamento": ""}, {"apartamento": "  "}, {"apartamento": None}]
)
def test_post_sessoes_corpo_invalido_422(abrir, corpo):
    assert abrir({}).post("/sessoes", json=corpo).status_code == 422


def test_mensagem_200_formato(abrir):
    c = abrir(roteiro_quadra())
    sid = nova_sessao(c)
    r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve a quadra para 2030-04-06."})
    assert r.status_code == 200
    assert set(r.json()) == {"resposta", "confirmacoes_pendentes"}
    assert r.json() == {"resposta": "Quadra reservada.", "confirmacoes_pendentes": []}


def test_mensagem_pendente_resposta_vazia(abrir):
    c = abrir(roteiro_salao())
    sid = nova_sessao(c)
    r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve o salão para 2030-04-20."})
    corpo = r.json()
    assert r.status_code == 200 and corpo["resposta"] == ""
    (pendencia,) = corpo["confirmacoes_pendentes"]
    assert set(pendencia) == {"id", "acao", "detalhes"}
    assert pendencia["acao"] == "reservar_area"
    assert pendencia["detalhes"]["area"] == "salao-de-festas"
    assert pendencia["detalhes"]["data"] == "2030-04-20"


@pytest.mark.parametrize("corpo", [{"texto": "   "}, {"texto": ""}, {}])
def test_mensagem_texto_vazio_422(abrir, corpo):
    c = abrir({})
    sid = nova_sessao(c)
    assert c.post(f"/sessoes/{sid}/mensagens", json=corpo).status_code == 422


def test_rotas_sessao_inexistente_404(abrir):
    c = abrir({})
    r = c.get("/sessoes/sessao-inexistente/eventos")
    assert (r.status_code, r.json()) == (404, DETAIL_404)
    r = c.post("/sessoes/sessao-inexistente/mensagens", json={"texto": "oi"})
    assert (r.status_code, r.json()) == (404, DETAIL_404)


def test_eventos_ordem_e_conteudo_completo(abrir):
    c = abrir(roteiro_salao())
    sid = nova_sessao(c)
    assert c.get(f"/sessoes/{sid}/eventos").json() == []
    c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve o salão para 2030-04-20."})
    eventos = c.get(f"/sessoes/{sid}/eventos").json()
    assert eventos[0]["author"] == "user"
    assert eventos[0]["content"]["parts"][0]["text"].startswith("Reserve o salão")
    chamadas = {
        p["function_call"]["name"]: p["function_call"]
        for e in eventos
        for p in e.get("content", {}).get("parts", [])
        if "function_call" in p
    }
    assert chamadas["transfer_to_agent"]["args"] == {"agent_name": "reservas"}
    assert chamadas["reservar_area"]["args"] == {"area": "salao-de-festas", "data": "2030-04-20"}
    assert "adk_request_confirmation" in chamadas
    nomes_resposta = [
        p["function_response"]["name"]
        for e in eventos
        for p in e.get("content", {}).get("parts", [])
        if "function_response" in p
    ]
    assert "transfer_to_agent" in nomes_resposta
    stamps = [e["timestamp"] for e in eventos]
    assert stamps == sorted(stamps)


def test_eventos_sem_chave_funciona(abrir):
    c = abrir({}, exige_chave=True)
    sid = nova_sessao(c)
    r = c.get(f"/sessoes/{sid}/eventos")
    assert (r.status_code, r.json()) == (200, [])


def test_mensagem_sem_chave_502(abrir):
    c = abrir({}, exige_chave=True)
    sid = nova_sessao(c)
    r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "oi"})
    assert (r.status_code, r.json()) == (502, DETAIL_502)
    assert c.get(f"/sessoes/{sid}/eventos").json() == []


class _LlmComFalha(LlmPorAgente):
    async def generate_content_async(self, llm_request, stream=False):
        raise genai_errors.ClientError(429, {"error": {"message": "cota"}})
        yield  # pragma: no cover


def test_falha_do_modelo_502(abrir):
    c = abrir({}, llm=_LlmComFalha(roteiros={}, pedidos={}))
    sid = nova_sessao(c)
    r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "oi"})
    assert (r.status_code, r.json()) == (502, DETAIL_502)


def test_reinicio_via_novo_app(abrir):
    roteiros = roteiro_quadra()
    roteiros["reservas"].append(resposta_texto("De nada."))
    app1 = abrir(roteiros)
    sid = nova_sessao(app1)
    app1.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve a quadra para 2030-04-06."})
    app1.post(f"/sessoes/{sid}/mensagens", json={"texto": "Obrigado"})
    antes = app1.get(f"/sessoes/{sid}/eventos").json()

    app2 = abrir({"reservas": [resposta_texto("Você tem a quadra."), resposta_texto("De nada.")]})
    assert app2.get(f"/sessoes/{sid}/eventos").json() == antes
    r = app2.post(
        f"/sessoes/{sid}/mensagens", json={"texto": "Quais são as minhas reservas agora?"}
    )
    assert r.status_code == 200
    assert len(app2.get(f"/sessoes/{sid}/eventos").json()) > len(antes)


def test_verificacao_reflete_conversa(abrir):
    c = abrir(roteiro_quadra())
    sid = nova_sessao(c)
    c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve a quadra para 2030-04-06."})
    reservas = c.get("/apartamentos/101/reservas").json()
    assert {"area": "quadra", "data": "2030-04-06"} in [
        {"area": r["area"], "data": r["data"]} for r in reservas
    ]


def test_resposta_do_modelo_vazia_nao_quebra_contrato(abrir):
    vazio = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="")]))
    c = abrir({"assistente": [vazio, vazio, vazio, vazio]})
    sid = nova_sessao(c)
    r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "oi"})
    assert r.status_code == 200
    assert set(r.json()) == {"resposta", "confirmacoes_pendentes"}
