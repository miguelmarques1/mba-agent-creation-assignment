import asyncio
import inspect
import json

import pytest
from google.adk.tools import FunctionTool

from aurora.tools.regulamento import indice_regulamento, ler_capitulo, listar_capitulos
from tests.fakes import evento_chamadas, fake_tool_context
from tests.regulamento_utils import trechos_de_outros_capitulos, trechos_do_regulamento


def _ler(numero, ctx=None):
    ctx = ctx or fake_tool_context([], "i1", "f1")
    return asyncio.run(ler_capitulo(numero, ctx))


def _ctx_com_leituras(numeros, atual="atual", invocation_id="i1"):
    chamadas = [(f"c{n}", {"numero": n}) for n in numeros]
    return fake_tool_context([evento_chamadas(invocation_id, chamadas)], "i1", atual)


def test_listar_capitulos_so_numero_e_titulo():
    resultado = asyncio.run(listar_capitulos())
    assert resultado["status"] == "success"
    assert len(resultado["capitulos"]) == 14
    assert all(set(item) == {"numero", "titulo"} for item in resultado["capitulos"])
    serializado = json.dumps(resultado, ensure_ascii=False)
    assert len(serializado.encode()) < 1500
    assert "Art." not in serializado


def test_listar_capitulos_sem_texto_de_artigos():
    serializado = json.dumps(asyncio.run(listar_capitulos()), ensure_ascii=False)
    assert not any(trecho in serializado for trecho in trechos_do_regulamento())


def test_ler_capitulo_devolve_um_unico_capitulo():
    resultado = _ler(4)
    assert resultado["numero"] == 4 and resultado["titulo"] == "Piscina"
    assert resultado["texto"] == indice_regulamento()[3].texto
    assert not any(t in resultado["texto"] for t in trechos_de_outros_capitulos(4))


@pytest.mark.parametrize("n", range(1, 15))
def test_ler_capitulo_cada_numero(n):
    resultado = _ler(n)
    assert resultado["status"] == "success"
    assert resultado["texto"] == indice_regulamento()[n - 1].texto


@pytest.mark.parametrize("n", [0, 15, -1, 99])
def test_ler_capitulo_fora_da_faixa(n):
    resultado = _ler(n)
    assert resultado["status"] == "error"
    assert "1 a 14" in resultado["message"]
    assert "texto" not in resultado


def test_ler_capitulo_limite_dois_capitulos():
    resultado = _ler(8, _ctx_com_leituras([4, 6]))
    assert resultado["status"] == "error"
    assert "Limite de 2 capítulos" in resultado["message"]
    assert "texto" not in resultado


def test_ler_capitulo_releitura_permitida():
    assert _ler(4, _ctx_com_leituras([4, 6]))["status"] == "success"


def test_ler_capitulo_limite_considera_chamadas_paralelas():
    chamadas = [("a", {"numero": 4}), ("b", {"numero": 6}), ("c", {"numero": 8})]
    eventos = [evento_chamadas("i1", chamadas)]
    assert _ler(8, fake_tool_context(eventos, "i1", "c"))["status"] == "error"
    assert _ler(6, fake_tool_context(eventos, "i1", "b"))["status"] == "success"


def test_ler_capitulo_limite_por_invocacao():
    ctx = _ctx_com_leituras([4, 6], invocation_id="outra")
    assert _ler(8, ctx)["status"] == "success"


def test_ler_capitulo_numero_invalido_nao_conta():
    assert _ler(6, _ctx_com_leituras([99, 4]))["status"] == "success"


def test_tools_nao_escrevem_state():
    ctx = fake_tool_context([], "i1", "f1")
    asyncio.run(listar_capitulos())
    asyncio.run(ler_capitulo(4, ctx))
    assert ctx.state == {}


def test_tools_sem_parametro_apartamento():
    assert list(inspect.signature(listar_capitulos).parameters) == []
    assert list(inspect.signature(ler_capitulo).parameters) == ["numero", "tool_context"]
    assert inspect.signature(ler_capitulo).parameters["numero"].annotation is int


def test_declaracao_ler_capitulo():
    declaracao = FunctionTool(ler_capitulo)._get_declaration()
    esquema = declaracao.parameters_json_schema
    assert list(esquema["properties"]) == ["numero"]
    assert esquema["properties"]["numero"]["type"] == "integer"
