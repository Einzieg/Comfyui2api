from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _write_valid_workflow(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "prompt": {
                    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
                    "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "sample"}},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class WorkflowRegistryLoadErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_all_keeps_invalid_workflow_errors_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workflows_dir = root / "workflows"
            workflows_dir.mkdir(parents=True, exist_ok=True)
            _write_valid_workflow(workflows_dir / "good.json")
            (workflows_dir / "broken.json").write_text("{", encoding="utf-8")

            from comfyui2api.workflow_registry import WorkflowRegistry

            registry = WorkflowRegistry(workflows_dir)
            with self.assertLogs("comfyui2api.workflow_registry", level="ERROR") as logs:
                await registry.load_all()

            items = await registry.list()
            load_errors = await registry.list_load_errors()

        self.assertEqual([item.name for item in items], ["good.json"])
        self.assertEqual([item.name for item in load_errors], ["broken.json"])
        self.assertIn("JSONDecodeError", load_errors[0].error)
        self.assertTrue(any("workflow load failed" in message for message in logs.output))


class WorkflowLoadErrorApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(cls.tempdir.name)
        workflows_dir = root / "workflows"
        runs_dir = root / "runs"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        runs_dir.mkdir(parents=True, exist_ok=True)

        workflow_name = "test_txt2img.json"
        _write_valid_workflow(workflows_dir / workflow_name)
        (workflows_dir / "broken.json").write_text("{", encoding="utf-8")

        env = {
            "API_TOKEN": "secret-token",
            "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
            "COMFYUI_STARTUP_CHECK": "0",
            "DEFAULT_TXT2IMG_WORKFLOW": workflow_name,
            "DEFAULT_IMG2IMG_WORKFLOW": workflow_name,
            "DEFAULT_IMG2VIDEO_WORKFLOW": workflow_name,
            "ENABLE_WORKFLOW_WATCH": "0",
            "RUNS_DIR": str(runs_dir),
            "WORKER_CONCURRENCY": "1",
            "WORKFLOWS_DIR": str(workflows_dir),
        }
        cls.env_patcher = patch.dict(os.environ, env, clear=False)
        cls.env_patcher.start()

        import comfyui2api.app as app_module

        cls.app_module = importlib.reload(app_module)
        cls.app = cls.app_module.app

    @classmethod
    def tearDownClass(cls) -> None:
        cls.env_patcher.stop()
        cls.tempdir.cleanup()

    def setUp(self) -> None:
        self.client_cm = TestClient(self.app)
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)

    def test_list_workflows_includes_load_errors(self) -> None:
        response = self.client.get(
            "/v1/workflows",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(response.status_code, 200)
        items = {item["name"]: item for item in response.json()["items"]}
        self.assertTrue(items["test_txt2img.json"]["available"])
        self.assertEqual(items["test_txt2img.json"]["kind"], "txt2img")
        self.assertIsNone(items["test_txt2img.json"]["load_error"])

        self.assertFalse(items["broken.json"]["available"])
        self.assertIsNone(items["broken.json"]["kind"])
        self.assertIn("JSONDecodeError", items["broken.json"]["load_error"])

    def test_requesting_failed_workflow_returns_load_error(self) -> None:
        response = self.client.get(
            "/v1/workflows/broken.json/parameters",
            headers={"Authorization": "Bearer secret-token"},
        )

        self.assertEqual(response.status_code, 400)
        message = response.json()["error"]["message"]
        self.assertIn("failed to load", message)
        self.assertIn("broken.json", message)
        self.assertIn("JSONDecodeError", message)


if __name__ == "__main__":
    unittest.main()
