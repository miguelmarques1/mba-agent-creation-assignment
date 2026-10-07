import hashlib

import pytest

from aurora.tools.regulamento import (
    REGULAMENTO_PATH,
    RegulamentoInvalidoError,
    carregar_capitulos,
    indice_regulamento,
    romano_para_int,
)


def test_indice_tem_14_capitulos():
    assert len(indice_regulamento()) == 14


def test_numeracao_sequencial():
    idx = indice_regulamento()
    assert [c.numero for c in idx] == list(range(1, 15))
    assert idx[3].romano == "IV"
    assert idx[13].romano == "XIV"


def test_titulos_dos_cabecalhos():
    idx = indice_regulamento()
    assert idx[3].titulo == "Piscina"
    assert idx[0].titulo == "Disposições gerais"
    assert all(c.titulo for c in idx)


def test_capitulo_4_contem_horario_de_domingo():
    texto = indice_regulamento()[3].texto
    assert "Aos domingos e feriados, a piscina funciona das 9h às 20h." in texto
    assert "**Art. 22.**" in texto


def test_capitulos_nao_se_sobrepoem():
    idx = indice_regulamento()
    assert "## Capítulo" not in idx[3].texto
    assert "**Art. 20.**" not in idx[3].texto
    assert "**Art. 29.**" not in idx[3].texto
    assert "Art. 21" not in idx[2].texto


def test_preambulo_descartado():
    titulo = "# Regulamento Interno do Residencial Aurora"
    assert all(titulo not in c.texto for c in indice_regulamento())


def test_crlf_normalizado():
    assert all("\r" not in c.texto and "\r" not in c.titulo for c in indice_regulamento())


def test_romano_para_int():
    assert [romano_para_int(r) for r in ("I", "IV", "IX", "XIV")] == [1, 4, 9, 14]


def _arquivo(tmp_path, romanos):
    corpo = "# Regulamento\n\n" + "".join(
        f"## Capítulo {r}: T{r}\n\ntexto {r}\n\n" for r in romanos
    )
    caminho = tmp_path / "reg.md"
    caminho.write_text(corpo, encoding="utf-8")
    return caminho


def test_rejeita_quantidade_errada(tmp_path):
    with pytest.raises(RegulamentoInvalidoError, match="encontrados 2"):
        carregar_capitulos(_arquivo(tmp_path, ["I", "II"]))


def test_rejeita_numeracao_fora_de_ordem(tmp_path):
    romanos = [
        "I",
        "III",
        "II",
        "IV",
        "V",
        "VI",
        "VII",
        "VIII",
        "IX",
        "X",
        "XI",
        "XII",
        "XIII",
        "XIV",
    ]
    with pytest.raises(RegulamentoInvalidoError):
        carregar_capitulos(_arquivo(tmp_path, romanos))


def test_carga_nao_altera_dados():
    antes = hashlib.sha256(REGULAMENTO_PATH.read_bytes()).hexdigest()
    carregar_capitulos(REGULAMENTO_PATH)
    indice_regulamento.cache_clear()
    indice_regulamento()
    assert hashlib.sha256(REGULAMENTO_PATH.read_bytes()).hexdigest() == antes
