from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi.testclient import TestClient

from comfyui2api.comfy_workflow import detect_capabilities, prepare_prompt
from comfyui2api.util import guess_media_type

IMG23D_PROMPT = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
    "2": {"class_type": "SaveGLB", "inputs": {"mesh": ["1", 0], "filename_prefix": "mesh/api"}},
}


class Img23dWorkflowTests(unittest.TestCase):
    def test_detects_img23d(self) -> None:
        caps = detect_capabilities(IMG23D_PROMPT)
        self.assertEqual(caps.kind, "img23d")
        self.assertTrue(caps.has_save_3d)
        self.assertTrue(caps.has_load_image)
        self.assertFalse(caps.has_save_video)

    def test_save3d_class_is_detected_too(self) -> None:
        self.assertTrue(detect_capabilities({"1": {"class_type": "Save3DModel", "inputs": {}}}).has_save_3d)

    def test_text_prompt_is_ignored_for_3d_workflow(self) -> None:
        graph, _extra, applied, _trace = prepare_prompt(
            workflow_obj=json.loads(json.dumps(IMG23D_PROMPT)),
            positive_prompt="a cat",
            negative_prompt="blurry",
            positive_prompt_node=None,
            negative_prompt_node=None,
            image="input/cat.png",
            image_node=None,
            overrides=[],
        )
        self.assertEqual(graph["1"]["inputs"]["image"], "input/cat.png")
        self.assertFalse([o for o in applied if o[1] == "text"])

    def test_text_prompt_still_errors_for_non_3d_workflow_without_target(self) -> None:
        with self.assertRaises(Exception):
            prepare_prompt(
                workflow_obj={"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                              "2": {"class_type": "SaveImage", "inputs": {}}},
                positive_prompt="a cat",
                negative_prompt=None,
                positive_prompt_node=None,
                negative_prompt_node=None,
                image=None,
                image_node=None,
                overrides=[],
            )

    def test_glb_media_types(self) -> None:
        self.assertEqual(guess_media_type("mesh.glb"), "model/gltf-binary")
        self.assertEqual(guess_media_type("mesh.GLTF"), "model/gltf+json")


class Img23dEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(cls.tempdir.name)
        workflows_dir = root / "workflows"
        workflows_dir.mkdir()
        (workflows_dir / "img23d.json").write_text(json.dumps(IMG23D_PROMPT), encoding="utf-8")
        env = {
            "API_TOKEN": "secret-token",
            "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
            "COMFYUI_INPUT_DIR": str(root / "input"),
            "COMFYUI_STARTUP_CHECK": "0",
            "DATA_DIR": str(root / "data"),
            "DATABASE_PATH": str(root / "data" / "comfyui2api.db"),
            "DEFAULT_IMG2VIDEO_WORKFLOW": "img23d.json",
            "ENABLE_WORKFLOW_WATCH": "0",
            "IMAGE_UPLOAD_MODE": "local",
            "RUNS_DIR": str(root / "runs"),
            "WORKER_CONCURRENCY": "1",
            "WORKFLOWS_DIR": str(workflows_dir),
        }
        cls.env_patcher = patch.dict(os.environ, env, clear=False)
        cls.env_patcher.start()
        import comfyui2api.app as app_module

        cls.app = importlib.reload(app_module).app

    @classmethod
    def tearDownClass(cls) -> None:
        cls.env_patcher.stop()
        cls.tempdir.cleanup()

    def setUp(self) -> None:
        self.client_cm = TestClient(self.app)
        self.client = self.client_cm.__enter__()

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)

    def test_workflow_listed_as_img23d(self) -> None:
        resp = self.client.get("/v1/workflows", headers={"Authorization": "Bearer secret-token"})
        kinds = {item["name"]: item["kind"] for item in resp.json()["items"]}
        self.assertEqual(kinds["img23d.json"], "img23d")

    def test_videos_endpoint_accepts_3d_workflow_with_input_image(self) -> None:
        create_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-3d", requested_model="img23d.json", created_at=1)
        )
        with patch.object(self.app.state.jobs, "create_job", create_job):
            resp = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer secret-token"},
                data={"prompt": "ignored", "model": "img23d.json"},
                files={"input_reference": ("in.png", b"fake-image", "image/png")},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(create_job.await_args.kwargs["kind"], "img2video")

    def test_videos_endpoint_rejects_3d_workflow_without_image(self) -> None:
        create_job = AsyncMock()
        with patch.object(self.app.state.jobs, "create_job", create_job):
            resp = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer secret-token"},
                data={"prompt": "ignored", "model": "img23d.json"},
                files={},
            )
        self.assertEqual(resp.status_code, 400)
        create_job.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
