from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from comfyui2api import workflow_sync
from comfyui2api.workflow_sync import (
    SavedWorkflow,
    SyncError,
    auto_sync,
    load_map,
    name_of,
    pick_run,
    recent_runs,
    run_cli,
    runs_from_history,
    save_map,
    saved_from_dir,
    saved_workflows,
    write_workflow,
)

GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "x"}},
}


def _history_item(graph: dict, *, workflow_id: str | None, created: int, status: str = "success") -> dict:
    extra: dict = {"create_time": created}
    if workflow_id:
        extra["extra_pnginfo"] = {"workflow": {"id": workflow_id}}
    return {"prompt": [1, "p", graph, extra, ["2"]], "status": {"status_str": status}}


def _client(history: dict, files: dict[str, dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/history":
            return httpx.Response(200, json=history)
        if path == "/userdata":
            return httpx.Response(200, json=sorted(files))
        if path.startswith("/userdata/"):
            name = request.url.raw_path.decode().split("%2F", 1)[1]
            return httpx.Response(200, json=files[name])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


class WorkflowSyncTests(unittest.TestCase):
    def test_pick_run_matches_saved_workflow_by_id(self) -> None:
        history = {
            "a": _history_item(GRAPH, workflow_id="id-1", created=1),
            "b": _history_item({"9": GRAPH["1"], "10": GRAPH["2"]}, workflow_id="id-2", created=2),
            "c": _history_item(GRAPH, workflow_id="id-1", created=3, status="error"),
        }
        client = _client(history, {"one.json": {"id": "id-1"}, "two.json": {"id": "id-2"}})
        runs = recent_runs(client, "http://c")
        names = saved_workflows(client, "http://c")
        self.assertEqual([r.prompt_id for r in runs], ["b", "a"])  # newest first, failures dropped
        self.assertEqual(pick_run(runs, names, "one", None).prompt_id, "a")
        with self.assertRaises(SyncError):
            pick_run(runs, names, "missing", None)

    def test_write_creates_updates_and_keeps_backup(self) -> None:
        client = _client({"a": _history_item(GRAPH, workflow_id=None, created=1)}, {})
        run = recent_runs(client, "http://c")[0]
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertEqual(write_workflow(run, d, "wf").status, "created")
            self.assertEqual(write_workflow(run, d, "wf").status, "unchanged")
            newer = json.loads(json.dumps(GRAPH))
            newer["1"]["inputs"]["text"] = "a dog"
            run2 = recent_runs(_client({"a": _history_item(newer, workflow_id=None, created=2)}, {}), "http://c")[0]
            result = write_workflow(run2, d, "wf.json")
            self.assertEqual((result.status, result.changed), ("updated", ["1"]))
            self.assertEqual(json.loads((d / "wf.json").read_text(encoding="utf-8"))["1"]["inputs"]["text"], "a dog")
            self.assertEqual(json.loads((d / "wf.json.bak").read_text(encoding="utf-8"))["1"]["inputs"]["text"], "a cat")

    def test_dry_run_and_bad_names_write_nothing(self) -> None:
        client = _client({"a": _history_item(GRAPH, workflow_id=None, created=1)}, {})
        run = recent_runs(client, "http://c")[0]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(write_workflow(run, Path(tmp), "wf", dry_run=True).status, "created")
            self.assertEqual(list(Path(tmp).iterdir()), [])
            for bad in ("../x", "a/b", ".hidden"):
                with self.assertRaises(SyncError):
                    write_workflow(run, Path(tmp), bad)


class AutoSyncTests(unittest.TestCase):
    def test_seed_only_change_is_not_an_update(self) -> None:
        graph = {"1": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20}}, "2": GRAPH["2"]}
        again = json.loads(json.dumps(graph))
        again["1"]["inputs"]["seed"] = 2
        steps = json.loads(json.dumps(graph))
        steps["1"]["inputs"]["steps"] = 30
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            first = recent_runs(_client({"a": _history_item(graph, workflow_id=None, created=1)}, {}), "http://c")[0]
            self.assertEqual(write_workflow(first, d, "wf").status, "created")
            for g, expect in ((again, "unchanged"), (steps, "updated")):
                run = recent_runs(_client({"a": _history_item(g, workflow_id=None, created=2)}, {}), "http://c")[0]
                self.assertEqual(write_workflow(run, d, "wf").status, expect)

    def test_auto_sync_maps_adds_and_skips_unrunnable_graphs(self) -> None:
        other = {"1": {"class_type": "Note", "inputs": {}}}
        history = {
            "a": _history_item(GRAPH, workflow_id="id-1", created=3),
            "b": _history_item({"9": GRAPH["1"], "10": GRAPH["2"]}, workflow_id="id-1", created=1),
            "c": _history_item(GRAPH, workflow_id="id-2", created=2),
            "d": _history_item(other, workflow_id="id-3", created=2),
            "e": _history_item(GRAPH, workflow_id="id-unsaved", created=2),
        }
        names = [SavedWorkflow(n, i, frozenset(), {}, 0.0) for i, n in (("id-1", "one.json"), ("id-2", "two.json"), ("id-3", "three.json"))]
        runs = runs_from_history(history)
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            results = auto_sync(runs, names, {"one.json": "api-one.json"}, d)
            self.assertEqual(sorted(r.target.name for r in results), ["api-one.json", "two.json"])  # newest run of one.json
            self.assertEqual(set(json.loads((d / "api-one.json").read_text(encoding="utf-8"))), {"1", "2"})
            self.assertEqual(auto_sync(runs, names, {"one.json": "api-one.json"}, d), [])  # nothing new
            self.assertEqual([r.target.name for r in auto_sync(runs, names, {}, Path(tmp) / "x", auto_add=False)], [])

    def test_saved_from_dir_skips_unreadable_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.json").write_text('{"id": "id-a", "nodes": [{"id": 1, "type": "KSampler"}]}', encoding="utf-8")
            (d / "bad.json").write_text("{", encoding="utf-8")
            (d / "noid.json").write_text("{}", encoding="utf-8")
            found = {s.name: s for s in saved_from_dir(d)}
            self.assertEqual(sorted(found), ["a.json", "noid.json"])
            self.assertEqual((found["a.json"].id, found["noid.json"].id), ("id-a", None))
            self.assertEqual(found["a.json"].signature, frozenset({("1", "KSampler")}))


def _run_from(workflow: dict) -> object:
    extra = {"create_time": 1, "extra_pnginfo": {"workflow": workflow}}
    history = {"p": {"prompt": (0, "p", GRAPH, extra, ["2"]), "status": {"status_str": "success"}}}
    return runs_from_history(history)[0]


class NameOfTests(unittest.TestCase):
    def _saved(self, name: str, wid, types: list, modified: float = 0.0, values=None) -> SavedWorkflow:
        sig = frozenset((str(i), t) for i, t in enumerate(types))
        return SavedWorkflow(name, wid, sig, values or {}, modified)

    def _wf(self, wid, types: list) -> dict:
        return {"id": wid, "nodes": [{"id": i, "type": t, "widgets_values": [i]} for i, t in enumerate(types)]}

    def test_save_as_copies_share_an_id_but_not_a_graph(self) -> None:
        saved = [self._saved("old.json", "same", ["A", "B"]), self._saved("copy.json", "same", ["A", "B", "C"])]
        self.assertEqual(name_of(_run_from(self._wf("same", ["A", "B", "C"])), saved), "copy.json")
        self.assertEqual(name_of(_run_from(self._wf("same", ["A", "B"])), saved), "old.json")

    def test_file_without_id_is_found_by_its_graph(self) -> None:
        saved = [self._saved("noid.json", None, ["A", "B"]), self._saved("other.json", "x", ["Z"])]
        self.assertEqual(name_of(_run_from(self._wf("fresh-runtime-id", ["A", "B"])), saved), "noid.json")

    def test_edited_but_unsaved_graph_falls_back_to_id_then_newest_file(self) -> None:
        saved = [self._saved("a.json", "same", ["A"], modified=1), self._saved("b.json", "same", ["A"], modified=2)]
        self.assertEqual(name_of(_run_from(self._wf("same", ["A", "New"])), saved), "b.json")
        self.assertIsNone(name_of(_run_from(self._wf("other", ["A", "New"])), saved))


class MappingTests(unittest.TestCase):
    def _run(self, tmp: str, **kwargs) -> None:
        history = {"a": _history_item(GRAPH, workflow_id="id-1", created=1)}
        runs = recent_runs(_client(history, {}), "http://c")
        env = {"WORKFLOWS_DIR": f"{tmp}/wf", "DATA_DIR": f"{tmp}/data", "COMFYUI_BASE_URL": "http://c"}
        defaults = dict(workflow=None, prompt_id=None, target=None, list_only=False, dry_run=False)
        with patch.dict(os.environ, env), patch.object(workflow_sync, "recent_runs", return_value=runs), patch.object(
            workflow_sync, "saved_workflows", return_value=[SavedWorkflow("comfy-name.json", "id-1", frozenset(), {}, 0.0)]
        ):
            run_cli(**{**defaults, **kwargs})

    def test_map_roundtrip_and_bad_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "workflow_sync.json"
            self.assertEqual(load_map(path), {})
            save_map(path, {"a.json": "b.json"})
            self.assertEqual(load_map(path), {"a.json": "b.json"})
            path.write_text("[1]", encoding="utf-8")
            self.assertEqual(load_map(path), {})

    def test_as_saves_mapping_and_all_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._run(tmp, workflow="comfy-name", target="api-name.json")
            self.assertEqual(load_map(Path(tmp) / "data" / "workflow_sync.json"), {"comfy-name.json": "api-name.json"})
            self.assertTrue((Path(tmp) / "wf" / "api-name.json").exists())
            (Path(tmp) / "wf" / "api-name.json").unlink()
            self._run(tmp, all_mapped=True)
            self.assertTrue((Path(tmp) / "wf" / "api-name.json").exists())
            self._run(tmp, workflow="comfy-name")  # no --as: the saved mapping decides the file name
            self.assertEqual(sorted(p.name for p in (Path(tmp) / "wf").iterdir()), ["api-name.json"])

    def test_all_without_mapping_and_dry_run_save_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SyncError):
                self._run(tmp, all_mapped=True)
            self._run(tmp, workflow="comfy-name", target="x.json", dry_run=True)
            self.assertFalse((Path(tmp) / "data").exists())


if __name__ == "__main__":
    unittest.main()
