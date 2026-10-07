"""Conferência mecânica do README de entrega (F10): estrutura, links, símbolos, comandos."""

import re
import tomllib
from pathlib import Path

from google.adk.tools.agent_tool import AgentTool

from aurora.agents.assistente import ASSISTENTE_AGENT_NAME, criar_app
from aurora.agents.regulamento import REGULAMENTO_AGENT_NAME
from aurora.agents.reservas import RESERVAS_AGENT_NAME
from aurora.agents.visitantes import VISITANTES_AGENT_NAME

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
TESTS = ROOT / "tests"


def _sem_codigo(texto: str) -> str:
    return re.sub(r"```.*?```", "", texto, flags=re.DOTALL)


def _secoes(texto: str, nivel: str) -> dict[str, str]:
    """Divide `texto` pelos cabeçalhos do nível dado (fora de blocos de código)."""
    limpo = _sem_codigo(texto)
    partes = re.split(rf"^{nivel} (.+)$", limpo, flags=re.MULTILINE)
    return {partes[i].strip(): partes[i + 1] for i in range(1, len(partes), 2)}


def _secao_bruta(titulo: str) -> str:
    """Corpo de uma seção `##` preservando os blocos de código."""
    m = re.search(rf"^## {re.escape(titulo)}$(.*?)(?=^## |\Z)", README, re.MULTILINE | re.DOTALL)
    assert m, titulo
    return m.group(1)


def _linhas_de_tabela(corpo: str) -> list[list[str]]:
    linhas = []
    for linha in corpo.splitlines():
        if not linha.startswith("|"):
            continue
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in celulas):
            continue
        linhas.append(celulas)
    return linhas[1:]  # descarta o cabeçalho


def _garantias() -> dict[str, str]:
    subs = _secoes(_secao_bruta("Garantias"), "###")
    return {k: v for k, v in subs.items() if k.startswith("Garantia ")}


def _arquivo_da_celula(celula: str) -> Path:
    m = re.search(r"\]\(([^)]+)\)", celula)
    assert m, f"célula sem link: {celula}"
    return ROOT / m.group(1)


def _identificadores(celula: str) -> list[str]:
    return [t.removesuffix("()") for t in re.findall(r"`([^`]+)`", celula)]


def _bloco_bash_como_rodar() -> list[str]:
    m = re.search(r"```bash\n(.*?)```", _secao_bruta("Como rodar"), re.DOTALL)
    assert m
    return [linha.strip() for linha in m.group(1).splitlines() if linha.strip()]


def test_readme_tem_exatamente_tres_secoes():
    limpo = _sem_codigo(README)
    assert re.findall(r"^## (.+)$", limpo, re.MULTILINE) == [
        "Arquitetura",
        "Garantias",
        "Como rodar",
    ]
    assert len(re.findall(r"^# ", limpo, re.MULTILINE)) == 1


def test_readme_nao_e_mais_o_enunciado():
    assert "## Fluxo do avaliador" not in README
    assert "## Critérios de aceite" not in README


def test_links_relativos_existem():
    links = re.findall(r"\]\(([^)\s]+)\)", README)
    assert links
    for link in links:
        if link.startswith(("http://", "https://", "#")):
            continue
        assert (ROOT / link.split("#")[0]).exists(), link


def test_garantias_tem_as_cinco_subsecoes():
    garantias = _garantias()
    assert [k.split(" — ")[0] for k in garantias] == [f"Garantia {n}" for n in range(1, 6)]
    for titulo, corpo in garantias.items():
        assert "Por que não depende do modelo" in corpo, titulo
        cabecalho = next(ln for ln in corpo.splitlines() if ln.startswith("|"))
        assert len(cabecalho.strip().strip("|").split("|")) == 4, titulo
        assert _linhas_de_tabela(corpo), titulo


def test_trechos_citados_existem_nos_arquivos():
    tabelas = [(t, _linhas_de_tabela(c)) for t, c in _garantias().items()]
    regras = _secoes(_secao_bruta("Garantias"), "###")["Regras de negócio"]
    linhas_regras = [(r[1], r[2]) for r in _linhas_de_tabela(regras)]
    pares = [(t, ln[0], ln[1]) for t, linhas in tabelas for ln in linhas]
    pares += [("Regras de negócio", a, b) for a, b in linhas_regras]
    assert len(pares) >= 20
    for titulo, arquivo, trecho in pares:
        caminho = _arquivo_da_celula(arquivo)
        assert caminho.is_file(), f"{titulo}: {arquivo}"
        fonte = caminho.read_text(encoding="utf-8")
        ids = _identificadores(trecho)
        assert ids, f"{titulo}: trecho sem identificador em crase: {trecho}"
        for ident in ids:
            if not re.fullmatch(r"\w+", ident):
                continue  # expressões (ex.: `taxa > 0`) não são símbolos
            assert re.search(rf"\b{ident}\b", fonte), (
                f"{titulo}: {ident} não está em {caminho.name}"
            )


def test_testes_citados_existem():
    fontes = "\n".join(p.read_text(encoding="utf-8") for p in TESTS.glob("*.py"))
    citados = 0
    for titulo, corpo in _garantias().items():
        for linha in _linhas_de_tabela(corpo):
            for ident in _identificadores(linha[3]):
                citados += 1
                if ident.endswith(".py"):
                    assert (TESTS / ident).is_file(), f"{titulo}: {ident}"
                else:
                    assert re.search(rf"def {ident}\b", fontes), f"{titulo}: {ident}"
    assert citados >= 30


def _linhas_agentes() -> dict[str, list[str]]:
    arq = _secoes(README, "##")["Arquitetura"]
    linhas = _linhas_de_tabela(arq)
    return {_identificadores(ln[0])[0]: ln for ln in linhas if _identificadores(ln[0])}


def test_arquitetura_descreve_os_quatro_agentes():
    agentes = _linhas_agentes()
    esperados = {
        ASSISTENTE_AGENT_NAME,
        RESERVAS_AGENT_NAME,
        VISITANTES_AGENT_NAME,
        REGULAMENTO_AGENT_NAME,
    }
    assert set(agentes) == esperados
    for nome, linha in agentes.items():
        assert len(linha) == 4 and all(linha), nome


def test_acionamento_bate_com_a_topologia():
    agentes = _linhas_agentes()
    root = criar_app("m1", "m2").root_agent
    subs = {a.name for a in root.sub_agents}
    tools = {t.agent.name for t in root.tools if isinstance(t, AgentTool)}
    for nome in (RESERVAS_AGENT_NAME, VISITANTES_AGENT_NAME):
        assert "Transferência" in agentes[nome][2], nome
        assert nome in subs
    assert "AgentTool" in agentes[REGULAMENTO_AGENT_NAME][2]
    assert REGULAMENTO_AGENT_NAME in tools


def test_arquitetura_tem_diagrama_mermaid():
    arq = _secao_bruta("Arquitetura")
    m = re.search(r"```mermaid\n(.*?)```", arq, re.DOTALL)
    assert m
    for termo in ("Runner", "assistente", "reservas", "visitantes", "regulamento", "SQLite"):
        assert termo in m.group(1), termo


def test_como_rodar_lista_comandos_em_ordem():
    comandos = _bloco_bash_como_rodar()
    esperados = ["cp .env.example .env", "uv sync", "uv run aurora-restore", "uv run aurora-api"]
    posicoes = [comandos.index(c) for c in esperados]
    assert posicoes == sorted(posicoes)


def test_comandos_existem_no_pyproject():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    assert scripts["aurora-api"] == "aurora.main:run"
    assert scripts["aurora-restore"] == "aurora.storage.carga:main"


def test_como_rodar_lista_variaveis_do_env_example():
    chaves = [
        ln.split("=")[0].strip()
        for ln in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln
    ]
    assert len(chaves) == 8
    como_rodar = _secao_bruta("Como rodar")
    for chave in chaves:
        assert chave in como_rodar, chave


def test_readme_cita_versao_do_adk_fixada():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    (pin,) = [d.replace(" ", "") for d in deps if d.replace(" ", "").startswith("google-adk")]
    assert pin in README


def test_readme_sem_chave():
    assert "AIza" not in README
    assert not re.search(r"GOOGLE_API_KEY\s*=\s*\S", README)
