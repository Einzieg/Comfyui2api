from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from watchfiles import Change, awatch

from .comfy_workflow import detect_capabilities, extract_prompt_and_extra, read_json
from .workflow_params import (
    WorkflowParameterSpec,
    load_workflow_parameter_spec,
    parameter_sidecar_dir,
    workflow_path_from_sidecar,
)


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    path: Path
    mtime_ns: int
    capabilities: Any
    workflow_obj: Any
    parameter_spec: WorkflowParameterSpec | None = None
    parameter_error: str | None = None

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
        parameter_spec: WorkflowParameterSpec | None = None
        parameter_error: str | None = None
        try:
            parameter_spec = load_workflow_parameter_spec(
                workflows_dir=self.workflows_dir,
                workflow_path=path,
                expected_kind=caps.kind,
            )
        except Exception as e:
            parameter_error = str(e)
        stat = path.stat()
        return WorkflowDefinition(
            name=path.name,
            path=path.resolve(),
            mtime_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)),
            capabilities=caps,
            workflow_obj=obj,
            parameter_spec=parameter_spec,
            parameter_error=parameter_error,
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
        root_dir = self.workflows_dir.resolve()
        sidecar_dir = parameter_sidecar_dir(self.workflows_dir).resolve()
        async for changes in awatch(self.workflows_dir):
            for change, raw_path in changes:
                p = Path(raw_path).resolve()
                if p.parent == sidecar_dir and p.name.endswith(".params.json"):
                    try:
                        workflow_path = workflow_path_from_sidecar(self.workflows_dir, p)
                    except Exception:
                        continue
                    if workflow_path.exists():
                        try:
                            await self.reload_path(workflow_path)
                        except Exception:
                            continue
                    else:
                        await self.remove_name(workflow_path.name)
                    continue

                if p.parent != root_dir or p.suffix.lower() != ".json":
                    continue
                if change in {Change.deleted}:
                    await self.remove_name(p.name)
                else:
                    try:
                        await self.reload_path(p)
                    except Exception:
                        continue
