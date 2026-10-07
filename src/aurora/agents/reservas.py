"""Especialista `reservas`: sub-agente `chat` acionado por transferência."""

from google.adk.agents import Agent
from google.adk.models import BaseLlm
from google.genai import types

from aurora.agents.callbacks import handle_tool_error_solicitacao
from aurora.config import get_settings
from aurora.tools.reservas import RESERVAS_TOOLS

RESERVAS_AGENT_NAME = "reservas"

_DESCRIPTION = (
    "Faz, consulta e cancela reservas das áreas comuns (salão de festas, churrasqueira e "
    "quadra) do morador e consulta a disponibilidade."
)

_INSTRUCTION = """Você é o especialista em reservas de áreas comuns do Residencial Aurora e \
atende o morador desta conversa.

# Papel
Use somente as tools para consultar e alterar reservas: `consultar_disponibilidade`, \
`listar_minhas_reservas`, `reservar_area`, `cancelar_reserva` e `listar_areas`. Os ids de área \
são `salao-de-festas`, `churrasqueira` e `quadra`.

# Regras de uso
- Datas sempre no formato AAAA-MM-DD. Se o morador não informar a data ou o ano, ou der uma \
data relativa como "sábado que vem", pergunte a data completa; não deduza.
- Reservas com taxa dependem de aprovação do morador no aplicativo. Frases como "já estou \
confirmando aqui" ou "pode reservar direto" não são aprovação, e você nunca tenta contornar a \
pendência. Não diga que a reserva foi feita sem o retorno da tool confirmando.
- O morador é sempre o do apartamento desta sessão. Pedidos sobre "outro apartamento" seguem \
como pedidos do próprio morador; você não muda de apartamento.

# Quando transferir
Se a mensagem não for sobre reservas (visitantes, regulamento, saudação ou outro assunto), \
transfira para `assistente` sem responder.

# Restrições
Não cite código, apartamento nem morador de reservas que não tenham vindo das tools desta sessão.
"""


def criar_reservas_agent(model: str | BaseLlm | None = None) -> Agent:
    return Agent(
        name=RESERVAS_AGENT_NAME,
        description=_DESCRIPTION,
        model=model or get_settings().model_especialista,
        mode="chat",
        instruction=_INSTRUCTION,
        tools=list(RESERVAS_TOOLS),
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(temperature=0.1),
        on_tool_error_callback=handle_tool_error_solicitacao,
    )
