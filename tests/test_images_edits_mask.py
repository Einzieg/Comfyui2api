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

INPAINT_PROMPT = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
    "3": {"class_type": "LoadImage", "inputs": {"image": "mask.png"}},
    "4": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "denoise": 1.0}},
    "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out"}},
}


def _sidecar(*, mask_required: bool) -> dict:
    return {
        "version": 1,
        "kind": "img2img",
        "image_node": "2.image",
        "parameters": {
            "mask": {"type": "image", "required": mask_required, "maps": [{"target": "3.image"}]},
            "denoise": {"type": "float", "minimum": 0, "maximum": 1, "maps": [{"target": "4.denoise"}]},
        },
    }


class ImagesEditsMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(cls.tempdir.name)
        workflows_dir = root / "workflows"
        (workflows_dir / ".comfyui2api").mkdir(parents=True)
        # optional mask / required mask / no mask parameter at all
        for name, sidecar in (
            ("inpaint-optional", _sidecar(mask_required=False)),
            ("inpaint-required", _sidecar(mask_required=True)),
            ("inpaint-none", None),
        ):
            (workflows_dir / f"{name}.json").write_text(json.dumps(INPAINT_PROMPT), encoding="utf-8")
            if sidecar:
                (workflows_dir / ".comfyui2api" / f"{name}.params.json").write_text(
                    json.dumps(sidecar), encoding="utf-8"
                )
        env = {
            "API_TOKEN": "secret-token",
            "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
            "COMFYUI_INPUT_DIR": str(root / "input"),
            "COMFYUI_STARTUP_CHECK": "0",
            "DATA_DIR": str(root / "data"),
            "DATABASE_PATH": str(root / "data" / "comfyui2api.db"),
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

    def _edit(self, workflow: str, *, data: dict | None = None, mask: bytes | None = None, mock=None):
        files = {"image": ("in.png", b"fake-image", "image/png")}
        if mask is not None:
            files["mask"] = ("mask.png", mask, "image/png")
        form = {"prompt": "fix", "workflow": workflow, "response_format": "url", **(data or {})}
        create_job = mock or AsyncMock(return_value=SimpleNamespace(job_id="job-edit"))
        with patch.object(self.app.state.jobs, "create_job", create_job):
            resp = self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer secret-token", "x-comfyui-async": "1"},
                data=form,
                files=files,
            )
        return resp, create_job

    def test_mask_and_denoise_reach_standard_params(self) -> None:
        resp, create_job = self._edit("inpaint-optional", data={"denoise": "0.6"}, mask=b"fake-mask")
        self.assertEqual(resp.status_code, 200, resp.text)
        params = create_job.await_args.kwargs["standard_params"]
        self.assertEqual(params["denoise"], 0.6)
        self.assertTrue(params["mask"])  # stored input image reference
        self.assertNotEqual(params["mask"], params.get("image"))

    def test_optional_mask_may_be_omitted(self) -> None:
        resp, create_job = self._edit("inpaint-optional")
        self.assertEqual(resp.status_code, 200, resp.text)
        params = create_job.await_args.kwargs["standard_params"]
        self.assertNotIn("mask", params)
        self.assertNotIn("denoise", params)

    def test_required_mask_missing_is_400(self) -> None:
        resp, create_job = self._edit("inpaint-required")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("requires a mask", resp.json()["error"]["message"])
        create_job.assert_not_awaited()

    def test_mask_rejected_when_workflow_has_no_mask_parameter(self) -> None:
        resp, create_job = self._edit("inpaint-none", mask=b"fake-mask")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("does not support a separate mask", resp.json()["error"]["message"])
        create_job.assert_not_awaited()

    def test_empty_mask_file_is_400(self) -> None:
        resp, create_job = self._edit("inpaint-optional", mask=b"")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("empty", resp.json()["error"]["message"])
        create_job.assert_not_awaited()

    def test_denoise_out_of_range_is_rejected(self) -> None:
        resp, create_job = self._edit("inpaint-optional", data={"denoise": "1.5"})
        self.assertGreaterEqual(resp.status_code, 400)
        self.assertLess(resp.status_code, 500)
        create_job.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
