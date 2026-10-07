import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV_VARS = {
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "AURORA_MODEL_PRINCIPAL",
    "AURORA_MODEL_ESPECIALISTA",
    "AURORA_DB_PATH",
    "AURORA_HOST",
    "AURORA_PORT",
}


def _pin() -> str:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    adk = [d for d in deps if d.replace(" ", "").startswith("google-adk")]
    assert len(adk) == 1
    return adk[0].replace(" ", "")


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("não é um repositório Git")
    return out.stdout.strip()


def _env_example() -> dict[str, str]:
    result = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def test_pyproject_pins_adk_exact():
    m = re.fullmatch(r"google-adk==(\d+)\.(\d+)\.(\d+)", _pin())
    assert m, "google-adk deve usar == com versão exata"
    major, minor, patch = map(int, m.groups())
    assert major == 2 and (minor, patch) >= (2, 0)


def test_uv_lock_matches_pin():
    version = _pin().split("==")[1]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    pkgs = [p for p in lock["package"] if p["name"] == "google-adk"]
    assert pkgs and pkgs[0]["version"] == version


def test_lock_and_python_version_tracked():
    tracked = _git("ls-files").splitlines()
    for f in ("uv.lock", "pyproject.toml", ".python-version"):
        assert f in tracked


def test_env_not_tracked():
    assert _git("ls-files", ".env") == ""
    assert _git("log", "--all", "--format=%H", "--", ".env") == ""


def test_env_example_lists_all_vars():
    assert ENV_VARS <= set(_env_example())


def test_env_example_has_no_secret_values():
    assert _env_example()["GOOGLE_API_KEY"] == ""
    assert not re.search(r"AIza", (ROOT / ".env.example").read_text(encoding="utf-8"))


def test_gitignore_entries():
    entries = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for e in (".env", ".venv/", "__pycache__/", "var/", "*.db"):
        assert e in entries
