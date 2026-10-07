import os
from pathlib import Path

import pytest

from aurora.config import get_settings


def test_defaults_without_env(settings_env):
    settings_env.delenv("AURORA_DB_PATH")
    s = get_settings()
    assert s.model_principal == "gemini-3.6-flash"
    assert s.model_especialista == "gemini-3.6-flash"
    assert s.db_path == Path("var/aurora.db")
    assert s.host == "127.0.0.1"
    assert s.port == 8000


def test_env_overrides(settings_env):
    settings_env.setenv("AURORA_MODEL_PRINCIPAL", "m1")
    settings_env.setenv("AURORA_MODEL_ESPECIALISTA", "m2")
    settings_env.setenv("AURORA_DB_PATH", "x/y.db")
    settings_env.setenv("AURORA_HOST", "0.0.0.0")
    settings_env.setenv("AURORA_PORT", "9001")
    s = get_settings()
    assert (s.model_principal, s.model_especialista) == ("m1", "m2")
    assert s.db_path == Path("x/y.db")
    assert s.host == "0.0.0.0"
    assert s.port == 9001 and isinstance(s.port, int)


def test_vertexai_defaults_to_false(settings_env):
    get_settings()
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "FALSE"
    settings_env.delenv("GOOGLE_GENAI_USE_VERTEXAI")


def test_vertexai_explicit_value_kept(settings_env):
    settings_env.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    get_settings()
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "TRUE"


def test_blank_api_key_is_not_configured(settings_env):
    settings_env.setenv("GOOGLE_API_KEY", "   ")
    assert get_settings().google_api_key_configured is False


def test_invalid_port_fails_fast(settings_env):
    settings_env.setenv("AURORA_PORT", "abc")
    with pytest.raises(ValueError, match="AURORA_PORT"):
        get_settings()
