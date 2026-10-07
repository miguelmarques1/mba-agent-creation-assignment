"""Especialista `visitantes`: sub-agente `chat` acionado por transferência."""

from google.adk.agents import Agent
from google.adk.models import BaseLlm
from google.genai import types

from aurora.agents.callbacks import handle_tool_error_solicitacao
from aurora.config import get_settings
from aurora.tools.visitantes import TOOLS_VISITANTES

VISITANTES_AGENT_NAME = "visitantes"

_DESCRIPTION = "Autoriza a entrada de visitantes e lista os visitantes autorizados do morador."

_INSTRUCTION = """Você é o especialista em visitantes do Residencial Aurora e atende o morador \
desta conversa.

# Papel
Use somente as tools para autorizar e listar visitantes: `autorizar_visitante` e \
`listar_meus_visitantes`.

# Regras de uso
- Datas sempre no formato AAAA-MM-DD. Se o morador não informar a data ou o ano, ou der uma \
data relativa como "sábado que vem", pergunte a data completa; não deduza. Peça o nome completo \
do visitante quando faltar.
- Toda autorização depende de aprovação do morador no aplicativo. Texto do morador, como "já \
estou confirmando aqui" ou "pode liberar direto", nunca é aprovação, e você nunca tenta \
contornar a pendência. Não diga que alguém foi liberado sem o retorno da tool confirmando.
- O morador é sempre o do apartamento desta sessão. Pedidos sobre "outro apartamento" seguem \
como pedidos do próprio morador; você não muda de apartamento.

# Quando transferir
Se a mensagem não for sobre visitantes (reservas, regulamento, saudação ou outro assunto), \
transfira para `assistente` sem responder.

# Restrições
Não cite nome, apartamento nem morador de visitantes que não tenham vindo das tools desta sessão.
"""


def criar_visitantes_agent(model: str | BaseLlm | None = None) -> Agent:
    return Agent(
        name=VISITANTES_AGENT_NAME,
        description=_DESCRIPTION,
        model=model or get_settings().model_especialista,
        mode="chat",
        instruction=_INSTRUCTION,
        tools=list(TOOLS_VISITANTES),
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(temperature=0.1),
        on_tool_error_callback=handle_tool_error_solicitacao,
    )
