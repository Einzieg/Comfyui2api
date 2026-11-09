"""Copy the API-format graph of a finished ComfyUI run into the workflows directory.

ComfyUI keeps every run's API prompt in ``/history``; the saved workflow files are UI format, which
this service cannot load. Taking the graph from a run avoids converting UI JSON ourselves.
A run is tied to its saved workflow through ``extra_pnginfo.workflow.id`` (the saved file's ``id``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import quote

if TYPE_CHECKING:  # httpx is imported lazily: the ComfyUI extension only needs the pure functions
    import httpx

from .comfy_workflow import detect_capabilities, extract_prompt_and_extra, read_json


class SyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class HistoryRun:
    prompt_id: str
    created_ms: int
    prompt: Dict[str, Any]
    workflow_id: Optional[str]
    signature: frozenset = frozenset()  # (node id, type) of the canvas graph the run was started from
    values: Dict[str, Any] = field(default_factory=dict, compare=False)  # node id -> widgets_values


@dataclass(frozen=True)
class SavedWorkflow:
    name: str  # file name in ComfyUI's workflows folder
    id: Optional[str]
    signature: frozenset
    values: Dict[str, Any]
    modified: float


@dataclass(frozen=True)
class SyncResult:
    target: Path
    prompt_id: str
    status: str  # created | updated | unchanged
    added: List[str]
    removed: List[str]
    changed: List[str]


def _get_json(client: httpx.Client, base_url: str, path: str, **params: Any) -> Any:
    import httpx

    try:
        r = client.get(base_url.rstrip("/") + path, params=params or None)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise SyncError(f"ComfyUI {path} failed: {exc}") from exc


def recent_runs(client: httpx.Client, base_url: str, *, limit: int = 100) -> List[HistoryRun]:
    """Successful runs, newest first."""
    return runs_from_history(_get_json(client, base_url, "/history", max_items=limit))


def runs_from_history(history: Any) -> List[HistoryRun]:
    """Successful runs of a ComfyUI history dict (``/history`` body or ``prompt_queue.get_history()``), newest first."""
    runs: List[HistoryRun] = []
    for prompt_id, item in (history or {}).items():
        entry = item.get("prompt") if isinstance(item, dict) else None
        status = (item.get("status") or {}).get("status_str") if isinstance(item, dict) else None
        if status != "success" or not isinstance(entry, (list, tuple)) or len(entry) < 4 or not isinstance(entry[2], dict):
            continue
        extra = entry[3] if isinstance(entry[3], dict) else {}
        workflow = (extra.get("extra_pnginfo") or {}).get("workflow")
        workflow_id = workflow.get("id") if isinstance(workflow, dict) else None
        signature, values = _canvas_facts(workflow)
        created = extra.get("create_time")
        runs.append(
            HistoryRun(
                prompt_id=str(prompt_id),
                created_ms=int(created) if isinstance(created, (int, float)) else 0,
                prompt=entry[2],
                workflow_id=workflow_id if isinstance(workflow_id, str) else None,
                signature=signature,
                values=values,
            )
        )
    runs.sort(key=lambda r: r.created_ms, reverse=True)
    return runs


def _canvas_facts(doc: Any) -> tuple[frozenset, Dict[str, Any]]:
    nodes = doc.get("nodes") if isinstance(doc, dict) else None
    if not isinstance(nodes, list):
        return frozenset(), {}
    nodes = [n for n in nodes if isinstance(n, dict)]
    signature = frozenset((str(n.get("id")), str(n.get("type"))) for n in nodes)
    return signature, {str(n.get("id")): n.get("widgets_values") for n in nodes}


def _saved(name: str, doc: Any, modified: float) -> Optional[SavedWorkflow]:
    if not isinstance(doc, dict):
        return None
    signature, values = _canvas_facts(doc)
    wid = doc.get("id")
    return SavedWorkflow(name, wid if isinstance(wid, str) else None, signature, values, modified)


def saved_workflows(client: httpx.Client, base_url: str) -> List[SavedWorkflow]:
    """The workflows saved in ComfyUI (top-level files of its ``workflows`` folder)."""
    entries = _get_json(client, base_url, "/userdata", dir="workflows", recurse="true", split="false", full_info="true")
    out: List[SavedWorkflow] = []
    for entry in entries if isinstance(entries, list) else []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(path, str) or not path.lower().endswith(".json") or "/" in path or "\\" in path:
            continue
        modified = float(entry.get("modified") or 0) if isinstance(entry, dict) else 0.0
        try:
            doc = _get_json(client, base_url, "/userdata/" + quote(f"workflows/{path}", safe=""))
        except SyncError:
            continue
        item = _saved(path, doc, modified)
        if item:
            out.append(item)
    return out


def saved_from_dir(workflows_dir: Path) -> List[SavedWorkflow]:
    """Same, read from the user's ``workflows`` folder on disk."""
    out: List[SavedWorkflow] = []
    for path in sorted(workflows_dir.glob("*.json")):
        try:
            item = _saved(path.name, json.loads(path.read_text(encoding="utf-8")), path.stat().st_mtime)
        except (OSError, ValueError):
            continue
        if item:
            out.append(item)
    return out


def name_of(run: HistoryRun, saved: List[SavedWorkflow]) -> Optional[str]:
    """The saved workflow a run was started from. A workflow id is not enough: "Save As" copies keep the
    id, and a file written without one gets a new id every time it is opened. So the same graph shape
    comes first, then the id, then how many widget values still agree, then the newest file."""
    best: Optional[tuple] = None
    for item in saved:
        same_graph = bool(run.signature) and item.signature == run.signature
        same_id = bool(run.workflow_id) and item.id == run.workflow_id
        if not (same_graph or same_id):
            continue
        agree = sum(1 for k, v in run.values.items() if item.values.get(k) == v)
        key = (same_graph, same_id, agree, item.modified)
        if best is None or key > best[0]:
            best = (key, item.name)
    return best[1] if best else None


def pick_run(runs: List[HistoryRun], saved: List[SavedWorkflow], workflow_name: Optional[str], prompt_id: Optional[str]) -> HistoryRun:
    if prompt_id:
        for run in runs:
            if run.prompt_id == prompt_id:
                return run
        raise SyncError(f"No successful run {prompt_id} in the recent ComfyUI history.")
    if not workflow_name:
        if not runs:
            raise SyncError("ComfyUI history has no successful run.")
        return runs[0]
    wanted = workflow_name if workflow_name.lower().endswith(".json") else workflow_name + ".json"
    for run in runs:
        if name_of(run, saved) == wanted:
            return run
    raise SyncError(f"No recent successful run of the ComfyUI workflow {wanted!r}. Run it once in ComfyUI, then retry.")


SEED_KEYS = {"seed", "noise_seed"}


def _without_seeds(node: Any) -> Any:
    """A node with plain seed values blanked: ComfyUI changes them on every run, which is not an edit."""
    if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
        return node
    inputs = {k: (None if k in SEED_KEYS and not isinstance(v, list) else v) for k, v in node["inputs"].items()}
    return {**node, "inputs": inputs}


def _diff(old: Dict[str, Any], new: Dict[str, Any]) -> tuple[List[str], List[str], List[str]]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if _without_seeds(old[k]) != _without_seeds(new[k]))
    return added, removed, changed


def write_workflow(run: HistoryRun, workflows_dir: Path, target_name: str, *, dry_run: bool = False) -> SyncResult:
    name = target_name if target_name.lower().endswith(".json") else target_name + ".json"
    if Path(name).name != name or name.startswith("."):
        raise SyncError(f"Invalid workflow file name: {target_name!r}")
    prompt, _ = extract_prompt_and_extra(run.prompt)
    detect_capabilities(prompt)  # same checks the registry applies on load

    target = workflows_dir / name
    old: Dict[str, Any] = {}
    if target.exists():
        try:
            old, _ = extract_prompt_and_extra(read_json(target))
        except Exception:
            old = {}
    added, removed, changed = _diff(old, prompt)
    if target.exists() and not (added or removed or changed):
        return SyncResult(target, run.prompt_id, "unchanged", [], [], [])
    status = "updated" if target.exists() else "created"
    if not dry_run:
        workflows_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            os.replace(target, target.with_suffix(".json.bak"))
        tmp = workflows_dir / f".{name}.tmp"  # the directory watcher only reacts to *.json
        tmp.write_text(json.dumps(prompt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    return SyncResult(target, run.prompt_id, status, added, removed, changed)


MAP_FILENAME = "workflow_sync.json"  # in the data dir, not the workflows dir: the watcher would load it as a workflow


def _json_name(name: str) -> str:
    return name if name.lower().endswith(".json") else name + ".json"


def load_map(path: Path) -> Dict[str, str]:
    """ComfyUI saved workflow file name -> file name in the workflows directory."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(doc, dict) else {}


def save_map(path: Path, mapping: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _report(result: SyncResult, dry_run: bool) -> None:
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}{result.status}: {result.target} (run {result.prompt_id})")
    for label, ids in (("added nodes", result.added), ("removed nodes", result.removed), ("changed nodes", result.changed)):
        if ids:
            print(f"  {label}: {', '.join(ids)}")
    if result.removed or result.added:
        print("  node ids changed: re-check this workflow's .params.json if it has one")


def run_cli(
    *,
    workflow: Optional[str],
    prompt_id: Optional[str],
    target: Optional[str],
    list_only: bool,
    dry_run: bool,
    all_mapped: bool = False,
) -> int:
    import httpx

    from .config import load_config

    cfg = load_config()
    map_path = cfg.data_dir / MAP_FILENAME
    mapping = load_map(map_path)
    with httpx.Client(timeout=30, trust_env=False) as client:
        runs = recent_runs(client, cfg.comfy_base_url)
        saved = saved_workflows(client, cfg.comfy_base_url)
        if list_only:
            for run in runs[:20]:
                print(f"{run.prompt_id}  {name_of(run, saved) or '(not a saved workflow)'}  nodes={len(run.prompt)}")
            return 0
        if all_mapped:
            if not mapping:
                raise SyncError("No mapping saved yet: sync a workflow once with --as <file name>.")
            for source, dest in sorted(mapping.items()):
                try:
                    run = pick_run(runs, saved, source, None)
                except SyncError:
                    print(f"skipped {source}: no recent successful run")
                    continue
                _report(write_workflow(run, cfg.workflows_dir, dest, dry_run=dry_run), dry_run)
            return 0
        run = pick_run(runs, saved, workflow, prompt_id)
    source = _json_name(workflow) if workflow else name_of(run, saved)
    name = target or (mapping.get(source) if source else None) or source
    if not name:
        raise SyncError("This run is not tied to a saved ComfyUI workflow; pass --as <file name>.")
    result = write_workflow(run, cfg.workflows_dir, name, dry_run=dry_run)
    _report(result, dry_run)
    if target and source and not dry_run and mapping.get(source) != result.target.name:
        mapping[source] = result.target.name
        save_map(map_path, mapping)
        print(f"  saved mapping: {source} -> {result.target.name}")
    return 0


def auto_sync(
    runs: List[HistoryRun],
    saved: List[SavedWorkflow],
    mapping: Dict[str, str],
    workflows_dir: Path,
    *,
    auto_add: bool = True,
) -> List[SyncResult]:
    """Write the newest run of each saved workflow. A mapped workflow goes to its mapped file; an unmapped one
    (when ``auto_add``) to a file of the same name, unless its graph is not a kind this service can run."""
    results: List[SyncResult] = []
    done: set[str] = set()
    for run in runs:  # newest first: the first run of a workflow is the one to keep
        source = name_of(run, saved)
        if not source or source in done:
            continue
        done.add(source)
        dest = mapping.get(source)
        if dest is None:
            if not auto_add:
                continue
            prompt, _ = extract_prompt_and_extra(run.prompt)
            if detect_capabilities(prompt).kind == "unknown":
                continue
            dest = source
        result = write_workflow(run, workflows_dir, dest)
        if result.status != "unchanged":
            results.append(result)
    return results
