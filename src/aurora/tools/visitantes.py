"""Tools de visitantes: o apartamento vem do state da sessão e a autorização exige confirmação."""

import asyncio

from google.adk.tools import ToolContext

from aurora import storage

APARTAMENTO_KEY = "apartamento"
ACAO_AUTORIZAR_VISITANTE = "autorizar_visitante"

MSG_ERRO_INESPERADO = "Ocorreu um erro inesperado ao processar sua solicitação."
MSG_NOME_INVALIDO = "Informe o nome completo do visitante."
MSG_NOME_LONGO = "O nome do visitante deve ter no máximo 80 caracteres."
MSG_DATA_INVALIDA = "Informe a data no formato AAAA-MM-DD."
MSG_JA_AUTORIZADO = "Esse visitante já está autorizado nessa data."
MSG_PENDENTE = "Aguardando a confirmação do morador no aplicativo."
MSG_NAO_CONFIRMADO = "Autorização não confirmada; ninguém foi liberado."
MSG_SEM_VISITANTES = "Não há visitantes autorizados para o seu apartamento."

_NOME_MIN, _NOME_MAX = 2, 80


def _apartamento_da_sessao(tool_context: ToolContext) -> str:
    apartamento = tool_context.state.get(APARTAMENTO_KEY)
    if not apartamento:
        raise RuntimeError("Sessão sem apartamento no state.")
    return str(apartamento)


def _normalizar_nome(nome: str) -> str:
    return " ".join(nome.split())


def _dobrar_caixa_ascii(texto: str) -> str:
    """Igual ao `COLLATE NOCASE` do SQLite: só A-Z viram minúsculas."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in texto)


def _erro(mensagem: str) -> dict:
    return {"status": "error", "message": mensagem}


def _validar(nome: str, data: str) -> dict | None:
    """Devolve o retorno de erro da tool, ou `None` se nome e data são válidos."""
    if not _NOME_MIN <= len(nome) <= _NOME_MAX:
        return _erro(MSG_NOME_LONGO if len(nome) > _NOME_MAX else MSG_NOME_INVALIDO)
    if not storage.data_valida(data):
        return _erro(MSG_DATA_INVALIDA)
    return None


async def _ja_autorizado(apartamento: str, nome: str, data: str) -> bool:
    chave = _dobrar_caixa_ascii(nome)
    visitantes = await asyncio.to_thread(storage.listar_visitantes, apartamento)
    return any(v.data == data and _dobrar_caixa_ascii(v.nome) == chave for v in visitantes)


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no apartamento do morador.

    Sempre exige confirmação do morador pelo aplicativo; nunca considere a autorização
    feita só porque o morador disse que confirma.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.

    Returns:
        dict com `status` (`pending` aguardando confirmação, `success`, `cancelled` ou
        `error`) e `message`.
    """
    apartamento = _apartamento_da_sessao(tool_context)
    nome_norm = _normalizar_nome(nome) if isinstance(nome, str) else ""
    erro = _validar(nome_norm, data)
    if erro:
        return erro
    if await _ja_autorizado(apartamento, nome_norm, data):
        return {"status": "success", "message": MSG_JA_AUTORIZADO}

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(
            hint=f"Confirmar a autorização de entrada de {nome_norm} em {data}?",
            payload={
                "acao": ACAO_AUTORIZAR_VISITANTE,
                "detalhes": {"nome": nome_norm, "data": data},
            },
        )
        return {"status": "pending", "message": MSG_PENDENTE}
    if confirmacao.confirmed is not True:
        return {"status": "cancelled", "message": MSG_NAO_CONFIRMADO}

    resultado = await asyncio.to_thread(storage.autorizar_visitante, apartamento, nome_norm, data)
    match resultado.status:
        case "autorizado":
            return {
                "status": "success",
                "message": f"Entrada de {nome_norm} autorizada para {data}.",
                "visitante": {"nome": nome_norm, "data": data},
            }
        case "ja_autorizado":
            return {"status": "success", "message": MSG_JA_AUTORIZADO}
        case "nome_invalido":
            return _erro(MSG_NOME_INVALIDO)
        case "data_invalida":
            return _erro(MSG_DATA_INVALIDA)
        case _:
            return _erro(resultado.mensagem or MSG_ERRO_INESPERADO)


async def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados no apartamento do morador.

    Returns:
        dict com `status` e `visitantes`, lista de `{nome, data}`.
    """
    apartamento = _apartamento_da_sessao(tool_context)
    visitantes = await asyncio.to_thread(storage.listar_visitantes, apartamento)
    resposta: dict = {"status": "success", "visitantes": [v.para_dict() for v in visitantes]}
    if not visitantes:
        resposta["message"] = MSG_SEM_VISITANTES
    return resposta


TOOLS_VISITANTES = [autorizar_visitante, listar_meus_visitantes]
