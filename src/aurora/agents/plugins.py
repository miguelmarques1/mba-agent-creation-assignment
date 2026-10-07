"""Plugins transversais do App."""

import logging

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import BasePlugin
from google.genai import types

logger = logging.getLogger("aurora")

_RETRIABLE_LLM_ERRORS = ("MALFORMED_RESPONSE",)
_MAX_RETRIES = 3

MENSAGEM_FALHA_TECNICA = (
    "Tive um problema técnico ao concluir esta etapa. Pode reenviar sua mensagem, por favor?"
)
NUDGE = types.Content(
    role="user",
    parts=[
        types.Part(
            text=(
                "Sua resposta anterior não teve conteúdo. "
                "Por favor, tente novamente e forneça uma resposta clara."
            )
        )
    ],
)


def _is_empty_response(llm_response: LlmResponse) -> bool:
    """Resposta que terminou normal mas sem nenhum conteúdo útil."""
    if llm_response.partial:
        return False
    if llm_response.error_code:
        return False
    if llm_response.content and llm_response.content.parts:
        for part in llm_response.content.parts:
            if part.thought:
                continue
            if (
                part.text
                or part.function_call
                or part.function_response
                or part.inline_data
                or part.executable_code
                or part.code_execution_result
            ):
                return False
    return True


class ModelRetryPlugin(BasePlugin):
    """Refaz a chamada ao modelo quando a resposta vem vazia ou malformada."""

    def __init__(self, name: str = "model_retry_plugin"):
        super().__init__(name=name)
        # As chamadas ao modelo de uma invocação são sequenciais.
        self._pending_requests: dict[str, LlmRequest] = {}

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> LlmResponse | None:
        self._pending_requests[callback_context.invocation_id] = llm_request
        return None

    async def after_model_callback(
        self, *, callback_context: CallbackContext, llm_response: LlmResponse
    ) -> LlmResponse | None:
        llm_request = self._pending_requests.pop(callback_context.invocation_id, None)
        code = getattr(llm_response.error_code, "name", llm_response.error_code)
        if llm_request is None or (
            code not in _RETRIABLE_LLM_ERRORS and not _is_empty_response(llm_response)
        ):
            return None

        retry_request = llm_request.model_copy(deep=True)
        retry_request.contents = [*(retry_request.contents or []), NUDGE]
        llm = callback_context._invocation_context.agent.canonical_model

        for tentativa in range(1, _MAX_RETRIES + 1):
            logger.warning(
                "Resposta %s do modelo; retry %d/%d", code or "vazia", tentativa, _MAX_RETRIES
            )
            final = None
            async for resposta in llm.generate_content_async(retry_request, stream=False):
                final = resposta
            if final is not None and not final.error_code and not _is_empty_response(final):
                return final

        return LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=MENSAGEM_FALHA_TECNICA)])
        )

    async def after_agent_callback(
        self, *, agent: BaseAgent, callback_context: CallbackContext
    ) -> types.Content | None:
        self._pending_requests.pop(callback_context.invocation_id, None)
        return None
