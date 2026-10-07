import pytest

from aurora.tools.sessao import ApartamentoAusenteError, apartamento_da_sessao
from tests.fakes import FakeToolContext


def test_apartamento_da_sessao_le_state():
    ctx = FakeToolContext("201")
    assert apartamento_da_sessao(ctx) == "201"
    assert ctx.state == {"apartamento": "201"}


@pytest.mark.parametrize("apartamento", [None, ""])
def test_apartamento_ausente_lanca_erro_de_infra(apartamento):
    ctx = FakeToolContext(apartamento)
    if apartamento == "":
        ctx.state["apartamento"] = ""
    with pytest.raises(ApartamentoAusenteError) as exc:
        apartamento_da_sessao(ctx)
    assert not isinstance(exc.value, ValueError)
