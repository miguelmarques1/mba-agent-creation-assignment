"""Dublês de teste: LLM roteirizado e ToolContext simples (sem Gemini)."""

from types import SimpleNamespace

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types


def resposta_texto(texto: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=texto)]))


def resposta_chamada(nome: str, /, **args) -> LlmResponse:
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


class FakeActions:
    def __init__(self):
        self.skip_summarization = False


class FakeToolContext:
    """`ToolContext` mínimo das tools de reservas: state, confirmação e pedidos registrados."""

    def __init__(self, apartamento="101", tool_confirmation=None, function_call_id="fc-1"):
        self.state = {} if apartamento is None else {"apartamento": apartamento}
        self.function_call_id = function_call_id
        self.tool_confirmation = tool_confirmation
        self.actions = FakeActions()
        self.confirmacoes_pedidas: list[dict] = []

    def request_confirmation(self, *, hint=None, payload=None):
        self.confirmacoes_pedidas.append({"hint": hint, "payload": payload})


def contexto_reservas(apartamento="101", confirmado: bool | None = None) -> FakeToolContext:
    """Contexto do apartamento; `confirmado` None = primeira execução, bool = reexecução."""
    from google.adk.tools.tool_confirmation import ToolConfirmation

    confirmacao = None if confirmado is None else ToolConfirmation(confirmed=confirmado)
    return FakeToolContext(apartamento, confirmacao)


_MARCADORES_DE_AGENTE = {
    "assistente": "Você é o assistente virtual",
    "reservas": "Você é o especialista em reservas",
    "visitantes": "Você é o especialista em visitantes",
    "regulamento": "Você é o especialista no regulamento",
}


class LlmPorAgente(BaseLlm):
    """Um LLM roteirizado por agente: identifica o agente pela `system_instruction`.

    `roteiros` mapeia o nome do agente à lista ordenada de respostas dele; `pedidos` guarda,
    por agente, os pedidos recebidos. Pode ser injetado como modelo principal e especialista.
    """

    model: str = "por-agente"
    roteiros: dict
    pedidos: dict

    def _agente(self, llm_request: LlmRequest) -> str:
        instrucao = str(llm_request.config.system_instruction)
        for nome, marcador in _MARCADORES_DE_AGENTE.items():
            if marcador in instrucao:
                return nome
        raise AssertionError("pedido de agente desconhecido")

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        nome = self._agente(llm_request)
        vistos = self.pedidos.setdefault(nome, [])
        vistos.append(llm_request)
        roteiro = self.roteiros.get(nome, [])
        if len(vistos) > len(roteiro):
            raise AssertionError(f"roteiro de {nome} esgotado ({len(vistos)} chamadas)")
        yield roteiro[len(vistos) - 1]


def novo_llm_por_agente(roteiros: dict) -> LlmPorAgente:
    return LlmPorAgente(roteiros={k: list(v) for k, v in roteiros.items()}, pedidos={})
