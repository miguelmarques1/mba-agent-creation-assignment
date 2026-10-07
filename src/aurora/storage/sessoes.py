"""Mapa `session_id -> apartamento` e registro de confirmações pendentes (aurora.db).

Funções síncronas, uma conexão por chamada, no padrão de `repositorio.py`.
"""

import json
from pathlib import Path

from aurora.storage.db import conectar
from aurora.storage.modelos import Confirmacao, ConfirmacaoPendente


def registrar_sessao(session_id: str, apartamento: str, db_path: Path | None = None) -> None:
    with conectar(db_path) as conn:
        conn.execute(
            "INSERT INTO sessoes (session_id, apartamento) VALUES (?, ?)",
            (session_id, apartamento),
        )


def apartamento_da_sessao_id(session_id: str, db_path: Path | None = None) -> str | None:
    with conectar(db_path) as conn:
        linha = conn.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        ).fetchone()
    return None if linha is None else str(linha[0])


def registrar_pendencia(
    confirmacao_id: str,
    session_id: str,
    acao: str,
    detalhes: dict,
    agente: str | None = None,
    chamada_original_id: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Grava a pendência; repetir o mesmo `id` não altera nada (`INSERT OR IGNORE`)."""
    with conectar(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO confirmacoes "
            "(id, session_id, acao, detalhes, agente, chamada_original_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                confirmacao_id,
                session_id,
                acao,
                json.dumps(detalhes, ensure_ascii=False),
                agente,
                chamada_original_id,
            ),
        )


def listar_pendentes(session_id: str, db_path: Path | None = None) -> list[ConfirmacaoPendente]:
    with conectar(db_path) as conn:
        linhas = conn.execute(
            "SELECT id, acao, detalhes FROM confirmacoes "
            "WHERE session_id = ? AND status = 'pendente' ORDER BY criada_em, rowid",
            (session_id,),
        ).fetchall()
    return [ConfirmacaoPendente(i, a, json.loads(d)) for i, a, d in linhas]


def obter_pendente(
    confirmacao_id: str, session_id: str, db_path: Path | None = None
) -> Confirmacao | None:
    """Linha `pendente` do `id` nesta sessão; `None` se inexistente, de outra sessão ou fechada."""
    with conectar(db_path) as conn:
        linha = conn.execute(
            "SELECT id, session_id, acao, detalhes, status, agente, chamada_original_id "
            "FROM confirmacoes WHERE id = ? AND session_id = ? AND status = 'pendente'",
            (confirmacao_id, session_id),
        ).fetchone()
    if linha is None:
        return None
    i, s, a, d, st, ag, co = linha
    return Confirmacao(i, s, a, json.loads(d), st, ag, co)


def responder_pendencia(
    confirmacao_id: str, session_id: str, confirmado: bool, db_path: Path | None = None
) -> bool:
    """Garantia 1: `UPDATE` atômico condicionado a `status='pendente'`; só a 1ª resposta vence."""
    with conectar(db_path) as conn:
        cursor = conn.execute(
            "UPDATE confirmacoes SET status = 'respondida', confirmado = ?, "
            "respondida_em = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND session_id = ? AND status = 'pendente'",
            (1 if confirmado else 0, confirmacao_id, session_id),
        )
        return cursor.rowcount == 1


def expirar_pendencias(
    session_id: str, agente_ativo: str, db_path: Path | None = None
) -> list[str]:
    """Expira as pendências cujo solicitante não é o agente ativo; devolve os ids expirados.

    Linhas sem `agente` (legado v2) não expiram.
    """
    with conectar(db_path) as conn:
        linhas = conn.execute(
            "UPDATE confirmacoes SET status = 'expirada', "
            "respondida_em = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE session_id = ? AND status = 'pendente' "
            "AND agente IS NOT NULL AND agente <> ? RETURNING id",
            (session_id, agente_ativo),
        ).fetchall()
    return [r[0] for r in linhas]
