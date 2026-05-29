from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from comfyui2api.jobs import Job, JobOutput


class AdminRoutesTests(unittest.TestCase):
    def _app_with_env(self, root: Path, *, ui_built: bool = True):
        workflows = root / "workflows"
        runs = root / "runs"
        data = root / "data"
        ui_dist = root / "ui"
        workflows.mkdir(parents=True, exist_ok=True)
        runs.mkdir(parents=True, exist_ok=True)
        data.mkdir(parents=True, exist_ok=True)
        if ui_built:
            (ui_dist / "assets").mkdir(parents=True, exist_ok=True)
            (ui_dist / "index.html").write_text("<html><body>dashboard</body></html>", encoding="utf-8")

        env = {
            "ADMIN_TOKEN": "admin-token",
            "API_TOKEN": "api-token",
            "COMFYUI_STARTUP_CHECK": "0",
            "COMFYUI2API_UI_DIST_DIR": str(ui_dist),
            "DATA_DIR": str(data),
            "DATABASE_PATH": str(data / "tasks.db"),
            "ENABLE_WORKFLOW_WATCH": "0",
            "RUNS_DIR": str(runs),
            "WORKFLOWS_DIR": str(workflows),
        }
        patcher = patch.dict(os.environ, env, clear=False)
        patcher.start()
        import comfyui2api.app as app_module

        app = importlib.reload(app_module).create_app()
        self.addCleanup(patcher.stop)
        return app

    def test_admin_tasks_require_token_and_return_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app_with_env(Path(tmp))
            with TestClient(app) as client:
                job = Job(
                    job_id="task_admin",
                    created_at=1780000000,
                    created_at_utc="2026-05-29T00:19:20Z",
                    status="completed",
                    kind="txt2img",
                    workflow="wf.json",
                    platform="OpenAI",
                    prompt_id="prompt_admin",
                    progress_percent=100,
                    outputs=[
                        JobOutput(
                            filename="out.png",
                            url="/runs/task_admin/out.png",
                            media_type="image/png",
                            node_id="1",
                            output_key="images",
                        )
                    ],
                )
                asyncio.run(app.state.job_store.upsert_job(job))
                asyncio.run(app.state.job_store.replace_outputs(job.job_id, job.outputs))

                unauthorized = client.get("/v1/admin/tasks")
                self.assertEqual(unauthorized.status_code, 401)

                response = client.get(
                    "/v1/admin/tasks?status=completed&kind=txt2img&platform=OpenAI&q=prompt_admin",
                    headers={"Authorization": "Bearer admin-token"},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["total"], 1)
                self.assertEqual(payload["items"][0]["job_id"], "task_admin")

                detail = client.get("/v1/admin/tasks/task_admin", headers={"Authorization": "Bearer admin-token"})
                self.assertEqual(detail.status_code, 200)
                output_url = detail.json()["outputs"][0]["url"]
                self.assertIn("/runs/task_admin/out.png", output_url)
                self.assertIn("sig=", output_url)

    def test_admin_ws_sends_snapshot_and_rejects_missing_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app_with_env(Path(tmp))
            with TestClient(app) as client:
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with client.websocket_connect("/v1/admin/tasks/ws"):
                        pass
                self.assertEqual(ctx.exception.code, 1008)

                with client.websocket_connect("/v1/admin/tasks/ws?token=admin-token") as ws:
                    payload = ws.receive_json()
                self.assertEqual(payload["type"], "snapshot")

    def test_ui_mount_built_and_missing_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app_with_env(Path(tmp), ui_built=True)
            with TestClient(app) as client:
                response = client.get("/ui")
                self.assertEqual(response.status_code, 200)
                self.assertIn("dashboard", response.text)

        with tempfile.TemporaryDirectory() as tmp:
            app = self._app_with_env(Path(tmp), ui_built=False)
            with TestClient(app) as client:
                response = client.get("/ui")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["error"], "Web UI has not been built.")


if __name__ == "__main__":
    unittest.main()
