"""Tools de reservas: o apartamento vem do state, a taxa vem do banco e a cobrança exige confirmação."""

import asyncio
import re
import sqlite3
import unicodedata

from google.adk.tools import ToolContext

from aurora.storage import repositorio as repo
from aurora.storage.modelos import Area
from aurora.tools.sessao import apartamento_da_sessao

ACAO_RESERVAR_AREA = "reservar_area"

MSG_AREA_INVALIDA = (
    "A área informada não pode ser reservada. "
    "Áreas disponíveis: salão de festas, churrasqueira e quadra."
)
MSG_DATA_INVALIDA = "Informe a data no formato AAAA-MM-DD."
MSG_OCUPADA = "Essa data já está ocupada para essa área."
MSG_OCUPADA_APOS_APROVACAO = "Essa data acabou de ser ocupada; sua reserva não foi criada."
MSG_NAO_CONFIRMADA = "Reserva não confirmada; nada foi feito."
MSG_AGUARDANDO = "Aguardando a confirmação do morador."
MSG_NAO_ENCONTREI_AREA_DATA = "Não encontrei reserva sua para essa área nessa data."
MSG_NAO_ENCONTREI_CODIGO = "Não encontrei reserva sua com esse código."
MSG_IDENTIFICACAO_CANCELAMENTO = "Informe a área e a data da reserva, ou o código."
MSG_CANCELADA = "Reserva cancelada."


def normalizar_texto_area(texto: str) -> str:
    """casefold, sem acentos, espaços e `_` viram `-`."""
    sem_acento = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[\s_]+", "-", sem_acento.strip().casefold())


def _resolver(texto: str, areas: list[Area]) -> Area | None:
    chave = normalizar_texto_area(texto)
    for area in areas:
        if chave in (normalizar_texto_area(area.id), normalizar_texto_area(area.nome)):
            return area
    return None


def resolver_area(texto: str) -> Area | None:
    """Resolve id ou nome (tolerante a caixa e acentos) contra o catálogo do banco."""
    return _resolver(texto, repo.listar_areas())


async def _area(texto: str) -> Area | None:
    return _resolver(texto, await asyncio.to_thread(repo.listar_areas))


def _erro(mensagem: str, **extra) -> dict:
    return {"status": "error", "message": mensagem, **extra}


def _area_invalida() -> dict:
    return _erro(MSG_AREA_INVALIDA, areas_validas=["churrasqueira", "quadra", "salao-de-festas"])


def _taxa_brl(taxa: float) -> str:
    return f"{taxa:.2f}".replace(".", ",")


async def listar_areas() -> dict:
    """Lista as áreas comuns reserváveis com nome e taxa de reserva.

    Returns:
        dict com `status` e `areas`, lista de `{id, nome, taxa}` (taxa em reais).
    """
    areas = await asyncio.to_thread(repo.listar_areas)
    return {
        "status": "success",
        "areas": [{"id": a.id, "nome": a.nome, "taxa": a.taxa} for a in areas],
    }


async def consultar_disponibilidade(area: str, data: str, tool_context: ToolContext) -> dict:
    """Informa se uma área comum está livre em uma data, sem dizer quem a reservou.

    Args:
        area: id ou nome da área (salão de festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.

    Returns:
        dict com `status`, `area`, `data` e `disponivel` (true = livre).
    """
    apartamento_da_sessao(tool_context)
    resolvida = await _area(area)
    if resolvida is None:
        return _area_invalida()
    if not repo.data_valida(data):
        return _erro(MSG_DATA_INVALIDA)
    ocupada = await asyncio.to_thread(repo.area_ocupada, resolvida.id, data)
    return {"status": "success", "area": resolvida.id, "data": data, "disponivel": not ocupada}


async def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador.

    Returns:
        dict com `status` e `reservas`, lista de `{codigo, area, data}`.
    """
    apartamento = apartamento_da_sessao(tool_context)
    reservas = await asyncio.to_thread(repo.listar_reservas_ativas, apartamento)
    return {"status": "success", "reservas": [r.para_dict() for r in reservas]}


async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador.

    Áreas com taxa (salão de festas, churrasqueira) exigem a confirmação do morador: a tool
    devolve `pending` e nada é gravado até a confirmação. A quadra é gravada direto.

    Args:
        area: id ou nome da área (salão de festas, churrasqueira ou quadra).
        data: data da reserva no formato AAAA-MM-DD.

    Returns:
        dict com `status` (`success`, `pending`, `cancelled` ou `error`) e `message`.
    """
    apartamento = apartamento_da_sessao(tool_context)
    resolvida = await _area(area)
    if resolvida is None:
        return _area_invalida()
    if not repo.data_valida(data):
        return _erro(MSG_DATA_INVALIDA)

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        if await asyncio.to_thread(repo.area_ocupada, resolvida.id, data):
            return _erro(MSG_OCUPADA)
        if resolvida.taxa > 0:
            detalhes = {"area": resolvida.id, "data": data, "taxa": float(resolvida.taxa)}
            tool_context.request_confirmation(
                hint=(
                    f"Confirmar a reserva de {resolvida.nome} em {data} "
                    f"com taxa de R$ {_taxa_brl(resolvida.taxa)}?"
                ),
                payload={"acao": ACAO_RESERVAR_AREA, "detalhes": detalhes},
            )
            tool_context.actions.skip_summarization = True
            return {"status": "pending", "message": MSG_AGUARDANDO, "detalhes": detalhes}
    elif confirmacao.confirmed is not True:
        return {"status": "cancelled", "message": MSG_NAO_CONFIRMADA}

    resultado = await asyncio.to_thread(repo.criar_reserva, apartamento, resolvida.id, data)
    match resultado.status:
        case "criada":
            return {
                "status": "success",
                "codigo": resultado.codigo,
                "area": resolvida.id,
                "data": data,
                "taxa": float(resolvida.taxa),
                "message": (
                    f"Reserva criada: {resolvida.nome} em {data}, código {resultado.codigo}."
                ),
            }
        case "ocupada":
            return _erro(MSG_OCUPADA if confirmacao is None else MSG_OCUPADA_APOS_APROVACAO)
        case "area_invalida":
            return _area_invalida()
        case "data_invalida":
            return _erro(MSG_DATA_INVALIDA)
        case _:
            return _erro(resultado.mensagem or repo.MSG_ERRO_GRAVACAO)


async def cancelar_reserva(
    tool_context: ToolContext,
    area: str | None = None,
    data: str | None = None,
    codigo: str | None = None,
) -> dict:
    """Cancela uma reserva do apartamento do morador, sem pedir confirmação.

    Identifique a reserva pelo código ou pela área e data.

    Args:
        area: id ou nome da área, usado junto com `data`.
        data: data da reserva no formato AAAA-MM-DD, usada junto com `area`.
        codigo: código da reserva (ex.: RSV-1377); tem precedência sobre área e data.

    Returns:
        dict com `status` (`success` ou `error`) e `message`.
    """
    apartamento = apartamento_da_sessao(tool_context)
    try:
        if codigo and str(codigo).strip():
            codigo_norm = str(codigo).strip().upper()
            if await asyncio.to_thread(repo.cancelar_reserva_por_codigo, apartamento, codigo_norm):
                return {"status": "success", "codigo": codigo_norm, "message": MSG_CANCELADA}
            return _erro(MSG_NAO_ENCONTREI_CODIGO)

        if not area or not data:
            return _erro(MSG_IDENTIFICACAO_CANCELAMENTO)
        resolvida = await _area(area)
        if resolvida is None:
            return _area_invalida()
        if not repo.data_valida(data):
            return _erro(MSG_DATA_INVALIDA)
        if await asyncio.to_thread(repo.cancelar_reserva, apartamento, resolvida.id, data):
            return {
                "status": "success",
                "area": resolvida.id,
                "data": data,
                "message": MSG_CANCELADA,
            }
        return _erro(MSG_NAO_ENCONTREI_AREA_DATA)
    except sqlite3.OperationalError:
        return _erro(repo.MSG_BANCO_OCUPADO)


RESERVAS_TOOLS = (
    consultar_disponibilidade,
    listar_minhas_reservas,
    reservar_area,
    cancelar_reserva,
    listar_areas,
)
