"""ComfyUI custom node: keep comfyui2api's workflows folder in step with finished runs.

Every few seconds the node looks at ComfyUI's own run history. The newest successful run of each saved
workflow is written to comfyui2api's workflows folder as an API-format file (see
``comfyui2api/workflow_sync.py`` for the rules). It adds no nodes and no web UI.

Setup: ``ComfyUI/user/comfyui2api_sync.json``
    {"project": "D:/path/to/Comfyui2api", "auto_add": true}
``workflows_dir``, ``map_file`` and ``interval_s`` can be set too; they default to the project's own folders. Without
this file the node does nothing.
"""

import json
import logging
import sys
import threading
import time
from pathlib import Path

NODE_CLASS_MAPPINGS = {}
CONFIG_NAME = "comfyui2api_sync.json"
INTERVAL_S = 3.0  # default for "interval_s" in the config
HISTORY_ITEMS = 50

log = logging.getLogger("comfyui2api_sync")


def _read_config(user_dir: Path):
    path = user_dir / CONFIG_NAME
    if not path.is_file():
        log.info("comfyui2api_sync: %s not found, not syncing", path)
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        project = Path(cfg["project"]).expanduser()
        if not (project / "src" / "comfyui2api").is_dir():
            raise ValueError(f"{project} is not a comfyui2api checkout")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("comfyui2api_sync: bad config %s: %s", path, exc)
        return None
    return {
        "project": project,
        "workflows_dir": Path(cfg.get("workflows_dir") or project / "comfyui-api-workflows").expanduser(),
        "map_file": Path(cfg.get("map_file") or project / "data" / "workflow_sync.json").expanduser(),
        "auto_add": cfg.get("auto_add", True) is not False,
        "interval_s": float(cfg.get("interval_s") or INTERVAL_S),
    }


def _watch(cfg, prompt_queue, saved_dir: Path) -> None:
    sys.path.insert(0, str(cfg["project"] / "src"))
    from comfyui2api.workflow_sync import auto_sync, load_map, runs_from_history, saved_from_dir

    seen = set()
    while True:
        time.sleep(cfg["interval_s"])
        try:
            runs = [r for r in runs_from_history(prompt_queue.get_history(max_items=HISTORY_ITEMS)) if r.prompt_id not in seen]
            if not runs:
                continue
            seen.update(r.prompt_id for r in runs)
            results = auto_sync(
                runs, saved_from_dir(saved_dir), load_map(cfg["map_file"]), cfg["workflows_dir"], auto_add=cfg["auto_add"]
            )
            for r in results:
                log.info("comfyui2api_sync: %s %s", r.status, r.target)
        except Exception:
            log.exception("comfyui2api_sync: sync failed")


def _start() -> None:
    try:
        import folder_paths
        from server import PromptServer
    except ImportError:  # imported outside ComfyUI
        return
    user_dir = Path(folder_paths.get_user_directory())
    cfg = _read_config(user_dir)
    if cfg is None:
        return
    saved_dir = user_dir / "default" / "workflows"
    threading.Thread(
        target=_watch, args=(cfg, PromptServer.instance.prompt_queue, saved_dir), name="comfyui2api-sync", daemon=True
    ).start()
    log.info("comfyui2api_sync: watching runs, writing to %s", cfg["workflows_dir"])


_start()
