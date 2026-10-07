import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aurora.config import get_settings
from aurora.main import create_app

ENV_VARS = (
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "AURORA_MODEL_PRINCIPAL",
    "AURORA_MODEL_ESPECIALISTA",
    "AURORA_DB_PATH",
    "AURORA_HOST",
    "AURORA_PORT",
)


@pytest.fixture
def settings_env(monkeypatch, tmp_path):
    """Isola as variáveis de ambiente, impede a leitura do `.env` real e limpa o cache.

    `AURORA_DB_PATH` aponta para um banco em `tmp_path`, para que nenhum teste crie `var/aurora.db`.
    """
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AURORA_DB_PATH", str(tmp_path / "aurora.db"))
    monkeypatch.setattr("aurora.config.load_dotenv", lambda *a, **k: False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.fixture
def client(settings_env):
    return TestClient(create_app())


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(settings_env):
    """Banco em `tmp_path` restaurado a partir de `dados/`; devolve o caminho."""
    from aurora.storage import restaurar

    path = get_settings().db_path
    restaurar(path, ROOT / "dados")
    return path


@pytest.fixture
def dados_copia(tmp_path):
    """Cópia de `dados/` em `tmp_path`, para corromper sem tocar no original."""
    destino = tmp_path / "dados_copia"
    shutil.copytree(ROOT / "dados", destino)
    return destino
