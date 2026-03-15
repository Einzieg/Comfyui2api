from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class AppSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(cls.tempdir.name)
        workflows_dir = root / "workflows"
        runs_dir = root / "runs"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        runs_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / ".comfyui2api").mkdir(parents=True, exist_ok=True)

        workflow_name = "test_txt2img.json"
        (workflows_dir / workflow_name).write_text(
            json.dumps(
                {
                    "prompt": {
                        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
                        "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "sample"}},
                        "10": {
                            "class_type": "EmptyLatentImage",
                            "inputs": {"width": 512, "height": 512},
                            "_meta": {"title": "Latent Size"},
                        },
                        "11": {
                            "class_type": "KSampler",
                            "inputs": {"seed": 1, "steps": 20, "cfg": 3.5},
                            "_meta": {"title": "Sampler"},
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workflows_dir / ".comfyui2api" / "test_txt2img.params.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "txt2img",
                    "parameters": {
                        "size": {
                            "type": "size",
                            "maps": [
                                {"target": "10.width", "part": "width"},
                                {"target": "10.height", "part": "height"},
                            ],
                        },
                        "steps": {
                            "type": "int",
                            "default": 20,
                            "maps": [{"target": "11.steps"}],
                        },
                        "cfg": {
                            "type": "float",
                            "default": 3.5,
                            "maps": [{"target": "11.cfg"}],
                        },
                        "seed": {
                            "type": "int",
                            "maps": [{"target": "11.seed"}],
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        env = {
            "API_TOKEN": "secret-token",
            "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
            "DEFAULT_TXT2IMG_WORKFLOW": workflow_name,
            "DEFAULT_IMG2IMG_WORKFLOW": workflow_name,
            "DEFAULT_IMG2VIDEO_WORKFLOW": workflow_name,
            "ENABLE_WORKFLOW_WATCH": "0",
            "MAX_BODY_BYTES": "256",
            "RUNS_DIR": str(runs_dir),
            "WORKER_CONCURRENCY": "1",
            "WORKFLOWS_DIR": str(workflows_dir),
        }
        cls.env_patcher = patch.dict(os.environ, env, clear=False)
        cls.env_patcher.start()

        import comfyui2api.app as app_module

        cls.app_module = importlib.reload(app_module)
        cls.app = cls.app_module.app
        cls.workflow_name = workflow_name

    @classmethod
    def tearDownClass(cls) -> None:
        cls.env_patcher.stop()
        cls.tempdir.cleanup()

    def setUp(self) -> None:
        self.client_cm = TestClient(self.app)
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)

    def test_models_require_auth_and_list_loaded_workflow(self) -> None:
        unauthorized = self.client.get("/v1/models")
        self.assertEqual(unauthorized.status_code, 401)

        authorized = self.client.get("/v1/models", headers={"Authorization": "Bearer secret-token"})
        self.assertEqual(authorized.status_code, 200)
        payload = authorized.json()
        self.assertEqual(payload["object"], "list")
        self.assertEqual(payload["data"][0]["id"], self.workflow_name)
        self.assertEqual(payload["data"][0]["metadata"]["kind"], "txt2img")

    def test_request_body_limit_returns_413(self) -> None:
        response = self.client.post("/v1/images/generations", json={"prompt": "x" * 2048})
        self.assertEqual(response.status_code, 413)
        payload = response.json()
        self.assertIn("Request body too large", payload["error"]["message"])

    def test_workflow_parameters_endpoint_exposes_sidecar_mapping(self) -> None:
        response = self.client.get(
            f"/v1/workflows/{self.workflow_name}/parameters",
            headers={"Authorization": "Bearer secret-token"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["workflow"]["name"], self.workflow_name)
        self.assertIsNone(payload["parameter_error"])
        names = [item["name"] for item in payload["parameter_mapping"]["parameters"]]
        self.assertEqual(names[:4], ["size", "steps", "cfg", "seed"])
        detected = payload["detected_candidates"]
        self.assertEqual(detected["size"][0]["maps"][0]["ref"], "10.width")
        self.assertEqual(detected["size"][0]["maps"][1]["ref"], "10.height")
        self.assertEqual(detected["steps"][0]["maps"][0]["ref"], "11.steps")
        self.assertEqual(detected["seed"][0]["maps"][0]["ref"], "11.seed")
        template = payload["suggested_template"]
        self.assertEqual(template["kind"], "txt2img")
        self.assertEqual(template["parameters"]["size"]["maps"][0]["target"]["ref"], "10.width")
        self.assertEqual(template["parameters"]["size"]["default"], "512x512")
        self.assertEqual(template["parameters"]["steps"]["default"], 20)

    def test_workflow_parameters_template_endpoint_returns_copyable_template(self) -> None:
        response = self.client.get(
            f"/v1/workflows/{self.workflow_name}/parameters/template",
            headers={"Authorization": "Bearer secret-token"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["parameter_error"])
        template = payload["template"]
        self.assertEqual(template["version"], 1)
        self.assertEqual(template["kind"], "txt2img")
        self.assertEqual(template["parameters"]["cfg"]["maps"][0]["target"]["ref"], "11.cfg")
        self.assertEqual(template["parameters"]["seed"]["maps"][0]["target"]["ref"], "11.seed")

    def test_images_generations_passes_standard_params_to_job_manager(self) -> None:
        mock_create_job = AsyncMock(return_value=SimpleNamespace(job_id="job-img"))
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/images/generations",
                headers={
                    "Authorization": "Bearer secret-token",
                    "x-comfyui-async": "1",
                },
                json={"prompt": "cat", "size": "1024x768", "seed": 7, "steps": 12},
            )
        self.assertEqual(response.status_code, 200)
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["standard_params"], {"size": "1024x768", "seed": 7, "steps": 12})

    def test_videos_create_passes_duration_and_fps_standard_params(self) -> None:
        mock_create_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-video", requested_model=self.workflow_name, created_at=123)
        )
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer secret-token"},
                data={
                    "prompt": "cat animation",
                    "model": self.workflow_name,
                    "seconds": "5",
                    "size": "1280x720",
                    "fps": "24",
                    "frames": "120",
                },
                files={},
            )
        self.assertEqual(response.status_code, 201)
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["standard_params"], {"duration": "5", "size": "1280x720", "fps": "24", "frames": "120"})

    def test_websocket_rejects_missing_auth(self) -> None:
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/v1/jobs/missing-job/ws"):
                pass
        self.assertEqual(ctx.exception.code, 1008)

    def test_websocket_accepts_query_token(self) -> None:
        with self.client.websocket_connect("/v1/jobs/missing-job/ws?api_key=secret-token") as ws:
            payload = ws.receive_json()
        self.assertEqual(payload["type"], "error")
        self.assertIn("Job not found", payload["data"]["message"])

    def test_images_generations_failure_includes_job_id(self) -> None:
        from comfyui2api.jobs import Job

        done = asyncio.Event()
        done.set()
        failed_job = Job(
            job_id="job-failed",
            created_at_utc="2026-03-16T00:00:00Z",
            created_at=123,
            status="failed",
            kind="txt2img",
            workflow=self.workflow_name,
            error="RuntimeError: prompt resolution failed",
            done=done,
        )

        mock_create_job = AsyncMock(return_value=failed_job)
        mock_get_job = AsyncMock(side_effect=[failed_job, failed_job])
        with patch.object(self.app.state.jobs, "create_job", mock_create_job), patch.object(
            self.app.state.jobs, "get_job", mock_get_job
        ):
            response = self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret-token"},
                json={"prompt": "cat"},
            )

        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "server_error")
        self.assertEqual(payload["error"]["job_id"], "job-failed")
        self.assertIn("RuntimeError: prompt resolution failed", payload["error"]["message"])


class JobManagerErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_logs_traceback_and_records_exception_type(self) -> None:
        import comfyui2api.jobs as jobs_module

        manager = jobs_module.JobManager(
            cfg=SimpleNamespace(worker_concurrency=1),
            registry=SimpleNamespace(),
            comfy=SimpleNamespace(),
        )

        with patch.object(manager, "_run_job", AsyncMock(side_effect=RuntimeError("boom"))), patch.object(
            jobs_module.logger, "exception"
        ) as mock_exception:
            job = await manager.create_job(
                kind="txt2img",
                workflow="broken.json",
                requested_model="broken-model",
                prompt="cat",
            )
            worker = asyncio.create_task(manager._worker_loop(7))
            try:
                await asyncio.wait_for(job.done.wait(), timeout=1)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

        updated = await manager.get_job(job.job_id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.status, "failed")
        self.assertEqual(updated.error, "RuntimeError: boom")
        mock_exception.assert_called_once()
        self.assertEqual(
            mock_exception.call_args.args,
            (
                "job failed: job_id=%s worker=%s workflow=%s kind=%s requested_model=%s",
                job.job_id,
                7,
                "broken.json",
                "txt2img",
                "broken-model",
            ),
        )


if __name__ == "__main__":
    unittest.main()
