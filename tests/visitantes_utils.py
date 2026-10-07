"""Dublê de `ToolContext` para as tools de visitantes."""


class FakeConfirmContext:
    def __init__(self, apartamento="101", confirmacao=None, function_call_id="fc-1"):
        self.state = {} if apartamento is None else {"apartamento": apartamento}
        self.function_call_id = function_call_id
        self.tool_confirmation = confirmacao
        self.pedidos: list[tuple[str, object]] = []

    def request_confirmation(self, *, hint=None, payload=None):
        self.pedidos.append((hint, payload))
