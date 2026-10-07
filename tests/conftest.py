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
def settings_env(monkeypatch):
    """Isola as variáveis de ambiente, impede a leitura do `.env` real e limpa o cache."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("aurora.config.load_dotenv", lambda *a, **k: False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.fixture
def client(settings_env):
    return TestClient(create_app())
