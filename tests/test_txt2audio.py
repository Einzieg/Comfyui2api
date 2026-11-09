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

from comfyui2api.comfy_workflow import (
    detect_capabilities,
    find_speech_target,
    find_text_prompt_targets,
    prepare_prompt,
)

TTS_PROMPT = {
    "1": {
        "class_type": "AnomalousTTS_CharacterSpeech",
        "inputs": {"character": "伊莉雅", "text": "old line", "language": "ja"},
    },
    "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": "tts/api"}},
}


class TxtToAudioWorkflowTests(unittest.TestCase):
    def test_detects_txt2audio(self) -> None:
        caps = detect_capabilities(TTS_PROMPT)
        self.assertEqual(caps.kind, "txt2audio")
        self.assertTrue(caps.has_save_audio)
        self.assertFalse(caps.has_save_image)

    def test_image_workflow_is_not_audio(self) -> None:
        caps = detect_capabilities({"1": {"class_type": "SaveImage", "inputs": {}}})
        self.assertEqual(caps.kind, "txt2img")
        self.assertFalse(caps.has_save_audio)

    def test_speech_target_follows_save_audio_link(self) -> None:
        self.assertEqual(find_speech_target(TTS_PROMPT), ("1", "text", "character"))
        self.assertIsNone(find_speech_target({"2": {"class_type": "SaveAudio", "inputs": {"audio": ["9", 0]}}}))

    def test_targets_expose_speech_text_node(self) -> None:
        pos, _neg = find_text_prompt_targets(TTS_PROMPT)
        self.assertEqual([(p[0], p[1]) for p in pos], [("1", "text")])

    def test_prepare_prompt_fills_text_and_voice(self) -> None:
        graph, _extra, _applied, _trace = prepare_prompt(
            workflow_obj=json.loads(json.dumps(TTS_PROMPT)),
            positive_prompt="先生、おはようございます！",
            negative_prompt=None,
            positive_prompt_node=None,
            negative_prompt_node=None,
            image=None,
            image_node=None,
            overrides=[("1", "character", "美游")],
        )
        self.assertEqual(graph["1"]["inputs"]["text"], "先生、おはようございます！")
        self.assertEqual(graph["1"]["inputs"]["character"], "美游")


class AudioSpeechEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        root = Path(cls.tempdir.name)
        workflows_dir = root / "workflows"
        workflows_dir.mkdir()
        cls.runs_dir = root / "runs"
        cls.runs_dir.mkdir()
        (workflows_dir / "anomalous-tts.json").write_text(json.dumps(TTS_PROMPT, ensure_ascii=False), encoding="utf-8")
        (workflows_dir / "img.json").write_text(
            json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}), encoding="utf-8"
        )
        env = {
            "API_TOKEN": "secret-token",
            "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
            "COMFYUI_INPUT_DIR": str(root / "input"),
            "COMFYUI_STARTUP_CHECK": "0",
            "DATA_DIR": str(root / "data"),
            "DATABASE_PATH": str(root / "data" / "comfyui2api.db"),
            "ENABLE_WORKFLOW_WATCH": "0",
            "RUNS_DIR": str(cls.runs_dir),
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
        self.headers = {"Authorization": "Bearer secret-token"}

    def tearDown(self) -> None:
        self.client_cm.__exit__(None, None, None)

    def _fake_done(self, job_id: str, name: str = "1__api_00001_.flac", data: bytes = b"fLaC-bytes") -> dict:
        run_dir = self.runs_dir / job_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / name).write_bytes(data)
        return {"job_id": job_id, "outputs": [{"filename": name, "url": f"/runs/{job_id}/{name}"}]}

    def test_workflows_list_shows_txt2audio(self) -> None:
        resp = self.client.get("/v1/workflows", headers=self.headers)
        kinds = {item["name"]: item["kind"] for item in resp.json()["items"]}
        self.assertEqual(kinds["anomalous-tts.json"], "txt2audio")

    def test_workflow_targets_detect_speech_text(self) -> None:
        resp = self.client.get("/v1/workflows/anomalous-tts.json/targets", headers=self.headers)
        self.assertEqual(resp.json()["targets"]["positive_prompt"]["autodetect"], "1.text")

    def test_speech_requires_auth_and_input(self) -> None:
        self.assertEqual(self.client.post("/v1/audio/speech", json={"input": "hi"}).status_code, 401)
        self.assertEqual(self.client.post("/v1/audio/speech", json={}, headers=self.headers).status_code, 400)

    def test_speech_rejects_non_audio_workflow(self) -> None:
        resp = self.client.post(
            "/v1/audio/speech", json={"input": "hi", "model": "img.json"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("does not support txt2audio", resp.json()["error"]["message"])

    def test_speech_rejects_unknown_format(self) -> None:
        resp = self.client.post(
            "/v1/audio/speech", json={"input": "hi", "response_format": "ogg123"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 400)

    def test_speech_returns_flac_and_passes_voice(self) -> None:
        create_job = AsyncMock(return_value=SimpleNamespace(job_id="job-a"))
        wait = AsyncMock(return_value=self._fake_done("job-a"))
        with patch.object(self.app.state.jobs, "create_job", create_job), patch.object(
            self.app_module, "_convert_audio_bytes"
        ) as convert:
            resp = self._post_with_wait(wait, {"input": "先生、おはよう", "voice": "伊莉雅", "language": "ja"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "audio/flac")
        self.assertEqual(resp.content, b"fLaC-bytes")
        convert.assert_not_called()
        kwargs = create_job.await_args.kwargs
        self.assertEqual(kwargs["kind"], "txt2audio")
        self.assertEqual(kwargs["workflow"], "anomalous-tts.json")
        self.assertEqual(kwargs["prompt"], "先生、おはよう")
        self.assertEqual(kwargs["overrides"], [("1", "character", "伊莉雅"), ("1", "language", "ja")])

    def test_speech_converts_to_wav(self) -> None:
        create_job = AsyncMock(return_value=SimpleNamespace(job_id="job-b"))
        wait = AsyncMock(return_value=self._fake_done("job-b"))
        with patch.object(self.app.state.jobs, "create_job", create_job), patch(
            "comfyui2api.app.shutil.which", return_value="ffmpeg"
        ), patch.object(self.app_module, "_convert_audio_bytes", return_value=b"RIFF-wav") as convert:
            resp = self._post_with_wait(wait, {"input": "hi", "response_format": "wav"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "audio/wav")
        self.assertEqual(resp.content, b"RIFF-wav")
        convert.assert_called_once_with(b"fLaC-bytes", fmt="wav")

    def test_speech_wav_without_ffmpeg_is_clear_error(self) -> None:
        with patch("comfyui2api.app.shutil.which", return_value=None):
            resp = self.client.post(
                "/v1/audio/speech", json={"input": "hi", "response_format": "mp3"}, headers=self.headers
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ffmpeg", resp.json()["error"]["message"])

    def _post_with_wait(self, wait: AsyncMock, body: dict):
        # _openai_wait is a closure inside create_app; short-circuit it by completing the fake job instead.
        job = SimpleNamespace(done=SimpleNamespace(wait=AsyncMock()), status="completed")
        done = wait.return_value
        with patch.object(self.app.state.jobs, "get_job", AsyncMock(return_value=job)), patch.object(
            self.app.state.jobs, "public_job", return_value=done
        ):
            return self.client.post("/v1/audio/speech", json=body, headers=self.headers)


if __name__ == "__main__":
    unittest.main()
