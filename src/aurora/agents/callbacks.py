"""Callbacks compartilhados pelos agentes."""

import logging

logger = logging.getLogger("aurora")

MENSAGEM_GENERICA = "Ocorreu um erro inesperado ao consultar o regulamento."


def handle_tool_error(tool, args, tool_context, error, *, mensagem_generica=MENSAGEM_GENERICA):
    """`on_tool_error_callback`: o resultado da tool vira um dict de erro, sem lançar."""
    logger.error("Erro na tool %s: %s", getattr(tool, "name", tool), type(error).__name__)
    if isinstance(error, ValueError):
        return {"status": "error", "message": str(error)}
    return {"status": "error", "message": mensagem_generica}
