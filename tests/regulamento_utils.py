"""Trechos-sentinela do regulamento, reutilizáveis pelas features F06 e F07."""

import re

from aurora.tools.regulamento import REGULAMENTO_PATH, indice_regulamento


def _trechos(texto: str, min_len: int) -> list[str]:
    trechos = []
    for linha in texto.splitlines():
        limpa = re.sub(r"\*\*|^#+\s*", "", linha.strip()).strip()
        if len(limpa) >= min_len:
            trechos.append(limpa)
    return trechos


def trechos_do_regulamento(min_len: int = 20) -> list[str]:
    return _trechos(REGULAMENTO_PATH.read_text(encoding="utf-8"), min_len)


def trechos_de_outros_capitulos(numero: int, min_len: int = 20) -> list[str]:
    proprios = set(_trechos(indice_regulamento()[numero - 1].texto, min_len))
    trechos = []
    for capitulo in indice_regulamento():
        if capitulo.numero != numero:
            trechos.extend(t for t in _trechos(capitulo.texto, min_len) if t not in proprios)
    return trechos
