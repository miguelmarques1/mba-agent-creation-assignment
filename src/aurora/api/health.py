"""Healthcheck; nunca expõe o valor da chave."""

from fastapi import APIRouter

from aurora.config import get_settings

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "google_api_key_configured": get_settings().google_api_key_configured,
    }
