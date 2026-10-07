"""Dublês de teste: LLM roteirizado e ToolContext simples (sem Gemini)."""

from types import SimpleNamespace

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types


def resposta_texto(texto: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=texto)]))


def resposta_chamada(nome: str, **args) -> LlmResponse:
    parte = types.Part(function_call=types.FunctionCall(name=nome, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[parte]))


class ScriptedLlm(BaseLlm):
    """Devolve, a cada chamada, a próxima resposta da lista e registra os pedidos."""

    model: str = "scripted"
    respostas: list
    pedidos: list

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        self.pedidos.append(llm_request)
        yield self.respostas[len(self.pedidos) - 1]


def novo_llm(respostas: list) -> ScriptedLlm:
    return ScriptedLlm(respostas=list(respostas), pedidos=[])


def evento_chamadas(invocation_id: str, chamadas: list[tuple[str, dict]]):
    """Evento simples com function_calls de `ler_capitulo`: cada item é (id, args)."""
    partes = [
        types.Part(function_call=types.FunctionCall(id=fid, name="ler_capitulo", args=args))
        for fid, args in chamadas
    ]
    return SimpleNamespace(
        invocation_id=invocation_id, content=types.Content(role="model", parts=partes)
    )


def fake_tool_context(events, invocation_id: str, function_call_id: str):
    return SimpleNamespace(
        session=SimpleNamespace(events=events),
        invocation_id=invocation_id,
        function_call_id=function_call_id,
        state={},
    )
