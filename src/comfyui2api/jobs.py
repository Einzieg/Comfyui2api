from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .comfy_client import ComfyApiError, ComfyUIClient
from .comfy_workflow import iter_file_outputs, prepare_prompt
from .config import Config
from .util import guess_media_type, json_dumps, pick_primary_url, sanitize_filename_part, utc_now_iso, utc_now_unix
from .workflow_registry import WorkflowDefinition, WorkflowRegistry


@dataclass
class JobOutput:
    filename: str
    url: str
    media_type: str
    node_id: str
    output_key: str


@dataclass
class Job:
    job_id: str
    created_at_utc: str
    created_at: int
    status: str
    kind: str
    workflow: str
    requested_model: str = ""
    seconds: str = ""
    size: str = ""
    quality: str = ""
    metadata: str = ""

    prompt: str = ""
    negative_prompt: str = ""
    image: str = ""

    prompt_node: str = ""
    negative_prompt_node: str = ""
    image_node: str = ""
    overrides: list[tuple[str, str, Any]] = field(default_factory=list)

    prompt_id: str = ""
    client_id: str = ""
    queue_number: Optional[int] = None

    current_node: str = ""
    progress: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    run_dir: str = ""
    outputs: list[JobOutput] = field(default_factory=list)
    url: str = ""

    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class JobManager:
    def __init__(
        self,
        *,
        cfg: Config,
        registry: WorkflowRegistry,
        comfy: ComfyUIClient,
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.comfy = comfy

        self._lock = asyncio.Lock()
        self._jobs: Dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []

        self._subscribers: Dict[str, set[Any]] = {}  # job_id -> set[WebSocket]
        self._sub_lock = asyncio.Lock()

    async def start_workers(self) -> None:
        for i in range(self.cfg.worker_concurrency):
            self._workers.append(asyncio.create_task(self._worker_loop(i), name=f"job-worker-{i}"))

    async def stop_workers(self) -> None:
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def create_job(
        self,
        *,
        kind: str,
        workflow: str,
        prompt: str,
        requested_model: str = "",
        seconds: str = "",
        size: str = "",
        quality: str = "",
        metadata: str = "",
        negative_prompt: str = "",
        image: str = "",
        prompt_node: str = "",
        negative_prompt_node: str = "",
        image_node: str = "",
        overrides: Optional[list[tuple[str, str, Any]]] = None,
    ) -> Job:
        job_id = uuid.uuid4().hex
        job = Job(
            job_id=job_id,
            created_at_utc=utc_now_iso(),
            created_at=utc_now_unix(),
            status="pending",
            kind=kind,
            workflow=workflow,
            requested_model=requested_model or "",
            seconds=seconds or "",
            size=size or "",
            quality=quality or "",
            metadata=metadata or "",
            prompt=prompt or "",
            negative_prompt=negative_prompt or "",
            image=image or "",
            prompt_node=prompt_node or "",
            negative_prompt_node=negative_prompt_node or "",
            image_node=image_node or "",
            overrides=list(overrides or []),
        )
        async with self._lock:
            self._jobs[job_id] = job
        await self._queue.put(job_id)
        await self._publish(job_id, {"type": "job_created", "data": self.public_job(job)})
        return job

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def list_jobs(self, *, limit: int = 100) -> list[Job]:
        async with self._lock:
            return list(self._jobs.values())[-max(1, int(limit)) :]

    def public_job(self, job: Job) -> Dict[str, Any]:
        return {
            "job_id": job.job_id,
            "created_at": job.created_at,
            "created_at_utc": job.created_at_utc,
            "status": job.status,
            "kind": job.kind,
            "workflow": job.workflow,
            "requested_model": job.requested_model or None,
            "seconds": job.seconds or None,
            "size": job.size or None,
            "quality": job.quality or None,
            "metadata": job.metadata or None,
            "prompt_id": job.prompt_id or None,
            "queue_number": job.queue_number,
            "current_node": job.current_node or None,
            "progress": job.progress or None,
            "error": job.error or None,
            "url": job.url or None,
            "outputs": [o.__dict__ for o in (job.outputs or [])],
        }

    async def subscribe(self, job_id: str, ws: Any) -> None:
        async with self._sub_lock:
            self._subscribers.setdefault(job_id, set()).add(ws)

    async def unsubscribe(self, job_id: str, ws: Any) -> None:
        async with self._sub_lock:
            s = self._subscribers.get(job_id)
            if not s:
                return
            s.discard(ws)
            if not s:
                self._subscribers.pop(job_id, None)

    async def _publish(self, job_id: str, event: Dict[str, Any]) -> None:
        payload = copy.deepcopy(event)
        async with self._sub_lock:
            sockets = list(self._subscribers.get(job_id, set()))
        if not sockets:
            return
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                await self.unsubscribe(job_id, ws)

    async def _update(self, job_id: str, **fields: Any) -> Optional[Job]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    async def _worker_loop(self, worker_index: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            except Exception as e:
                await self._update(job_id, status="failed", error=str(e))
                await self._publish(job_id, {"type": "job_failed", "data": {"error": str(e)}})
                job = await self.get_job(job_id)
                if job:
                    job.done.set()
            finally:
                self._queue.task_done()

    async def _resolve_workflow(self, name: str) -> WorkflowDefinition:
        wf = await self.registry.get(name)
        if wf:
            return wf
        for item in await self.registry.list():
            if item.name.lower() == name.lower():
                return item
        raise FileNotFoundError(f"Workflow not found: {name}")

    async def _run_job(self, job_id: str) -> None:
        job = await self.get_job(job_id)
        if not job:
            return

        wf = await self._resolve_workflow(job.workflow)
        job_obj = wf.clone_obj()

        client_id = uuid.uuid4().hex
        await self._update(job_id, client_id=client_id, status="queued")
        await self._publish(job_id, {"type": "job_queued", "data": {"client_id": client_id, "workflow": wf.name}})

        prompt_graph, extra_data, applied = prepare_prompt(
            workflow_obj=job_obj,
            positive_prompt=job.prompt or None,
            negative_prompt=job.negative_prompt or None,
            positive_prompt_node=job.prompt_node or None,
            negative_prompt_node=job.negative_prompt_node or None,
            image=job.image or None,
            image_node=job.image_node or None,
            overrides=job.overrides or [],
        )

        qp = await self.comfy.queue_prompt(prompt=prompt_graph, client_id=client_id, extra_data=extra_data)
        await self._update(job_id, prompt_id=qp.prompt_id, queue_number=qp.number)
        await self._publish(
            job_id,
            {
                "type": "comfyui_prompt_queued",
                "data": {"prompt_id": qp.prompt_id, "queue_number": qp.number, "overrides": applied},
            },
        )

        run_dir = (self.cfg.runs_dir / job_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "prompt.json").write_text(
            json_dumps({"prompt": prompt_graph, "extra_data": extra_data}) + "\n", encoding="utf-8"
        )
        await self._update(job_id, run_dir=str(run_dir))

        ws_task = asyncio.create_task(
            self._monitor_ws(job_id=job_id, client_id=client_id, prompt_id=qp.prompt_id), name=f"job-ws-{job_id[:8]}"
        )
        hist_task = asyncio.create_task(
            self.comfy.wait_for_history_complete(
                prompt_id=qp.prompt_id, timeout_s=self.cfg.timeout_s, poll_interval_s=self.cfg.poll_interval_s
            ),
            name=f"job-hist-{job_id[:8]}",
        )

        history: Dict[str, Any]
        while True:
            done, pending = await asyncio.wait({ws_task, hist_task}, return_when=asyncio.FIRST_COMPLETED)

            if ws_task in done:
                exc = ws_task.exception()
                if exc:
                    hist_task.cancel()
                    await asyncio.gather(hist_task, return_exceptions=True)
                    raise exc
                history = await hist_task
                break

            if hist_task in done:
                history = hist_task.result()
                ws_task.cancel()
                await asyncio.gather(ws_task, return_exceptions=True)
                break

        (run_dir / "history.json").write_text(json_dumps(history) + "\n", encoding="utf-8")

        outputs: list[JobOutput] = []
        for node_id, output_key, fileinfo in iter_file_outputs(history):
            filename = fileinfo.get("filename")
            if not isinstance(filename, str) or not filename:
                continue
            subfolder = fileinfo.get("subfolder", "")
            if not isinstance(subfolder, str):
                subfolder = ""
            folder_type = fileinfo.get("type", "output")
            if not isinstance(folder_type, str):
                folder_type = "output"

            blob = await self.comfy.view_bytes(filename=filename, subfolder=subfolder, folder_type=folder_type)
            safe_node = sanitize_filename_part(node_id, max_len=60)
            safe_name = sanitize_filename_part(Path(filename).name, max_len=120)
            out_name = f"{safe_node}__{safe_name}"
            out_path = run_dir / out_name
            out_path.write_bytes(blob)

            url = f"/runs/{job_id}/{out_name}"
            outputs.append(
                JobOutput(
                    filename=out_name,
                    url=url,
                    media_type=guess_media_type(out_name),
                    node_id=node_id,
                    output_key=output_key,
                )
            )

        primary = pick_primary_url([{"filename": o.filename, "url": o.url} for o in outputs]) if outputs else None
        await self._update(job_id, status="completed", outputs=outputs, url=primary or "")
        await self._publish(
            job_id,
            {
                "type": "job_completed",
                "data": {"url": primary, "outputs": [o.__dict__ for o in outputs]},
            },
        )

        job = await self.get_job(job_id)
        if job:
            job.done.set()

    async def _monitor_ws(self, *, job_id: str, client_id: str, prompt_id: str) -> None:
        async for msg in self.comfy.ws_events(client_id=client_id):
            await self._publish(job_id, {"type": "comfyui_ws", "data": msg})
            mtype = msg.get("type")
            data = msg.get("data", {})
            if mtype == "executing" and isinstance(data, dict):
                pid = data.get("prompt_id")
                node = data.get("node")
                if pid and pid != prompt_id:
                    continue
                if node is None:
                    return
                await self._update(job_id, status="running", current_node=str(node))
                await self._publish(job_id, {"type": "job_running", "data": {"node": str(node)}})
            elif mtype == "progress" and isinstance(data, dict):
                await self._update(job_id, progress=data)
                await self._publish(job_id, {"type": "job_progress", "data": data})
            elif mtype == "execution_error" and isinstance(data, dict):
                pid = data.get("prompt_id")
                if pid and pid != prompt_id:
                    continue
                raise ComfyApiError(str(data))
