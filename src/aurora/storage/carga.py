"""Leitura de `dados/`, restauração transacional e comando `aurora-restore`."""

import json
import sqlite3
import sys
from pathlib import Path

from aurora.storage.db import TABELAS_DOMINIO, conectar, criar_schema, transacao_escrita
from aurora.storage.modelos import ResumoRestauracao
from aurora.storage.repositorio import data_valida

DADOS_DIR_PADRAO = Path("dados")
_SEM_ALTERACAO = "Nenhuma alteração aplicada."


class ErroRestauracao(Exception):
    """Falha ao ler/validar `dados/` ou ao gravar; a mensagem é pronta para o usuário."""


def _falha(arquivo: str, detalhe: str) -> ErroRestauracao:
    return ErroRestauracao(f"Falha ao restaurar: dados/{arquivo} {detalhe}. {_SEM_ALTERACAO}")


def _invalido(arquivo: str, registro: int, motivo: str) -> ErroRestauracao:
    return _falha(arquivo, f"inválido (registro {registro}: {motivo})")


def _texto(arquivo: str, k: int, reg: dict, campo: str) -> str:
    valor = reg.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise _invalido(arquivo, k, f"campo '{campo}' ausente ou vazio")
    return valor


def _data(arquivo: str, k: int, reg: dict) -> str:
    valor = _texto(arquivo, k, reg, "data")
    if not data_valida(valor):
        raise _invalido(arquivo, k, "campo 'data' fora do formato AAAA-MM-DD")
    return valor


def _carregar(dados_dir: Path, arquivo: str) -> list[dict]:
    caminho = Path(dados_dir) / arquivo
    try:
        with open(caminho, encoding="utf-8") as f:
            conteudo = json.load(f)
    except FileNotFoundError:
        raise _falha(arquivo, "ausente") from None
    except json.JSONDecodeError as exc:
        raise _falha(arquivo, f"inválido (linha {exc.lineno})") from None
    except UnicodeDecodeError:
        raise _falha(arquivo, "inválido (codificação não é UTF-8)") from None
    if not isinstance(conteudo, list):
        raise _falha(arquivo, "inválido (esperada uma lista de objetos)")
    for k, reg in enumerate(conteudo, start=1):
        if not isinstance(reg, dict):
            raise _invalido(arquivo, k, "esperado um objeto")
    return conteudo


def ler_dados(dados_dir: Path = DADOS_DIR_PADRAO) -> dict[str, list[tuple]]:
    """Lê e valida os quatro arquivos (somente leitura). Devolve tuplas prontas para `INSERT`."""
    arq = "apartamentos.json"
    apartamentos, vistos = [], set()
    for k, reg in enumerate(_carregar(dados_dir, arq), start=1):
        numero, morador = _texto(arq, k, reg, "numero"), _texto(arq, k, reg, "morador")
        if numero in vistos:
            raise _invalido(arq, k, f"numero '{numero}' duplicado")
        vistos.add(numero)
        apartamentos.append((numero, morador))

    arq = "areas.json"
    areas, ids = [], set()
    for k, reg in enumerate(_carregar(dados_dir, arq), start=1):
        id_, nome = _texto(arq, k, reg, "id"), _texto(arq, k, reg, "nome")
        taxa = reg.get("taxa")
        if isinstance(taxa, bool) or not isinstance(taxa, int | float) or taxa < 0:
            raise _invalido(arq, k, "campo 'taxa' deve ser numérico e >= 0")
        if id_ in ids:
            raise _invalido(arq, k, f"id '{id_}' duplicado")
        ids.add(id_)
        areas.append((id_, nome, float(taxa)))

    arq = "reservas.json"
    reservas, codigos, ocupados = [], set(), set()
    for k, reg in enumerate(_carregar(dados_dir, arq), start=1):
        codigo, apto = _texto(arq, k, reg, "codigo"), _texto(arq, k, reg, "apartamento")
        area, data = _texto(arq, k, reg, "area"), _data(arq, k, reg)
        if apto not in vistos:
            raise _invalido(arq, k, f"apartamento '{apto}' inexistente")
        if area not in ids:
            raise _invalido(arq, k, f"área '{area}' inexistente")
        if codigo in codigos:
            raise _invalido(arq, k, f"código '{codigo}' duplicado")
        if (area, data) in ocupados:
            raise _invalido(arq, k, f"área '{area}' já reservada em {data}")
        codigos.add(codigo)
        ocupados.add((area, data))
        reservas.append((codigo, apto, area, data, "ativa"))

    arq = "visitantes.json"
    visitantes, chaves = [], set()
    for k, reg in enumerate(_carregar(dados_dir, arq), start=1):
        apto, nome = _texto(arq, k, reg, "apartamento"), _texto(arq, k, reg, "nome")
        nome = " ".join(nome.split())
        data = _data(arq, k, reg)
        if apto not in vistos:
            raise _invalido(arq, k, f"apartamento '{apto}' inexistente")
        if not 2 <= len(nome) <= 80:
            raise _invalido(arq, k, "campo 'nome' deve ter de 2 a 80 caracteres")
        chave = (apto, nome.casefold(), data)
        if chave in chaves:
            raise _invalido(arq, k, "visitante duplicado")
        chaves.add(chave)
        visitantes.append((apto, nome, data))

    return {
        "apartamentos": apartamentos,
        "areas": areas,
        "reservas": reservas,
        "visitantes": visitantes,
    }


def _inserir_visitantes(conn: sqlite3.Connection, visitantes: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)", visitantes
    )


def _gravar(conn: sqlite3.Connection, dados: dict[str, list[tuple]]) -> None:
    # Ordem de DELETE respeita as FKs; só as 4 tabelas de domínio, nunca sessões.
    for tabela in ("visitantes", "reservas", "areas", "apartamentos"):
        conn.execute(f"DELETE FROM {tabela}")
    conn.executemany(
        "INSERT INTO apartamentos (numero, morador) VALUES (?, ?)", dados["apartamentos"]
    )
    conn.executemany("INSERT INTO areas (id, nome, taxa) VALUES (?, ?, ?)", dados["areas"])
    conn.executemany(
        "INSERT INTO reservas (codigo, apartamento, area, data, status) VALUES (?, ?, ?, ?, ?)",
        dados["reservas"],
    )
    _inserir_visitantes(conn, dados["visitantes"])


def _vazio(conn: sqlite3.Connection) -> bool:
    return all(
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
        for t in ("apartamentos", "areas")
    )


def _restaurar(
    db_path: Path | None, dados_dir: Path, busy_timeout_ms: int, somente_se_vazio: bool
) -> ResumoRestauracao | None:
    dados = ler_dados(dados_dir)
    try:
        with conectar(db_path, busy_timeout_ms) as conn:
            criar_schema(conn)
            with transacao_escrita(conn):
                if somente_se_vazio and not _vazio(conn):
                    return None
                _gravar(conn, dados)
    except sqlite3.OperationalError:
        raise ErroRestauracao(
            f"Falha ao restaurar: banco ocupado por outra operação. {_SEM_ALTERACAO}"
        ) from None
    return ResumoRestauracao(*(len(dados[t]) for t in TABELAS_DOMINIO))


def restaurar(
    db_path: Path | None = None,
    dados_dir: Path = DADOS_DIR_PADRAO,
    busy_timeout_ms: int = 5000,
) -> ResumoRestauracao:
    """Recarrega as 4 tabelas de domínio a partir de `dados/`, tudo ou nada."""
    resumo = _restaurar(db_path, dados_dir, busy_timeout_ms, somente_se_vazio=False)
    assert resumo is not None
    return resumo


def inicializar_banco(db_path: Path | None = None, dados_dir: Path = DADOS_DIR_PADRAO) -> bool:
    """Cria o schema e popula só se o banco não tem dados. `True` se populou."""
    with conectar(db_path) as conn:
        criar_schema(conn)
        if not _vazio(conn):
            return False
    return _restaurar(db_path, dados_dir, 5000, somente_se_vazio=True) is not None


def main(dados_dir: Path = DADOS_DIR_PADRAO) -> int:
    """Entry point de `aurora-restore`: imprime o resumo (exit 0) ou o erro em stderr (exit 1)."""
    try:
        resumo = restaurar(dados_dir=dados_dir)
    except ErroRestauracao as exc:
        print(exc, file=sys.stderr)
        return 1
    print(resumo)
    return 0
