from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_webui(app: FastAPI, ui_dist_dir: Path) -> None:
    index = Path(ui_dist_dir) / "index.html"
    assets = Path(ui_dist_dir) / "assets"

    if not index.exists():
        @app.get("/ui")
        async def ui_missing() -> dict[str, str]:
            return {
                "error": "Web UI has not been built.",
                "hint": "Run the frontend build script first.",
            }

        return

    if assets.exists():
        app.mount("/ui/assets", StaticFiles(directory=str(assets)), name="ui-assets")

    @app.get("/ui")
    @app.get("/ui/{path:path}")
    async def webui(path: str = "") -> Any:
        return FileResponse(str(index))
