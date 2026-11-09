from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSION = PROJECT_ROOT / "comfyui_extension" / "comfyui2api_sync" / "__init__.py"

GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "x"}},
}


class FakeQueue:
    def __init__(self) -> None:
        extra = {"create_time": 1, "extra_pnginfo": {"workflow": {"id": "wf-1"}}}
        self.history = {"p1": {"prompt": (0, "p1", GRAPH, extra, ["2"]), "status": {"status_str": "success"}}}

    def get_history(self, max_items=None):
        return dict(self.history)


class ExtensionTests(unittest.TestCase):
    def _load(self, user_dir: Path, queue: FakeQueue):
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_user_directory = lambda: str(user_dir)
        server = types.ModuleType("server")
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(prompt_queue=queue))
        sys.modules.update(folder_paths=folder_paths, server=server)
        spec = importlib.util.spec_from_file_location("comfyui2api_sync_under_test", EXTENSION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def tearDown(self) -> None:
        for name in ("folder_paths", "server"):
            sys.modules.pop(name, None)

    def test_no_config_does_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load(Path(tmp), FakeQueue())
            self.assertEqual(module.NODE_CLASS_MAPPINGS, {})

    def test_finished_run_of_a_saved_workflow_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user"
            (user / "default" / "workflows").mkdir(parents=True)
            (user / "default" / "workflows" / "My Flow.json").write_text('{"id": "wf-1"}', encoding="utf-8")
            out = Path(tmp) / "out"
            (user / "comfyui2api_sync.json").write_text(
                json.dumps(
                    {
                        "project": str(PROJECT_ROOT),
                        "workflows_dir": str(out),
                        "map_file": str(Path(tmp) / "map.json"),
                        "interval_s": 0.05,
                    }
                ),
                encoding="utf-8",
            )
            self._load(user, FakeQueue())
            target = out / "My Flow.json"
            deadline = time.monotonic() + 5
            while not target.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), GRAPH)


if __name__ == "__main__":
    unittest.main()
