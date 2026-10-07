import asyncio

from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from aurora.agents.regulamento import (
    _DESCRIPTION,
    _INSTRUCTION,
    RESPOSTA_NAO_ENCONTRADA,
    criar_regulamento_agent,
    criar_regulamento_tool,
)
from aurora.config import get_settings
from aurora.tools.regulamento import indice_regulamento
from tests.fakes import novo_llm, resposta_chamada, resposta_texto
from tests.regulamento_utils import trechos_do_regulamento

PERGUNTA = "Até que horas a piscina funciona aos domingos?"
RESPOSTA = "Aos domingos e feriados a piscina funciona das 9h às 20h (Art. 22, II)."


def test_agente_nome_tools_e_config(settings_env):
    agent = criar_regulamento_agent()
    assert agent.name == "regulamento"
    assert {t.__name__ for t in agent.tools} == {"listar_capitulos", "ler_capitulo"}
    assert agent.output_key is None and agent.output_schema is None
    assert agent.generate_content_config.temperature == 0.1
    assert agent.on_tool_error_callback is not None


def test_modelo_vem_de_aurora_model_especialista(settings_env):
    assert criar_regulamento_agent().model == "gemini-3.6-flash"
    settings_env.setenv("AURORA_MODEL_ESPECIALISTA", "modelo-x")
    get_settings.cache_clear()
    assert criar_regulamento_agent().model == "modelo-x"


def _sem_texto_do_regulamento(texto):
    assert not any(t in texto for t in trechos_do_regulamento())
    assert not any(c.titulo.lower() in texto.lower() for c in indice_regulamento())
    assert "**Art." not in texto


def test_instrucao_sem_texto_do_regulamento():
    _sem_texto_do_regulamento(_INSTRUCTION)


def test_descricao_sem_texto_do_regulamento():
    _sem_texto_do_regulamento(_DESCRIPTION)
    _sem_texto_do_regulamento(criar_regulamento_agent(model="x").description)


def test_instrucao_contem_frase_nao_encontrada():
    assert RESPOSTA_NAO_ENCONTRADA == "Não encontrei essa informação no regulamento interno."
    assert RESPOSTA_NAO_ENCONTRADA in _INSTRUCTION


def test_instrucao_sem_placeholders_de_state():
    assert "{" not in _INSTRUCTION


def test_regulamento_tool_e_agenttool():
    tool = criar_regulamento_tool(model="x")
    assert isinstance(tool, AgentTool)
    assert tool.name == "regulamento" and tool.agent.name == "regulamento"
    esquema = tool._get_declaration().parameters_json_schema
    assert list(esquema["properties"]) == ["request"]
    assert esquema["properties"]["request"]["type"] == "string"


def test_fabricas_criam_instancias_novas():
    assert criar_regulamento_tool(model="x").agent is not criar_regulamento_tool(model="x").agent


def _rodar_cenario():
    especialista = novo_llm(
        [
            resposta_chamada("listar_capitulos"),
            resposta_chamada("ler_capitulo", numero=4),
            resposta_texto(RESPOSTA),
        ]
    )
    raiz_llm = novo_llm(
        [resposta_chamada("regulamento", request=PERGUNTA), resposta_texto("Funciona até 20h.")]
    )
    raiz = Agent(
        name="raiz_teste",
        model=raiz_llm,
        instruction="Use a tool regulamento.",
        tools=[criar_regulamento_tool(model=especialista)],
    )

    async def rodar():
        runner = InMemoryRunner(agent=raiz, app_name="teste_f05")
        sessao = await runner.session_service.create_session(
            app_name="teste_f05", user_id="u", state={"apartamento": "101"}
        )
        mensagem = types.Content(role="user", parts=[types.Part(text=PERGUNTA)])
        async for _ in runner.run_async(user_id="u", session_id=sessao.id, new_message=mensagem):
            pass
        return await runner.session_service.get_session(
            app_name="teste_f05", user_id="u", session_id=sessao.id
        )

    return asyncio.run(rodar()), especialista


def test_agenttool_isola_eventos_do_especialista():
    sessao, _ = _rodar_cenario()
    chamadas, respostas = [], []
    for evento in sessao.events:
        for parte in (evento.content.parts if evento.content else []) or []:
            if parte.function_call:
                chamadas.append(parte.function_call.name)
            if parte.function_response:
                respostas.append(parte.function_response)
    assert "regulamento" in chamadas
    assert any(r.name == "regulamento" and "20h" in str(r.response) for r in respostas)
    assert not {"listar_capitulos", "ler_capitulo"} & (set(chamadas) | {r.name for r in respostas})

    serializado = "".join(str(e.model_dump(mode="json")) for e in sessao.events)
    permitido = serializado.replace(RESPOSTA, "")
    assert not any(t in permitido for t in trechos_do_regulamento())
    assert set(sessao.state) == {"apartamento"}


def test_specialist_ve_so_a_pergunta():
    _, especialista = _rodar_cenario()
    primeiro = especialista.pedidos[0]
    textos = " ".join(p.text for c in primeiro.contents for p in c.parts if p.text)
    assert PERGUNTA in textos
    assert "Use a tool regulamento." not in textos


def test_limite_de_capitulos_vale_no_runner_real():
    """Com eventos reais da sessão: o terceiro capítulo distinto é recusado pela tool."""
    especialista = novo_llm(
        [
            resposta_chamada("ler_capitulo", numero=1),
            resposta_chamada("ler_capitulo", numero=2),
            resposta_chamada("ler_capitulo", numero=3),
            resposta_texto(RESPOSTA_NAO_ENCONTRADA),
        ]
    )
    agente = criar_regulamento_agent(model=especialista)

    async def rodar():
        runner = InMemoryRunner(agent=agente, app_name="teste_f05_limite")
        sessao = await runner.session_service.create_session(
            app_name="teste_f05_limite", user_id="u"
        )
        mensagem = types.Content(role="user", parts=[types.Part(text=PERGUNTA)])
        retornos = []
        async for evento in runner.run_async(
            user_id="u", session_id=sessao.id, new_message=mensagem
        ):
            for parte in (evento.content.parts if evento.content else []) or []:
                if parte.function_response:
                    retornos.append(parte.function_response.response)
        return retornos

    retornos = asyncio.run(rodar())
    assert [r["status"] for r in retornos] == ["success", "success", "error"]
    assert "texto" not in retornos[2]
