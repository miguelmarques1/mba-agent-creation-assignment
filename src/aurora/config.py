"""Configuração central do projeto, lida do ambiente e do `.env`."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "gemini-3.6-flash"


@dataclass(frozen=True)
class Settings:
    google_api_key: str | None
    model_principal: str
    model_especialista: str
    db_path: Path
    host: str
    port: int

    @property
    def google_api_key_configured(self) -> bool:
        return bool(self.google_api_key and self.google_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(override=False)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

    raw_port = os.environ.get("AURORA_PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError:
        raise ValueError(f"AURORA_PORT inválida: {raw_port!r} (esperado um inteiro)") from None

    api_key = (os.environ.get("GOOGLE_API_KEY") or "").strip() or None
    return Settings(
        google_api_key=api_key,
        model_principal=os.environ.get("AURORA_MODEL_PRINCIPAL", DEFAULT_MODEL),
        model_especialista=os.environ.get("AURORA_MODEL_ESPECIALISTA", DEFAULT_MODEL),
        db_path=Path(os.environ.get("AURORA_DB_PATH", "var/aurora.db")),
        host=os.environ.get("AURORA_HOST", "127.0.0.1"),
        port=port,
    )
