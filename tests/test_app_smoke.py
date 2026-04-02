from __future__ import annotations

import asyncio
import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

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
        txt2video_workflow_name = "test_txt2video.json"
        (workflows_dir / txt2video_workflow_name).write_text(
            json.dumps(
                {
                    "prompt": {
                        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
                        "2": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "sample"}},
                        "3": {"class_type": "VideoCombine", "inputs": {"fps": 24, "frames": 96}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        hybrid_video_workflow_name = "test_hybrid_video.json"
        (workflows_dir / hybrid_video_workflow_name).write_text(
            json.dumps(
                {
                    "prompt": {
                        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
                        "2": {"class_type": "LoadImage", "inputs": {"image": "input.png"}},
                        "3": {"class_type": "VHS_VideoCombine", "inputs": {"frame_rate": 24, "images": ["1", 0]}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        dual_input_video_workflow_name = "test_dual_input_video.json"
        (workflows_dir / dual_input_video_workflow_name).write_text(
            json.dumps(
                {
                    "prompt": {
                        "325": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "second prompt"}},
                        "437": {"class_type": "LoadImage", "inputs": {"image": "primary.png"}},
                        "438": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "first prompt"}},
                        "440": {"class_type": "LoadImage", "inputs": {"image": "secondary.png"}},
                        "500": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "sample"}},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (workflows_dir / ".comfyui2api" / "test_dual_input_video.params.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "img2video",
                    "prompt_node": "438.value",
                    "image_node": "437.image",
                    "parameters": {
                        "prompt2": {"type": "string", "maps": [{"target": "325.value"}]},
                        "image2": {"type": "image", "maps": [{"target": "440.image"}]},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        env = {
            "API_TOKEN": "secret-token",
            "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
            "COMFYUI_INPUT_DIR": str(root / "input"),
            "COMFYUI_STARTUP_CHECK": "0",
            "DEFAULT_TXT2IMG_WORKFLOW": workflow_name,
            "DEFAULT_IMG2IMG_WORKFLOW": workflow_name,
            "DEFAULT_IMG2VIDEO_WORKFLOW": workflow_name,
            "ENABLE_WORKFLOW_WATCH": "0",
            "IMAGE_UPLOAD_MODE": "local",
            "MAX_BODY_BYTES": "1024",
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
        cls.txt2video_workflow_name = txt2video_workflow_name
        cls.hybrid_video_workflow_name = hybrid_video_workflow_name
        cls.dual_input_video_workflow_name = dual_input_video_workflow_name

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
        by_id = {item["id"]: item for item in payload["data"]}
        self.assertIn("test_txt2img", by_id)
        self.assertNotIn(self.workflow_name, by_id)
        self.assertEqual(by_id["test_txt2img"]["metadata"]["kind"], "txt2img")

    def test_request_body_limit_returns_413(self) -> None:
        response = self.client.post("/v1/images/generations", json={"prompt": "x" * 2048})
        self.assertEqual(response.status_code, 413)
        payload = response.json()
        self.assertIn("Request body too large", payload["error"]["message"])

    def test_images_generations_accepts_model_id_without_json_suffix(self) -> None:
        mock_create_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-images", requested_model="test_txt2img", created_at=123)
        )
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret-token", "x-comfyui-async": "true"},
                json={"prompt": "cat", "model": "test_txt2img"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["workflow"], self.workflow_name)

    def test_chat_completions_routes_text_prompt_to_txt2img(self) -> None:
        mock_create_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-chat-image", requested_model="test_txt2img", created_at=123)
        )
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer secret-token", "x-comfyui-async": "true"},
                json={
                    "model": "test_txt2img",
                    "messages": [{"role": "user", "content": "draw a cinematic cat"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["model"], "test_txt2img")
        message = payload["choices"][0]["message"]
        self.assertEqual(message["role"], "assistant")
        content = json.loads(message["content"])
        self.assertEqual(content["type"], "generation_job")
        self.assertEqual(content["kind"], "txt2img")
        self.assertEqual(content["status"], "pending")
        self.assertEqual(content["job_id"], "job-chat-image")
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["kind"], "txt2img")
        self.assertEqual(kwargs["workflow"], self.workflow_name)
        self.assertEqual(kwargs["prompt"], "draw a cinematic cat")

    def test_chat_completions_routes_multimodal_prompt_to_img2video(self) -> None:
        image_url = "data:image/png;base64," + base64.b64encode(b"fake-image").decode("ascii")
        mock_create_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-chat-video", requested_model="test_hybrid_video", created_at=123)
        )
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer secret-token", "x-comfyui-async": "true"},
                json={
                    "model": "test_hybrid_video",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "animate this frame"},
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        }
                    ],
                    "fps": 24,
                    "seconds": 5,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        content = json.loads(payload["choices"][0]["message"]["content"])
        self.assertEqual(content["kind"], "img2video")
        self.assertEqual(content["status"], "pending")
        self.assertEqual(content["video_id"], "video_job-chat-video")
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["kind"], "img2video")
        self.assertEqual(kwargs["workflow"], self.hybrid_video_workflow_name)
        self.assertEqual(kwargs["requested_model"], "test_hybrid_video")
        self.assertEqual(kwargs["prompt"], "animate this frame")
        self.assertTrue(kwargs["image"])
        self.assertEqual(kwargs["standard_params"], {"fps": 24, "duration": 5})

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

    def test_images_generations_returns_b64_json_for_base64_response_format(self) -> None:
        from comfyui2api.jobs import Job, JobOutput

        done = asyncio.Event()
        done.set()
        job_id = "job-image-b64"
        out_dir = Path(os.environ["RUNS_DIR"]) / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "image.png"
        raw_bytes = b"fake-image-bytes"
        out_path.write_bytes(raw_bytes)

        job = Job(
            job_id=job_id,
            created_at_utc="2026-03-16T00:00:00Z",
            created_at=123,
            status="completed",
            kind="txt2img",
            workflow=self.workflow_name,
            requested_model="test_txt2img",
            outputs=[
                JobOutput(
                    filename="image.png",
                    url=f"/runs/{job_id}/image.png",
                    media_type="image/png",
                    node_id="2",
                    output_key="images",
                )
            ],
            done=done,
        )

        with (
            patch.object(self.app.state.jobs, "create_job", AsyncMock(return_value=SimpleNamespace(job_id=job_id))),
            patch.object(self.app.state.jobs, "get_job", AsyncMock(return_value=job)),
        ):
            response = self.client.post(
                "/v1/images/generations",
                headers={"Authorization": "Bearer secret-token"},
                json={"prompt": "cat", "response_format": "base64"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(base64.b64decode(payload["data"][0]["b64_json"]), raw_bytes)

    def test_images_edits_rejects_txt2img_workflow_with_clear_400(self) -> None:
        mock_create_job = AsyncMock()
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/images/edits",
                headers={"Authorization": "Bearer secret-token"},
                data={"prompt": "cat", "workflow": self.workflow_name},
                files={"image": ("input.png", b"fake-image", "image/png")},
            )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "invalid_request_error")
        self.assertIn("does not support img2img", payload["error"]["message"])
        self.assertIn("missing LoadImage", payload["error"]["message"])
        mock_create_job.assert_not_awaited()

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
                    "model": self.txt2video_workflow_name,
                    "seconds": "5",
                    "size": "1280x720",
                    "fps": "24",
                    "frames": "120",
                },
                files={},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["standard_params"], {"duration": "5", "size": "1280x720", "fps": "24", "frames": "120"})

    def test_videos_create_accepts_json_body_with_duration_and_size_fields(self) -> None:
        mock_create_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-video-json", requested_model="test_txt2video", created_at=123)
        )
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer secret-token"},
                json={
                    "prompt": "astronaut walking on the moon",
                    "model": "test_txt2video",
                    "duration": 5,
                    "width": 1280,
                    "height": 720,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "video")
        self.assertEqual(payload["model"], "test_txt2video")
        self.assertEqual(payload["status"], "queued")
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["kind"], "txt2video")
        self.assertEqual(kwargs["workflow"], self.txt2video_workflow_name)
        self.assertEqual(kwargs["requested_model"], "test_txt2video")
        self.assertEqual(kwargs["prompt"], "astronaut walking on the moon")
        self.assertEqual(kwargs["seconds"], "5")
        self.assertEqual(kwargs["standard_params"], {"duration": "5", "width": "1280", "height": "720"})

    def test_videos_create_accepts_hybrid_video_workflow_for_txt2video(self) -> None:
        mock_create_job = AsyncMock(
            return_value=SimpleNamespace(job_id="job-hybrid", requested_model=self.hybrid_video_workflow_name, created_at=123)
        )
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer secret-token"},
                data={
                    "prompt": "cat animation",
                    "model": self.hybrid_video_workflow_name,
                    "seconds": "5",
                },
                files={},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["workflow"], self.hybrid_video_workflow_name)

    def test_videos_create_collects_custom_secondary_prompt_and_image_parameters(self) -> None:
        first_image = "data:image/png;base64," + base64.b64encode(b"first-image").decode("ascii")
        second_image = "data:image/png;base64," + base64.b64encode(b"second-image").decode("ascii")
        mock_create_job = AsyncMock(
            return_value=SimpleNamespace(
                job_id="job-dual-input",
                requested_model=self.dual_input_video_workflow_name,
                created_at=123,
            )
        )
        with patch.object(self.app.state.jobs, "create_job", mock_create_job):
            response = self.client.post(
                "/v1/videos",
                headers={"Authorization": "Bearer secret-token"},
                json={
                    "prompt": "primary prompt",
                    "prompt2": "secondary prompt",
                    "model": self.dual_input_video_workflow_name,
                    "image": first_image,
                    "image2": second_image,
                    "duration": 5,
                },
            )

        self.assertEqual(response.status_code, 200)
        kwargs = mock_create_job.await_args.kwargs
        self.assertEqual(kwargs["workflow"], self.dual_input_video_workflow_name)
        self.assertEqual(kwargs["prompt"], "primary prompt")
        self.assertEqual(kwargs["standard_params"]["duration"], "5")
        self.assertEqual(kwargs["standard_params"]["prompt2"], "secondary prompt")
        self.assertTrue(kwargs["standard_params"]["image2"])

    def test_videos_get_returns_signed_content_url(self) -> None:
        from comfyui2api.jobs import Job, JobOutput

        done = asyncio.Event()
        done.set()
        job = Job(
            job_id="job-video-content",
            created_at_utc="2026-03-16T00:00:00Z",
            created_at=123,
            status="completed",
            kind="txt2video",
            workflow=self.txt2video_workflow_name,
            requested_model=self.txt2video_workflow_name,
            outputs=[
                JobOutput(
                    filename="clip.mp4",
                    url="/runs/job-video-content/clip.mp4",
                    media_type="video/mp4",
                    node_id="2",
                    output_key="images",
                )
            ],
            done=done,
        )

        with patch.object(self.app.state.jobs, "get_job", AsyncMock(return_value=job)):
            response = self.client.get(
                "/v1/videos/video_job-video-content",
                headers={"Authorization": "Bearer secret-token"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        parsed = urlparse(payload["url"])
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/v1/videos/video_job-video-content/content")
        self.assertIn("exp", params)
        self.assertIn("sig", params)

    def test_videos_get_maps_running_status_to_in_progress(self) -> None:
        from comfyui2api.jobs import Job

        job = Job(
            job_id="job-video-running",
            created_at_utc="2026-03-16T00:00:00Z",
            created_at=123,
            status="running",
            kind="txt2video",
            workflow=self.txt2video_workflow_name,
            requested_model=self.txt2video_workflow_name,
        )

        with patch.object(self.app.state.jobs, "get_job", AsyncMock(return_value=job)):
            response = self.client.get(
                "/v1/videos/video_job-video-running",
                headers={"Authorization": "Bearer secret-token"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "in_progress")
        self.assertEqual(payload["progress"], 0)
        self.assertIsNone(payload["url"])

    def test_videos_content_accepts_query_api_key(self) -> None:
        from comfyui2api.jobs import Job, JobOutput

        done = asyncio.Event()
        done.set()
        job_id = "job-video-download"
        out_dir = Path(os.environ["RUNS_DIR"]) / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "clip.mp4"
        out_path.write_bytes(b"fake-video")

        job = Job(
            job_id=job_id,
            created_at_utc="2026-03-16T00:00:00Z",
            created_at=123,
            status="completed",
            kind="txt2video",
            workflow=self.txt2video_workflow_name,
            requested_model=self.txt2video_workflow_name,
            outputs=[
                JobOutput(
                    filename="clip.mp4",
                    url=f"/runs/{job_id}/clip.mp4",
                    media_type="video/mp4",
                    node_id="2",
                    output_key="images",
                )
            ],
            done=done,
        )

        with patch.object(self.app.state.jobs, "get_job", AsyncMock(return_value=job)):
            response = self.client.get(f"/v1/videos/video_{job_id}/content?api_key=secret-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"fake-video")
        self.assertEqual(response.headers["content-type"], "video/mp4")

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

    def test_images_generations_upstream_failure_mentions_comfyui_base_url(self) -> None:
        from comfyui2api.jobs import Job

        done = asyncio.Event()
        done.set()
        failed_job = Job(
            job_id="job-upstream",
            created_at_utc="2026-03-16T00:00:00Z",
            created_at=123,
            status="failed",
            kind="txt2img",
            workflow=self.workflow_name,
            error=(
                "ComfyApiError: ComfyUI /prompt failed: status=502, "
                "url=http://127.0.0.1:8188/prompt, headers={'server': 'nginx'}, body=''"
            ),
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
        self.assertEqual(payload["error"]["job_id"], "job-upstream")
        self.assertEqual(payload["error"]["upstream"], "comfyui")
        self.assertEqual(payload["error"]["comfyui_base_url"], "http://127.0.0.1:8188")
        self.assertIn("ComfyUI upstream unavailable", payload["error"]["message"])
        self.assertIn("status=502", payload["error"]["message"])

    def test_startup_fails_fast_when_comfyui_healthcheck_fails(self) -> None:
        env = {"COMFYUI_STARTUP_CHECK": "1"}
        with patch.dict(os.environ, env, clear=False):
            with patch.object(self.app_module.ComfyUIClient, "system_stats", AsyncMock(side_effect=RuntimeError("bad gateway"))):
                app = self.app_module.create_app()
                with self.assertRaises(RuntimeError) as ctx:
                    with TestClient(app):
                        pass

        self.assertIn("ComfyUI startup check failed", str(ctx.exception))
        self.assertIn("COMFYUI_BASE_URL=http://127.0.0.1:8188", str(ctx.exception))


class JobManagerErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_job_uses_workflow_default_prompt_and_image_nodes(self) -> None:
        import comfyui2api.jobs as jobs_module
        from comfyui2api.workflow_params import WorkflowParameterSpec

        manager = jobs_module.JobManager(
            cfg=SimpleNamespace(worker_concurrency=1, runs_dir=Path(".")),
            registry=SimpleNamespace(),
            comfy=SimpleNamespace(
                object_info=AsyncMock(return_value={}),
                queue_prompt=AsyncMock(side_effect=RuntimeError("stop")),
            ),
        )

        spec = WorkflowParameterSpec(
            version=1,
            kind="img2video",
            parameters={},
            path=Path("hybrid.params.json"),
            prompt_node="339.custom_prompt",
            image_node="167.image",
        )
        workflow = SimpleNamespace(
            name="hybrid.json",
            parameter_spec=spec,
            clone_obj=lambda: {"prompt": {"167": {"class_type": "LoadImage", "inputs": {"image": "input.png"}}}},
        )

        job = await manager.create_job(
            kind="img2video",
            workflow="hybrid.json",
            prompt="hello",
            image="comfyui2api/input.png",
        )

        with patch.object(manager, "_resolve_workflow", AsyncMock(return_value=workflow)), patch.object(
            jobs_module, "prepare_prompt", return_value=({}, None, [], {})
        ) as mock_prepare:
            with self.assertRaises(RuntimeError) as ctx:
                await manager._run_job(job.job_id)

        self.assertEqual(str(ctx.exception), "stop")
        kwargs = mock_prepare.call_args.kwargs
        self.assertEqual(kwargs["positive_prompt_node"], "339.custom_prompt")
        self.assertEqual(kwargs["image_node"], "167.image")
        self.assertIsNone(kwargs["negative_prompt_node"])

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
