from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("API_LISTEN", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run("comfyui2api.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

