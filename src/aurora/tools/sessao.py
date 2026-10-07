"""Apartamento da sessão: única fonte do apartamento para as tools."""

from google.adk.tools import ToolContext

APARTAMENTO_KEY = "apartamento"


class ApartamentoAusenteError(RuntimeError):
    """Sessão sem apartamento no state: defeito de infraestrutura, não erro de negócio."""


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    """Lê o apartamento gravado no state na criação da sessão; nunca escreve no state."""
    apartamento = tool_context.state.get(APARTAMENTO_KEY)
    if not apartamento:
        raise ApartamentoAusenteError("Sessão sem apartamento no state.")
    return str(apartamento)
