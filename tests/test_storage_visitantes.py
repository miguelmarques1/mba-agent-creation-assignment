from aurora.storage import (
    apartamento_existe,
    autorizar_visitante,
    conectar,
    listar_visitantes,
)


def test_autorizar_e_listar_visitante(db):
    assert autorizar_visitante("101", "Joana Ribeiro", "2030-04-21").status == "autorizado"
    assert {"nome": "Joana Ribeiro", "data": "2030-04-21"} in [
        v.para_dict() for v in listar_visitantes("101")
    ]


def test_visitante_duplicado_nao_duplica(db):
    autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    assert autorizar_visitante("101", "joana  ribeiro ", "2030-04-21").status == "ja_autorizado"
    assert len(listar_visitantes("101")) == 1


def test_listar_visitantes_filtra_por_apartamento(db):
    autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    assert "Marina Duarte" not in [v.nome for v in listar_visitantes("101")]
    assert {"nome": "Marina Duarte", "data": "2030-03-16"} in [
        v.para_dict() for v in listar_visitantes("302")
    ]


def test_listar_visitantes_ordem_e_campos(db):
    autorizar_visitante("101", "Zélia", "2030-05-01")
    autorizar_visitante("101", "Ana", "2030-05-01")
    autorizar_visitante("101", "Bia", "2030-04-01")
    lista = listar_visitantes("101")
    assert [(v.data, v.nome) for v in lista] == [
        ("2030-04-01", "Bia"),
        ("2030-05-01", "Ana"),
        ("2030-05-01", "Zélia"),
    ]
    assert set(lista[0].para_dict()) == {"nome", "data"}


def test_autorizar_visitante_valida_nome_e_data(db):
    for nome in ("  ", "A", "x" * 81):
        assert autorizar_visitante("101", nome, "2030-04-21").status == "nome_invalido"
    assert autorizar_visitante("101", "Joana", "21/04/2030").status == "data_invalida"
    with conectar(db) as c:
        assert (
            c.execute("SELECT COUNT(*) FROM visitantes WHERE apartamento='101'").fetchone()[0] == 0
        )


def test_apartamento_existe(db):
    assert apartamento_existe("101") is True
    assert apartamento_existe("999") is False
