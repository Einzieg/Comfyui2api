from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from watchfiles import Change, awatch

from .comfy_workflow import detect_capabilities, extract_prompt_and_extra, read_json


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    path: Path
    mtime_ns: int
    capabilities: Any
    workflow_obj: Any

    def clone_obj(self) -> Any:
        return copy.deepcopy(self.workflow_obj)


class WorkflowRegistry:
    def __init__(self, workflows_dir: Path) -> None:
        self.workflows_dir = workflows_dir
        self._lock = asyncio.Lock()
        self._items: Dict[str, WorkflowDefinition] = {}

    async def load_all(self) -> None:
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        items: Dict[str, WorkflowDefinition] = {}
        for path in sorted(self.workflows_dir.glob("*.json")):
            try:
                wf = self._load_one(path)
                items[wf.name] = wf
            except Exception:
                continue
        async with self._lock:
            self._items = items

    def _load_one(self, path: Path) -> WorkflowDefinition:
        obj = read_json(path)
        prompt, _extra = extract_prompt_and_extra(obj)
        caps = detect_capabilities(prompt)
        stat = path.stat()
        return WorkflowDefinition(
            name=path.name,
            path=path.resolve(),
            mtime_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)),
            capabilities=caps,
            workflow_obj=obj,
        )

    async def get(self, name: str) -> Optional[WorkflowDefinition]:
        key = (name or "").strip()
        async with self._lock:
            return self._items.get(key)

    async def list(self) -> list[WorkflowDefinition]:
        async with self._lock:
            return list(self._items.values())

    async def reload_path(self, path: Path) -> None:
        if not path.exists() or path.suffix.lower() != ".json":
            return
        wf = self._load_one(path)
        async with self._lock:
            self._items[wf.name] = wf

    async def remove_name(self, name: str) -> None:
        async with self._lock:
            self._items.pop(name, None)

    async def watch_forever(self) -> None:
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        async for changes in awatch(self.workflows_dir):
            for change, raw_path in changes:
                p = Path(raw_path)
                if p.suffix.lower() != ".json":
                    continue
                if change in {Change.deleted}:
                    await self.remove_name(p.name)
                else:
                    try:
                        await self.reload_path(p)
                    except Exception:
                        continue

