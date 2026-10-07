"""Rota de confirmações: responde uma única vez a uma pendência registrada pela conversa."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictBool, field_validator

from aurora.api.sessoes import DETAIL_MODELO, DETAIL_SESSAO, RespostaTurnoOut
from aurora.conversa import (
    ConfirmacaoNaoPendente,
    ErroModelo,
    ServicoConversa,
    SessaoNaoEncontrada,
)

router = APIRouter(tags=["confirmacoes"])

DETAIL_NAO_PENDENTE = "Não existe confirmação pendente com esse id nesta sessão."


class ResponderConfirmacaoIn(BaseModel):
    id: str
    confirmado: StrictBool

    @field_validator("id")
    @classmethod
    def _normalizar(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("id não pode ser vazio")
        return valor


def _servico(request: Request) -> ServicoConversa:
    return request.app.state.servico


@router.post("/sessoes/{session_id}/confirmacoes", response_model=RespostaTurnoOut)
async def responder_confirmacao(
    session_id: str, corpo: ResponderConfirmacaoIn, request: Request
) -> dict:
    try:
        resultado = await _servico(request).responder_confirmacao(
            session_id, corpo.id, corpo.confirmado
        )
    except SessaoNaoEncontrada:
        raise HTTPException(status_code=404, detail=DETAIL_SESSAO) from None
    except ConfirmacaoNaoPendente:
        raise HTTPException(status_code=409, detail=DETAIL_NAO_PENDENTE) from None
    except ErroModelo:
        raise HTTPException(status_code=502, detail=DETAIL_MODELO) from None
    return {
        "resposta": resultado.resposta,
        "confirmacoes_pendentes": resultado.confirmacoes_pendentes,
    }
