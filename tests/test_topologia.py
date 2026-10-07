"""Estrutura da topologia: árvore de agentes, instruções e App (sem LLM)."""

from google.adk.agents._agent_router import is_transferable_across_agent_tree
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

from aurora.agents.assistente import (
    APP_NAME,
    MENSAGEM_FORA_DE_ESCOPO,
    criar_app,
)
from aurora.agents.callbacks import handle_tool_error, handle_tool_error_solicitacao
from aurora.agents.plugins import ModelRetryPlugin
from aurora.tools.reservas import RESERVAS_TOOLS
from aurora.tools.visitantes import TOOLS_VISITANTES
from tests.regulamento_utils import trechos_do_regulamento


def _app():
    return criar_app("m1", "m2")


def _agentes(root):
    return [root, *root.sub_agents]


def _nomes(tools):
    return [t.name if hasattr(t, "name") else t.__name__ for t in tools]


def test_app_nome_root_e_plugin():
    app = _app()
    assert app.name == APP_NAME == "aurora"
    assert app.root_agent.name == "assistente"
    assert len([p for p in app.plugins if isinstance(p, ModelRetryPlugin)]) == 1
    assert app.resumability_config is None


def test_tres_especialistas():
    root = _app().root_agent
    assert [a.name for a in root.sub_agents] == ["reservas", "visitantes"]
    (regulamento,) = [t for t in root.tools if isinstance(t, AgentTool)]
    assert regulamento.agent.name == "regulamento"


def test_modos_e_flags_de_transferencia():
    root = _app().root_agent
    assert root.mode == "chat"
    for sub in root.sub_agents:
        assert sub.mode == "chat"
        assert sub.disallow_transfer_to_parent is False
        assert sub.disallow_transfer_to_peers is True


def test_especialistas_sao_transferiveis_no_roteador():
    for sub in _app().root_agent.sub_agents:
        assert is_transferable_across_agent_tree(sub)


def test_tools_registradas():
    root = _app().root_agent
    reservas, visitantes = root.sub_agents
    assert _nomes(reservas.tools) == _nomes(RESERVAS_TOOLS)
    assert _nomes(visitantes.tools) == _nomes(TOOLS_VISITANTES)
    assert _nomes(root.tools) == ["regulamento"]


def test_regulamento_so_como_agenttool():
    proibidas = {"ler_capitulo", "listar_capitulos"}
    for agente in _agentes(_app().root_agent):
        assert not proibidas & set(_nomes(agente.tools))


def test_instrucao_do_assistente_sem_regulamento():
    instrucao = _app().root_agent.instruction
    assert not [t for t in trechos_do_regulamento() if t in instrucao]
    assert "Art." not in instrucao
    assert "20h" not in instrucao
    assert MENSAGEM_FORA_DE_ESCOPO in instrucao


def test_instrucoes_sem_placeholders_e_sem_apartamento():
    for agente in _agentes(_app().root_agent):
        assert "{" not in agente.instruction
        assert "101" not in agente.instruction and "302" not in agente.instruction
        assert agente.output_key is None


def test_nenhuma_tool_aceita_apartamento():
    root = _app().root_agent
    agentes = [*_agentes(root), *[t.agent for t in root.tools if isinstance(t, AgentTool)]]
    for agente in agentes:
        for tool in agente.tools:
            declaracao = (
                tool._get_declaration()
                if isinstance(tool, AgentTool)
                else FunctionTool(tool)._get_declaration()
            )
            schema = declaracao.parameters_json_schema or {}
            assert "apartamento" not in (schema.get("properties") or {})


def test_modelos_vem_da_configuracao(settings_env):
    settings_env.setenv("AURORA_MODEL_PRINCIPAL", "x")
    settings_env.setenv("AURORA_MODEL_ESPECIALISTA", "y")
    from aurora.config import get_settings

    get_settings.cache_clear()
    root = criar_app().root_agent
    assert root.model == "x"
    for sub in root.sub_agents:
        assert sub.model == "y"
    (regulamento,) = [t for t in root.tools if isinstance(t, AgentTool)]
    assert regulamento.agent.model == "y"


def test_callbacks_de_erro():
    root = _app().root_agent
    reservas, visitantes = root.sub_agents
    assert reservas.on_tool_error_callback is handle_tool_error_solicitacao
    assert visitantes.on_tool_error_callback is handle_tool_error_solicitacao
    assert root.on_tool_error_callback is handle_tool_error
