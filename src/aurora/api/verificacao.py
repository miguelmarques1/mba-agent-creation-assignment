"""Rotas de verificação: leitura direta do SQLite, sem agentes e sem cache."""

from fastapi import APIRouter
from pydantic import BaseModel

from aurora.storage import listar_reservas_ativas, listar_visitantes

router = APIRouter(tags=["verificacao"])


class ReservaVerificacao(BaseModel):
    codigo: str
    area: str
    data: str


class VisitanteVerificacao(BaseModel):
    nome: str
    data: str


@router.get("/apartamentos/{numero}/reservas", response_model=list[ReservaVerificacao])
def listar_reservas_do_apartamento(numero: str) -> list[dict[str, str]]:
    return [r.para_dict() for r in listar_reservas_ativas(numero)]


@router.get("/apartamentos/{numero}/visitantes", response_model=list[VisitanteVerificacao])
def listar_visitantes_do_apartamento(numero: str) -> list[dict[str, str]]:
    return [v.para_dict() for v in listar_visitantes(numero)]
