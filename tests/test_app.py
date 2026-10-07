import logging

from fastapi.testclient import TestClient

from aurora import main
from aurora.config import get_settings

WARNING = "GOOGLE_API_KEY não configurada: as rotas de conversa vão falhar."


def test_healthz_ok(client, settings_env):
    settings_env.setenv("GOOGLE_API_KEY", "segredo-xyz")
    get_settings.cache_clear()
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "google_api_key_configured": True}
    assert "segredo-xyz" not in r.text


def test_healthz_without_key(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["google_api_key_configured"] is False


def test_startup_warns_without_key(settings_env, caplog):
    with caplog.at_level(logging.WARNING, logger="aurora"), TestClient(main.create_app()):
        pass
    assert any(
        r.name == "aurora" and r.levelno == logging.WARNING and r.getMessage() == WARNING
        for r in caplog.records
    )


def test_startup_silent_with_key(settings_env, caplog):
    settings_env.setenv("GOOGLE_API_KEY", "k")
    with caplog.at_level(logging.WARNING, logger="aurora"), TestClient(main.create_app()):
        pass
    assert not any(WARNING in r.getMessage() for r in caplog.records)


def test_run_uses_settings(settings_env, monkeypatch):
    settings_env.setenv("AURORA_HOST", "127.0.0.2")
    settings_env.setenv("AURORA_PORT", "8123")
    calls = []
    monkeypatch.setattr(main.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    main.run()
    assert calls == [(("aurora.main:app",), {"host": "127.0.0.2", "port": 8123})]
