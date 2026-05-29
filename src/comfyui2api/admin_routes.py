from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect

from .config import Config
from .job_store import JobStore
from .jobs import JobManager
from .signed_urls import create_signed_query, signing_secret
from .util import bearer_authorized


def create_admin_router() -> APIRouter:
    router = APIRouter(prefix="/v1/admin", tags=["admin"])

    @router.get("/tasks")
    async def list_tasks(
        request: Request,
        start: str | None = None,
        end: str | None = None,
        q: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        platform: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        cfg = _cfg(request)
        _require_admin_auth(cfg, authorization)
        store = _store(request)
        return await store.list_tasks(
            start=start,
            end=end,
            q=q,
            statuses=_split_csv(status),
            kinds=_split_csv(kind),
            platforms=_split_csv(platform),
            limit=limit,
            offset=offset,
        )

    @router.get("/tasks/{job_id}")
    async def get_task(
        request: Request,
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        cfg = _cfg(request)
        _require_admin_auth(cfg, authorization)
        payload = await _store(request).get_task(job_id)
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": {"message": "Task not found"}})
        return _rewrite_task_payload_urls(request, cfg, payload)

    @router.get("/stats")
    async def stats(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        cfg = _cfg(request)
        _require_admin_auth(cfg, authorization)
        base = await _store(request).stats()
        base.update(
            {
                "worker_concurrency": cfg.worker_concurrency,
                "comfyui_base_url": cfg.comfy_base_url,
                "workflows_dir": str(cfg.workflows_dir),
                "runs_dir": str(cfg.runs_dir),
                "database_path": str(cfg.database_path),
                "ui_enabled": cfg.ui_enabled,
            }
        )
        return base

    @router.get("/workflows")
    async def workflows(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        cfg = _cfg(request)
        _require_admin_auth(cfg, authorization)
        registry = request.app.state.registry
        items: list[dict[str, Any]] = []
        for wf in await registry.list():
            items.append(
                {
                    "name": wf.name,
                    "kind": wf.capabilities.kind,
                    "available": True,
                    "load_error": None,
                    "parameter_error": wf.parameter_error,
                }
            )
        for load_error in await registry.list_load_errors():
            items.append(
                {
                    "name": load_error.name,
                    "kind": None,
                    "available": False,
                    "load_error": load_error.error,
                    "parameter_error": None,
                }
            )
        items.sort(key=lambda item: str(item.get("name") or "").lower())
        return {"workflows_dir": str(cfg.workflows_dir), "items": items}

    @router.post("/shutdown")
    async def shutdown(request: Request, authorization: str | None = Header(default=None)) -> dict[str, str]:
        cfg = _cfg(request)
        _require_admin_auth(cfg, authorization)
        _require_local_request(request)
        callback = getattr(request.app.state, "shutdown_callback", None)
        if not callable(callback):
            raise HTTPException(status_code=409, detail={"error": {"message": "Shutdown is not available for this process"}})
        asyncio.get_running_loop().call_later(0.2, callback)
        return {"status": "shutting_down"}

    @router.websocket("/tasks/ws")
    async def tasks_ws(ws: WebSocket) -> None:
        cfg = ws.app.state.cfg
        try:
            _require_admin_auth(cfg, _auth_value_from_ws(ws))
        except HTTPException:
            await ws.close(code=1008)
            return

        jobs: JobManager = ws.app.state.jobs
        store: JobStore = ws.app.state.job_store
        await ws.accept()
        await jobs.subscribe_all(ws)
        try:
            snapshot = await store.list_tasks(limit=50)
            await ws.send_json({"type": "snapshot", "data": snapshot})
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await jobs.unsubscribe_all(ws)

    return router


def _cfg(request: Request) -> Config:
    return request.app.state.cfg


def _store(request: Request) -> JobStore:
    return request.app.state.job_store


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _auth_value_from_query_params(query_params: Mapping[str, Any]) -> str | None:
    for key in ("authorization", "api_key", "token", "access_token"):
        raw_value = query_params.get(key)
        raw = str(raw_value or "").strip()
        if not raw:
            continue
        if key == "authorization" or raw.lower().startswith("bearer "):
            return raw
        return f"Bearer {raw}"
    return None


def _auth_value_from_ws(ws: WebSocket) -> str | None:
    header_value = (ws.headers.get("authorization") or "").strip()
    if header_value:
        return header_value
    return _auth_value_from_query_params(ws.query_params)


def _require_admin_auth(cfg: Config, authorization: str | None) -> None:
    if not cfg.admin_token:
        return
    if not bearer_authorized(authorization or "", cfg.admin_token):
        raise HTTPException(status_code=401, detail={"error": {"message": "Unauthorized"}})


def _require_local_request(request: Request) -> None:
    host = (request.client.host if request.client else "") or ""
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        if host.lower() == "localhost":
            return
    raise HTTPException(status_code=403, detail={"error": {"message": "Shutdown is only available from localhost"}})


def _base_url(request: Request, cfg: Config) -> str:
    return (cfg.public_base_url or str(request.base_url)).rstrip("/")


def _abs_url(request: Request, cfg: Config, maybe_path: str) -> str:
    if not maybe_path:
        return ""
    if maybe_path.startswith("/"):
        return _base_url(request, cfg) + maybe_path
    return maybe_path


def _authorized_url(request: Request, cfg: Config, maybe_path: str) -> str:
    url = _abs_url(request, cfg, maybe_path)
    if not url or not cfg.api_token:
        return url
    secret = signing_secret(configured_secret=cfg.signed_url_secret, api_token=cfg.api_token)
    if not secret:
        return url
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    params = [(key, value) for key, value in params if key not in {"sig", "exp", "authorization", "api_key", "token", "access_token"}]
    params.extend(
        create_signed_query(
            path=parts.path,
            ttl_seconds=cfg.signed_url_ttl_seconds,
            secret=secret,
        ).items()
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


def _rewrite_task_payload_urls(request: Request, cfg: Config, payload: dict[str, Any]) -> dict[str, Any]:
    copied = {"task": dict(payload.get("task") or {}), "outputs": []}
    job_id = str(copied["task"].get("job_id") or "")
    outputs: list[dict[str, Any]] = []
    for raw in payload.get("outputs") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        filename = str(item.get("filename") or Path(str(item.get("url") or "")).name)
        if filename and job_id:
            item["url"] = _authorized_url(request, cfg, f"/runs/{job_id}/{filename}")
        outputs.append(item)
    copied["outputs"] = outputs
    raw_primary = str(copied["task"].get("url") or "")
    primary_name = Path(raw_primary).name if raw_primary else ""
    if primary_name and job_id:
        copied["task"]["url"] = _authorized_url(request, cfg, f"/runs/{job_id}/{primary_name}")
    return copied
