"""Rotas de sessão e conversa: criar sessão, enviar mensagem e listar eventos."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from aurora.conversa import (
    ApartamentoInexistente,
    ErroModelo,
    ServicoConversa,
    SessaoNaoEncontrada,
)

router = APIRouter(tags=["sessoes"])

DETAIL_SESSAO = "Sessão não encontrada."
DETAIL_APARTAMENTO = "Apartamento inexistente."
DETAIL_MODELO = "Falha ao consultar o modelo; tente novamente."


class CriarSessaoIn(BaseModel):
    apartamento: str

    @field_validator("apartamento", mode="before")
    @classmethod
    def _normalizar(cls, valor: Any) -> Any:
        if isinstance(valor, int) and not isinstance(valor, bool):
            valor = str(valor)
        if isinstance(valor, str):
            valor = valor.strip()
            if not valor:
                raise ValueError("apartamento não pode ser vazio")
        return valor


class SessaoCriadaOut(BaseModel):
    session_id: str


class MensagemIn(BaseModel):
    texto: str

    @field_validator("texto")
    @classmethod
    def _nao_vazio(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("texto não pode ser vazio")
        return valor


class ConfirmacaoPendenteOut(BaseModel):
    id: str
    acao: str
    detalhes: dict[str, Any]


class RespostaTurnoOut(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[ConfirmacaoPendenteOut]


def _servico(request: Request) -> ServicoConversa:
    return request.app.state.servico


def _nao_encontrada() -> HTTPException:
    return HTTPException(status_code=404, detail=DETAIL_SESSAO)


@router.post("/sessoes", status_code=201, response_model=SessaoCriadaOut)
async def criar_sessao(corpo: CriarSessaoIn, request: Request) -> SessaoCriadaOut:
    try:
        session_id = await _servico(request).criar_sessao(corpo.apartamento)
    except ApartamentoInexistente:
        raise HTTPException(status_code=422, detail=DETAIL_APARTAMENTO) from None
    return SessaoCriadaOut(session_id=session_id)


@router.post("/sessoes/{session_id}/mensagens", response_model=RespostaTurnoOut)
async def enviar_mensagem(session_id: str, corpo: MensagemIn, request: Request) -> dict:
    try:
        resultado = await _servico(request).enviar_mensagem(session_id, corpo.texto)
    except SessaoNaoEncontrada:
        raise _nao_encontrada() from None
    except ErroModelo:
        raise HTTPException(status_code=502, detail=DETAIL_MODELO) from None
    return {
        "resposta": resultado.resposta,
        "confirmacoes_pendentes": resultado.confirmacoes_pendentes,
    }


@router.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str, request: Request) -> list[dict]:
    try:
        return await _servico(request).eventos(session_id)
    except SessaoNaoEncontrada:
        raise _nao_encontrada() from None
