"""Camada de armazenamento do condomínio (SQLite): schema, repositório e restauração."""

from aurora.storage.carga import ErroRestauracao, inicializar_banco, restaurar
from aurora.storage.db import conectar
from aurora.storage.modelos import (
    Area,
    ConfirmacaoPendente,
    Reserva,
    ResultadoReserva,
    ResultadoVisitante,
    ResumoRestauracao,
    Visitante,
)
from aurora.storage.repositorio import (
    apartamento_existe,
    area_ocupada,
    autorizar_visitante,
    cancelar_reserva,
    cancelar_reserva_por_codigo,
    criar_reserva,
    data_valida,
    gerar_codigo,
    listar_areas,
    listar_reservas_ativas,
    listar_visitantes,
    obter_area,
)
from aurora.storage.sessoes import (
    apartamento_da_sessao_id,
    listar_pendentes,
    registrar_pendencia,
    registrar_sessao,
)

__all__ = [
    "Area",
    "ConfirmacaoPendente",
    "ErroRestauracao",
    "Reserva",
    "ResultadoReserva",
    "ResultadoVisitante",
    "ResumoRestauracao",
    "Visitante",
    "apartamento_da_sessao_id",
    "apartamento_existe",
    "area_ocupada",
    "autorizar_visitante",
    "cancelar_reserva",
    "cancelar_reserva_por_codigo",
    "conectar",
    "criar_reserva",
    "data_valida",
    "gerar_codigo",
    "inicializar_banco",
    "listar_areas",
    "listar_pendentes",
    "listar_reservas_ativas",
    "listar_visitantes",
    "obter_area",
    "registrar_pendencia",
    "registrar_sessao",
    "restaurar",
]
