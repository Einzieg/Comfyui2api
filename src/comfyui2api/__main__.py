from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def _try_load_dotenv(path: Path) -> bool:
    try:
        from dotenv import load_dotenv
    except Exception:
        return False
    if not path.exists():
        return False
    load_dotenv(dotenv_path=str(path), override=False)
    return True


def _load_env() -> None:
    env_file = (os.environ.get("ENV_FILE") or "").strip()
    if env_file:
        _try_load_dotenv(Path(env_file))
        return

    _try_load_dotenv(Path.cwd() / ".env")

    project_root = Path(__file__).resolve().parents[2]
    _try_load_dotenv(project_root / ".env")


def main() -> None:
    _load_env()
    host = os.environ.get("API_LISTEN", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run("comfyui2api.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
