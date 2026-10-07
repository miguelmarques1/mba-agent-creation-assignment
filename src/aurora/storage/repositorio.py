"""Acesso ao domínio: única porta de leitura e gravação para tools e rotas.

Funções síncronas, uma conexão por chamada. Violações do banco viram resultados de negócio,
nunca exceções. Nenhuma função daqui apaga reservas: cancelar é `UPDATE` de status.
"""

import logging
import re
import secrets
import sqlite3
import string
from datetime import date
from pathlib import Path

from aurora.storage.db import conectar, transacao_escrita
from aurora.storage.modelos import (
    Area,
    Reserva,
    ResultadoReserva,
    ResultadoVisitante,
    Visitante,
)

logger = logging.getLogger("aurora.storage")

_ALFABETO_CODIGO = string.ascii_uppercase + string.digits
_RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

MSG_CODIGO_ESGOTADO = "Não foi possível gerar um código de reserva; tente novamente."
MSG_BANCO_OCUPADO = "O sistema está ocupado; tente novamente em instantes."
MSG_ERRO_GRAVACAO = "Não foi possível gravar agora; tente novamente."


def data_valida(data: str) -> bool:
    """`True` só para `AAAA-MM-DD` de calendário válido."""
    if not isinstance(data, str) or not _RE_DATA.fullmatch(data):
        return False
    try:
        date.fromisoformat(data)
    except ValueError:
        return False
    return True


def gerar_codigo() -> str:
    return "RSV-" + "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(6))


def _normalizar_nome(nome: str) -> str:
    return " ".join(nome.split())


def listar_areas(db_path: Path | None = None) -> list[Area]:
    with conectar(db_path) as conn:
        linhas = conn.execute("SELECT id, nome, taxa FROM areas ORDER BY id").fetchall()
    return [Area(*linha) for linha in linhas]


def obter_area(area_id: str, db_path: Path | None = None) -> Area | None:
    with conectar(db_path) as conn:
        linha = conn.execute("SELECT id, nome, taxa FROM areas WHERE id = ?", (area_id,)).fetchone()
    return Area(*linha) if linha else None


def apartamento_existe(numero: str, db_path: Path | None = None) -> bool:
    with conectar(db_path) as conn:
        return (
            conn.execute("SELECT 1 FROM apartamentos WHERE numero = ?", (numero,)).fetchone()
            is not None
        )


def area_ocupada(area: str, data: str, db_path: Path | None = None) -> bool:
    """Só o booleano: nunca devolve dono nem código da reserva."""
    with conectar(db_path) as conn:
        linha = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa')",
            (area, data),
        ).fetchone()
    return bool(linha[0])


def listar_reservas_ativas(apartamento: str, db_path: Path | None = None) -> list[Reserva]:
    with conectar(db_path) as conn:
        linhas = conn.execute(
            "SELECT codigo, area, data FROM reservas "
            "WHERE apartamento = ? AND status = 'ativa' ORDER BY data, codigo",
            (apartamento,),
        ).fetchall()
    return [Reserva(*linha) for linha in linhas]


def criar_reserva(
    apartamento: str,
    area: str,
    data: str,
    *,
    gerar=gerar_codigo,
    tentativas: int = 5,
    db_path: Path | None = None,
    busy_timeout_ms: int = 5000,
) -> ResultadoReserva:
    """Grava uma reserva ativa; quem decide a exclusividade é o índice único parcial."""
    if not data_valida(data):
        return ResultadoReserva(status="data_invalida")
    try:
        with conectar(db_path, busy_timeout_ms) as conn:
            if conn.execute("SELECT 1 FROM areas WHERE id = ?", (area,)).fetchone() is None:
                return ResultadoReserva(status="area_invalida")
            for _ in range(tentativas):
                codigo = gerar()
                try:
                    with transacao_escrita(conn):
                        conn.execute(
                            "INSERT INTO reservas (codigo, apartamento, area, data, status) "
                            "VALUES (?, ?, ?, ?, 'ativa')",
                            (codigo, apartamento, area, data),
                        )
                except sqlite3.IntegrityError as exc:
                    nome = getattr(exc, "sqlite_errorname", "")
                    if nome == "SQLITE_CONSTRAINT_PRIMARYKEY":
                        continue
                    if nome == "SQLITE_CONSTRAINT_UNIQUE":
                        return ResultadoReserva(status="ocupada")
                    logger.error("Falha de integridade ao criar reserva: %s", exc)
                    return ResultadoReserva(status="erro", mensagem=MSG_ERRO_GRAVACAO)
                return ResultadoReserva(status="criada", codigo=codigo)
            return ResultadoReserva(status="erro", mensagem=MSG_CODIGO_ESGOTADO)
    except sqlite3.OperationalError as exc:
        logger.error("Banco indisponível ao criar reserva: %s", exc)
        return ResultadoReserva(status="erro", mensagem=MSG_BANCO_OCUPADO)


def _cancelar(clausula: str, params: tuple, db_path: Path | None) -> bool:
    with conectar(db_path) as conn, transacao_escrita(conn):
        cursor = conn.execute(
            f"UPDATE reservas SET status = 'cancelada' WHERE {clausula} AND status = 'ativa'",
            params,
        )
        return cursor.rowcount == 1


def cancelar_reserva(apartamento: str, area: str, data: str, db_path: Path | None = None) -> bool:
    return _cancelar(
        "apartamento = ? AND area = ? AND data = ?", (apartamento, area, data), db_path
    )


def cancelar_reserva_por_codigo(apartamento: str, codigo: str, db_path: Path | None = None) -> bool:
    return _cancelar("codigo = ? AND apartamento = ?", (codigo, apartamento), db_path)


def autorizar_visitante(
    apartamento: str, nome: str, data: str, db_path: Path | None = None
) -> ResultadoVisitante:
    nome_norm = _normalizar_nome(nome) if isinstance(nome, str) else ""
    if not 2 <= len(nome_norm) <= 80:
        return ResultadoVisitante(status="nome_invalido")
    if not data_valida(data):
        return ResultadoVisitante(status="data_invalida")
    try:
        with conectar(db_path) as conn, transacao_escrita(conn):
            conn.execute(
                "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
                (apartamento, nome_norm, data),
            )
    except sqlite3.IntegrityError as exc:
        if getattr(exc, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_UNIQUE":
            return ResultadoVisitante(status="ja_autorizado")
        logger.error("Falha de integridade ao autorizar visitante: %s", exc)
        return ResultadoVisitante(status="erro", mensagem=MSG_ERRO_GRAVACAO)
    except sqlite3.OperationalError as exc:
        logger.error("Banco indisponível ao autorizar visitante: %s", exc)
        return ResultadoVisitante(status="erro", mensagem=MSG_BANCO_OCUPADO)
    return ResultadoVisitante(status="autorizado")


def listar_visitantes(apartamento: str, db_path: Path | None = None) -> list[Visitante]:
    with conectar(db_path) as conn:
        linhas = conn.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome",
            (apartamento,),
        ).fetchall()
    return [Visitante(*linha) for linha in linhas]
