"""Conexão SQLite, schema do domínio e transação de escrita."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aurora.config import get_settings

TABELAS_DOMINIO = ("apartamentos", "areas", "reservas", "visitantes")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS apartamentos (
    numero  TEXT PRIMARY KEY,
    morador TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id   TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    taxa REAL NOT NULL CHECK (taxa >= 0)
);

CREATE TABLE IF NOT EXISTS reservas (
    codigo      TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL REFERENCES apartamentos(numero),
    area        TEXT NOT NULL REFERENCES areas(id),
    data        TEXT NOT NULL CHECK (data GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    status      TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_reservas_area_data_ativa
    ON reservas(area, data) WHERE status = 'ativa';
CREATE INDEX IF NOT EXISTS ix_reservas_apartamento ON reservas(apartamento, status, data);

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY,
    apartamento TEXT NOT NULL REFERENCES apartamentos(numero),
    nome        TEXT NOT NULL CHECK (length(trim(nome)) BETWEEN 2 AND 80),
    data        TEXT NOT NULL CHECK (data GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_visitantes_apto_nome_data
    ON visitantes(apartamento, nome COLLATE NOCASE, data);
CREATE INDEX IF NOT EXISTS ix_visitantes_apartamento ON visitantes(apartamento, data);

CREATE TABLE IF NOT EXISTS sessoes (
    session_id  TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    criada_em   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS confirmacoes (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessoes(session_id),
    acao          TEXT NOT NULL,
    detalhes      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pendente' CHECK (status IN ('pendente', 'respondida')),
    confirmado    INTEGER CHECK (confirmado IN (0, 1)),
    criada_em     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    respondida_em TEXT
);

CREATE INDEX IF NOT EXISTS ix_confirmacoes_sessao_status
    ON confirmacoes(session_id, status, criada_em);

PRAGMA user_version = 2;
"""


@contextmanager
def conectar(
    db_path: Path | None = None, busy_timeout_ms: int = 5000
) -> Iterator[sqlite3.Connection]:
    """Abre uma conexão configurada (WAL, busy_timeout, FKs) e a fecha ao sair."""
    path = Path(db_path) if db_path is not None else get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=busy_timeout_ms / 1000, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
    finally:
        conn.close()


def criar_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


@contextmanager
def transacao_escrita(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE` ... `COMMIT`, com `ROLLBACK` em qualquer exceção."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
