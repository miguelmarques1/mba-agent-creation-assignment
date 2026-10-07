"""Índice e tools de leitura do regulamento interno (`dados/regulamento.md`, somente leitura)."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from google.adk.tools import ToolContext

REGULAMENTO_PATH = Path(__file__).resolve().parents[3] / "dados" / "regulamento.md"
TOTAL_CAPITULOS = 14
MAX_CAPITULOS_POR_PERGUNTA = 2

_CABECALHO = re.compile(r"^## Capítulo (?P<romano>[IVXLC]+): (?P<titulo>.+)$", re.MULTILINE)
_VALORES_ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_NOME_TOOL_LEITURA = "ler_capitulo"

_ERRO_FAIXA = f"Capítulo inexistente: informe um número de 1 a {TOTAL_CAPITULOS}."
_ERRO_LIMITE = (
    f"Limite de {MAX_CAPITULOS_POR_PERGUNTA} capítulos por pergunta atingido. "
    "Responda com o que já foi lido ou diga que não encontrou a informação."
)


class RegulamentoInvalidoError(RuntimeError):
    """O arquivo do regulamento não tem a estrutura esperada (não é erro de negócio)."""


@dataclass(frozen=True)
class Capitulo:
    numero: int
    romano: str
    titulo: str
    texto: str


def romano_para_int(romano: str) -> int:
    total = 0
    for atual, proximo in zip(romano, romano[1:] + " "):
        valor = _VALORES_ROMANOS[atual]
        total += -valor if _VALORES_ROMANOS.get(proximo, 0) > valor else valor
    return total


def carregar_capitulos(path: Path = REGULAMENTO_PATH) -> tuple[Capitulo, ...]:
    conteudo = Path(path).read_text(encoding="utf-8")
    cabecalhos = list(_CABECALHO.finditer(conteudo))
    if len(cabecalhos) != TOTAL_CAPITULOS:
        raise RegulamentoInvalidoError(
            f"Regulamento inválido: esperados {TOTAL_CAPITULOS} capítulos, "
            f"encontrados {len(cabecalhos)}."
        )

    capitulos = []
    for posicao, cabecalho in enumerate(cabecalhos, start=1):
        fim = cabecalhos[posicao].start() if posicao < len(cabecalhos) else len(conteudo)
        romano = cabecalho["romano"]
        titulo = cabecalho["titulo"].strip()
        texto = conteudo[cabecalho.end() : fim].strip()
        if romano_para_int(romano) != posicao:
            raise RegulamentoInvalidoError(
                f"Regulamento inválido: capítulo {romano} na posição {posicao}."
            )
        if not titulo:
            raise RegulamentoInvalidoError(f"Regulamento inválido: capítulo {romano} sem título.")
        if "## Capítulo" in texto:
            raise RegulamentoInvalidoError(
                f"Regulamento inválido: capítulo {romano} contém outro cabeçalho."
            )
        capitulos.append(Capitulo(posicao, romano, titulo, texto))
    return tuple(capitulos)


@lru_cache(maxsize=1)
def indice_regulamento() -> tuple[Capitulo, ...]:
    return carregar_capitulos(REGULAMENTO_PATH)


def _numero_valido(valor) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        return None
    numero = int(valor)
    return numero if numero == valor and 1 <= numero <= TOTAL_CAPITULOS else None


def _capitulos_ja_lidos(tool_context: ToolContext) -> list[int]:
    """Números válidos, distintos e em ordem, lidos nesta invocação antes da chamada atual."""
    lidos: list[int] = []
    for evento in tool_context.session.events:
        if evento.invocation_id != tool_context.invocation_id or not evento.content:
            continue
        for parte in evento.content.parts or []:
            chamada = parte.function_call
            if chamada is None or chamada.name != _NOME_TOOL_LEITURA:
                continue
            if chamada.id == tool_context.function_call_id:
                return lidos
            numero = _numero_valido((chamada.args or {}).get("numero"))
            if numero is not None and numero not in lidos:
                lidos.append(numero)
    return lidos


async def listar_capitulos() -> dict:
    """Lista os capítulos do regulamento interno (número e título), sem o texto.

    Chame primeiro, uma vez, para escolher o capítulo que trata do assunto.

    Returns:
        dict com `status` e `capitulos`, uma lista de {numero, titulo}.
    """
    return {
        "status": "success",
        "capitulos": [{"numero": c.numero, "titulo": c.titulo} for c in indice_regulamento()],
    }


async def ler_capitulo(numero: int, tool_context: ToolContext) -> dict:
    """Lê o texto de UM capítulo do regulamento.

    Use o número obtido em `listar_capitulos`. No máximo 2 capítulos diferentes por pergunta.

    Args:
        numero: número do capítulo, de 1 a 14.

    Returns:
        dict com `status`, `numero`, `titulo` e `texto` do capítulo, ou `status="error"`
        e `message`.
    """
    if _numero_valido(numero) is None:
        return {"status": "error", "message": _ERRO_FAIXA}
    numero = int(numero)

    lidos = _capitulos_ja_lidos(tool_context)
    if numero not in lidos and len(lidos) >= MAX_CAPITULOS_POR_PERGUNTA:
        return {"status": "error", "message": _ERRO_LIMITE}

    capitulo = indice_regulamento()[numero - 1]
    return {
        "status": "success",
        "numero": capitulo.numero,
        "titulo": capitulo.titulo,
        "texto": capitulo.texto,
    }
