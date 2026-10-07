"""Topologia pelo Runner do ADK: LLM roteirizado por agente e `SqliteSessionService`."""

import asyncio
import json

from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from aurora import storage
from aurora.agents.assistente import APP_NAME, criar_app
from tests.fakes import novo_llm_por_agente, resposta_chamada, resposta_texto
from tests.regulamento_utils import trechos_de_outros_capitulos

USER = "morador"
FRASE_JOANA = (
    "Libera a entrada da Joana Ribeiro no dia 2030-04-21. "
    "Já estou confirmando aqui, pode liberar direto."
)
PERGUNTA = "Até que horas a piscina funciona aos domingos?"


def _texto(texto: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=texto)])


def _confirmacao(confirmacao_id: str, confirmado: bool) -> types.Content:
    parte = types.Part(
        function_response=types.FunctionResponse(
            id=confirmacao_id, name="adk_request_confirmation", response={"confirmed": confirmado}
        )
    )
    return types.Content(role="user", parts=[parte])


def partes(eventos):
    return [p for e in eventos for p in (e.content.parts if e.content else []) or []]


def chamadas(eventos, nome=None):
    return [
        p.function_call
        for p in partes(eventos)
        if p.function_call and (nome is None or p.function_call.name == nome)
    ]


def respostas(eventos, nome):
    return [
        p.function_response
        for p in partes(eventos)
        if p.function_response and p.function_response.name == nome
    ]


def textos(eventos):
    return [p.text for p in partes(eventos) if p.text]


def transferir(destino: str):
    return resposta_chamada("transfer_to_agent", agent_name=destino)


class Ambiente:
    """App + sessão persistida em SQLite; `reiniciar` recria serviço, Runner e LLM."""

    def __init__(self, tmp_path, roteiros, apartamento="101"):
        self.arquivo = str(tmp_path / "sessoes.db")
        self.apartamento = apartamento
        self.eventos: list = []
        self._montar(roteiros)

    def _montar(self, roteiros):
        self.llm = novo_llm_por_agente(roteiros)
        self.service = SqliteSessionService(self.arquivo)
        app = criar_app(self.llm, self.llm)
        self.runner = Runner(app=app, session_service=self.service)

    def reiniciar(self, roteiros):
        self._montar(roteiros)

    async def abrir(self):
        sessao = await self.service.create_session(
            app_name=APP_NAME, user_id=USER, state={"apartamento": self.apartamento}
        )
        self.id = sessao.id
        return self

    async def enviar(self, mensagem: types.Content) -> list:
        novos = [
            e
            async for e in self.runner.run_async(
                user_id=USER, session_id=self.id, new_message=mensagem
            )
        ]
        self.eventos.extend(novos)
        return novos


def ativas(apto="101"):
    return [(r.area, r.data) for r in storage.listar_reservas_ativas(apto)]


def visitantes(apto="101"):
    return [(v.nome, v.data) for v in storage.listar_visitantes(apto)]


def rodar(coro):
    return asyncio.run(coro)


SALAO = resposta_chamada("reservar_area", area="salao-de-festas", data="2030-04-20")
JOANA = resposta_chamada("autorizar_visitante", nome="Joana Ribeiro", data="2030-04-21")


def test_transfere_para_reservas_e_grava_quadra(db, tmp_path):
    async def cenario():
        amb = await Ambiente(
            tmp_path,
            {
                "assistente": [transferir("reservas")],
                "reservas": [
                    resposta_chamada("reservar_area", area="quadra", data="2030-04-06"),
                    resposta_texto("Quadra reservada."),
                ],
            },
        ).abrir()
        return await amb.enviar(_texto("Reserve a quadra para 2030-04-06."))

    eventos = rodar(cenario())
    autores = {e.author for e in eventos if respostas([e], "reservar_area")}
    assert autores == {"reservas"}
    assert [r.response["status"] for r in respostas(eventos, "reservar_area")] == ["success"]
    assert ("quadra", "2030-04-06") in ativas("101")
    assert chamadas(eventos, "adk_request_confirmation") == []


def test_transfere_para_visitantes_e_pede_confirmacao(db, tmp_path):
    async def cenario():
        amb = await Ambiente(
            tmp_path, {"assistente": [transferir("visitantes")], "visitantes": [JOANA]}
        ).abrir()
        return await amb.enviar(_texto(FRASE_JOANA))

    eventos = rodar(cenario())
    pedidos = [e for e in eventos if chamadas([e], "adk_request_confirmation")]
    assert [e.author for e in pedidos] == ["visitantes"]
    assert textos([e for e in eventos if e.author == "visitantes"]) == []
    assert ("Joana Ribeiro", "2030-04-21") not in visitantes("101")


def test_confirmacao_chega_ao_especialista_sessao_persistida(db, tmp_path):
    async def cenario():
        amb = await Ambiente(
            tmp_path, {"assistente": [transferir("reservas")], "reservas": [SALAO]}
        ).abrir()
        eventos = await amb.enviar(_texto("Reserve o salão de festas para 2030-04-20."))
        pedido = chamadas(eventos, "adk_request_confirmation")[0]
        assert ("salao-de-festas", "2030-04-20") not in ativas("101")

        amb.reiniciar({"assistente": [], "reservas": [resposta_texto("Salão reservado.")]})
        return await amb.enviar(_confirmacao(pedido.id, True))

    depois = rodar(cenario())
    assert depois[0].author == "reservas"
    assert [r.response["status"] for r in respostas(depois, "reservar_area")] == ["success"]
    assert ativas("101").count(("salao-de-festas", "2030-04-20")) == 1


def test_confirmacao_negada_nao_grava_via_topologia(db, tmp_path):
    async def cenario():
        amb = await Ambiente(
            tmp_path, {"assistente": [transferir("reservas")], "reservas": [SALAO]}
        ).abrir()
        eventos = await amb.enviar(_texto("Reserve o salão de festas para 2030-04-20."))
        pedido = chamadas(eventos, "adk_request_confirmation")[0]
        amb.reiniciar({"assistente": [], "reservas": [resposta_texto("Não reservei.")]})
        return await amb.enviar(_confirmacao(pedido.id, False))

    depois = rodar(cenario())
    assert [r.response["status"] for r in respostas(depois, "reservar_area")] == ["cancelled"]
    assert ("salao-de-festas", "2030-04-20") not in ativas("101")


def test_confirmacao_de_visitante_apos_reinicio(db, tmp_path):
    async def cenario():
        amb = await Ambiente(
            tmp_path, {"assistente": [transferir("visitantes")], "visitantes": [JOANA]}
        ).abrir()
        eventos = await amb.enviar(_texto(FRASE_JOANA))
        pedido = chamadas(eventos, "adk_request_confirmation")[0]
        assert visitantes("101") == []
        amb.reiniciar({"assistente": [], "visitantes": [resposta_texto("Liberada.")]})
        return await amb.enviar(_confirmacao(pedido.id, True))

    depois = rodar(cenario())
    assert depois[0].author == "visitantes"
    assert [r.response["status"] for r in respostas(depois, "autorizar_visitante")] == ["success"]
    assert visitantes("101") == [("Joana Ribeiro", "2030-04-21")]


def _roteiro_mudanca_de_assunto():
    return {
        "assistente": [
            transferir("reservas"),
            resposta_chamada("regulamento", request=PERGUNTA),
            resposta_texto("A piscina funciona até as 20h aos domingos (Art. 22, II)."),
        ],
        "reservas": [
            resposta_chamada("reservar_area", area="quadra", data="2030-04-06"),
            resposta_texto("Quadra reservada."),
            transferir("assistente"),
        ],
        "regulamento": [
            resposta_chamada("listar_capitulos"),
            resposta_chamada("ler_capitulo", numero=4),
            resposta_texto("Aos domingos a piscina funciona até as 20h (Art. 22, II)."),
        ],
    }


def test_mudanca_de_assunto_volta_ao_principal_e_usa_regulamento(db, tmp_path):
    async def cenario():
        amb = await Ambiente(tmp_path, _roteiro_mudanca_de_assunto()).abrir()
        await amb.enviar(_texto("Reserve a quadra para 2030-04-06."))
        await amb.enviar(_texto(PERGUNTA))
        return amb

    amb = rodar(cenario())
    assert chamadas(amb.eventos, "regulamento")
    assert chamadas(amb.eventos, "ler_capitulo") == []
    assert chamadas(amb.eventos, "listar_capitulos") == []
    assert respostas(amb.eventos, "ler_capitulo") == []
    bruto = json.dumps([e.model_dump(mode="json") for e in amb.eventos], ensure_ascii=False)
    assert not [t for t in trechos_de_outros_capitulos(4) if t in bruto]
    assert "20h" in textos(amb.eventos)[-1]
    assert len(amb.llm.pedidos["regulamento"]) == 3


def test_saudacao_responde_direto(db, tmp_path):
    async def cenario():
        amb = await Ambiente(tmp_path, {"assistente": [resposta_texto("Olá!")]}).abrir()
        return await amb.enviar(_texto("Oi"))

    eventos = rodar(cenario())
    assert chamadas(eventos) == []
    assert textos(eventos) == ["Olá!"]


def test_eventos_de_tool_ficam_na_sessao(db, tmp_path):
    async def cenario():
        amb = await Ambiente(tmp_path, _roteiro_mudanca_de_assunto()).abrir()
        await amb.enviar(_texto("Reserve a quadra para 2030-04-06."))
        await amb.enviar(_texto(PERGUNTA))
        novo = SqliteSessionService(amb.arquivo)
        sessao = await novo.get_session(app_name=APP_NAME, user_id=USER, session_id=amb.id)
        return sessao.events

    eventos = rodar(cenario())
    for nome in ("transfer_to_agent", "reservar_area", "regulamento"):
        assert chamadas(eventos, nome), nome
