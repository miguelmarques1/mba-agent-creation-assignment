"""Mapa `session_id -> apartamento` e registro de confirmações pendentes (aurora.db).

Funções síncronas, uma conexão por chamada, no padrão de `repositorio.py`.
"""

import json
from pathlib import Path

from aurora.storage.db import conectar
from aurora.storage.modelos import ConfirmacaoPendente


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
    db_path: Path | None = None,
) -> None:
    """Grava a pendência; repetir o mesmo `id` não altera nada (`INSERT OR IGNORE`)."""
    with conectar(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO confirmacoes (id, session_id, acao, detalhes) "
            "VALUES (?, ?, ?, ?)",
            (confirmacao_id, session_id, acao, json.dumps(detalhes, ensure_ascii=False)),
        )


def listar_pendentes(session_id: str, db_path: Path | None = None) -> list[ConfirmacaoPendente]:
    with conectar(db_path) as conn:
        linhas = conn.execute(
            "SELECT id, acao, detalhes FROM confirmacoes "
            "WHERE session_id = ? AND status = 'pendente' ORDER BY criada_em, rowid",
            (session_id,),
        ).fetchall()
    return [ConfirmacaoPendente(i, a, json.loads(d)) for i, a, d in linhas]
