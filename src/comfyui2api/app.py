from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import re
import socket
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .comfy_client import ComfyUIClient
from .comfy_workflow import (
    extract_prompt_and_extra,
    find_load_image_targets,
    find_text_prompt_targets,
    pick_unique_load_image_target,
    pick_unique_target,
)
from .config import Config, load_config
from .job_retention import run_job_retention_forever
from .jobs import JobManager
from .signed_urls import create_signed_query, has_valid_signature, signing_secret
from .util import (
    bearer_authorized,
    decode_data_url_base64,
    guess_image_ext,
    guess_media_type,
    sanitize_filename_part,
    save_input_image,
    utc_now_unix,
)
from .workflow_params import (
    STANDARD_PARAMETER_ORDER,
    detect_parameter_candidates,
    generate_parameter_template,
    public_parameter_spec,
)
from .workflow_registry import WorkflowRegistry


def _openai_error(
    message: str,
    *,
    code: str = "invalid_request_error",
    http_status: int = 400,
    extra: Mapping[str, Any] | None = None,
) -> HTTPException:
    error = {"message": message, "type": code}
    if extra:
        error.update(extra)
    return HTTPException(
        status_code=http_status,
        detail={"error": error},
    )


def _require_auth(cfg: Config, authorization: str | None) -> None:
    if not cfg.api_token:
        return
    if not bearer_authorized(authorization or "", cfg.api_token):
        raise _openai_error("Unauthorized", code="invalid_api_key", http_status=401)


def _require_download_access(cfg: Config, request: Request, authorization: str | None) -> None:
    if not cfg.api_token:
        return
    secret = signing_secret(configured_secret=cfg.signed_url_secret, api_token=cfg.api_token)
    if has_valid_signature(path=request.url.path, query_params=request.query_params, secret=secret):
        return
    _require_auth(cfg, _auth_value_from_request(request, authorization))


def _uuid_now_hex() -> str:
    import uuid

    return uuid.uuid4().hex


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


def _auth_value_from_request(request: Request, authorization: str | None) -> str | None:
    header_value = (authorization or "").strip()
    if header_value:
        return header_value
    return _auth_value_from_query_params(request.query_params)


def _auth_value_from_ws(ws: WebSocket) -> str | None:
    header_value = (ws.headers.get("authorization") or "").strip()
    if header_value:
        return header_value
    return _auth_value_from_query_params(ws.query_params)


class _RequestBodyTooLargeError(RuntimeError):
    def __init__(self, size: int) -> None:
        super().__init__(size)
        self.size = size


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max(0, int(max_body_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.max_body_bytes <= 0:
            await self.app(scope, receive, send)
            return

        response_started = False
        seen = 0
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        content_length = headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > self.max_body_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "message": f"Request body too large ({declared} bytes)",
                            "type": "invalid_request_error",
                        }
                    },
                )
                await response(scope, receive, send)
                return

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] != "http.request":
                return message
            body = message.get("body", b"")
            seen += len(body)
            if seen > self.max_body_bytes:
                raise _RequestBodyTooLargeError(seen)
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLargeError as exc:
            if response_started:
                return
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "message": f"Request body too large ({exc.size} bytes)",
                        "type": "invalid_request_error",
                    }
                },
            )
            await response(scope, receive, send)


def _clean_optional_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return value


def _collect_standard_params(values: Mapping[str, Any], *, aliases: Mapping[str, str] | None = None) -> dict[str, Any]:
    alias_map = dict(aliases or {})
    params: dict[str, Any] = {}
    for key, raw_value in values.items():
        name = alias_map.get(key, key)
        if name not in STANDARD_PARAMETER_ORDER:
            continue
        value = _clean_optional_value(raw_value)
        if value is None:
            continue
        params[name] = value
    return params


async def _collect_workflow_request_params(
    *,
    spec: Any,
    values: Mapping[str, Any],
    store_input_image_bytes: Any,
    store_input_image_value: Any,
    skip: set[str] | None = None,
) -> dict[str, Any]:
    if spec is None or not getattr(spec, "parameters", None):
        return {}

    skipped = set(skip or ())
    params: dict[str, Any] = {}
    for name, definition in spec.parameters.items():
        if name in skipped or name not in values:
            continue
        raw_value = values.get(name)
        if definition.type == "image":
            if hasattr(raw_value, "read"):
                data = await raw_value.read()
                if not data:
                    continue
                filename_hint = getattr(raw_value, "filename", None) or name
                params[name] = await store_input_image_bytes(data=data, filename_hint=filename_hint)
                continue
            cleaned = _clean_optional_value(raw_value)
            if cleaned is None:
                continue
            params[name] = await store_input_image_value(str(cleaned), filename_hint=name)
            continue

        cleaned = _clean_optional_value(raw_value)
        if cleaned is None:
            continue
        params[name] = cleaned
    return params


def _extract_status_code(error_message: str) -> int | None:
    match = re.search(r"status=(\d{3})", error_message or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _workflow_supports_kind(wf: Any, kind: str) -> bool:
    caps = getattr(wf, "capabilities", None)
    if caps is None:
        return True
    if kind == "txt2img":
        return bool(getattr(caps, "has_save_image", False))
    if kind == "img2img":
        return bool(getattr(caps, "has_save_image", False) and getattr(caps, "has_load_image", False))
    if kind == "txt2video":
        return bool(getattr(caps, "has_save_video", False))
    if kind == "img2video":
        return bool(getattr(caps, "has_save_video", False) and getattr(caps, "has_load_image", False))
    return True


def _workflow_kind_error_message(*, wf: Any, kind: str) -> str:
    caps = getattr(wf, "capabilities", None)
    detected_kind = getattr(caps, "kind", "unknown") if caps is not None else "unknown"
    missing: list[str] = []
    if kind in {"img2img", "img2video"} and not getattr(caps, "has_load_image", False):
        missing.append("missing LoadImage")
    if kind in {"txt2img", "img2img"} and not getattr(caps, "has_save_image", False):
        missing.append("missing SaveImage")
    if kind in {"txt2video", "img2video"} and not getattr(caps, "has_save_video", False):
        missing.append("missing SaveVideo")

    detail = f"detected kind={detected_kind}"
    if missing:
        detail += f"; {', '.join(missing)}"
    return f"Workflow '{wf.name}' does not support {kind} ({detail})."


def create_app() -> FastAPI:
    cfg = load_config()
    registry = WorkflowRegistry(cfg.workflows_dir)
    comfy = ComfyUIClient(cfg.comfy_base_url, http_timeout_s=cfg.http_timeout_s)
    jobs = JobManager(cfg=cfg, registry=registry, comfy=comfy)

    app = FastAPI(title="comfyui2api", version="0.1.0")
    app.add_middleware(MaxBodySizeMiddleware, max_body_bytes=cfg.max_body_bytes)
    app.state.cfg = cfg
    app.state.registry = registry
    app.state.comfy = comfy
    app.state.jobs = jobs

    cfg.runs_dir.mkdir(parents=True, exist_ok=True)

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException):  # type: ignore[override]
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return await http_exception_handler(request, exc)

    @app.on_event("startup")
    async def _startup() -> None:
        if cfg.comfyui_startup_check:
            try:
                await comfy.system_stats()
            except Exception as exc:
                raise RuntimeError(
                    f"ComfyUI startup check failed for COMFYUI_BASE_URL={cfg.comfy_base_url}: {exc}"
                ) from exc
        await registry.load_all()
        if cfg.enable_workflow_watch:
            app.state.workflow_watch_task = asyncio.create_task(registry.watch_forever(), name="workflow-watch")
        if cfg.job_retention_seconds > 0 or cfg.max_jobs_in_memory > 0:
            app.state.job_retention_task = asyncio.create_task(
                run_job_retention_forever(
                    jobs,
                    interval_s=cfg.job_cleanup_interval_s,
                    ttl_seconds=cfg.job_retention_seconds,
                    max_jobs=cfg.max_jobs_in_memory,
                ),
                name="job-retention",
            )
        await jobs.start_workers()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        retention_task = getattr(app.state, "job_retention_task", None)
        if retention_task:
            retention_task.cancel()
            await asyncio.gather(retention_task, return_exceptions=True)
        t = getattr(app.state, "workflow_watch_task", None)
        if t:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
        await jobs.stop_workers()
        await comfy.aclose()

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"status": "ok"}

    @app.get("/runs/{job_id}/{output_name}")
    async def get_run_output(
        request: Request,
        job_id: str,
        output_name: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Any:
        _require_download_access(cfg, request, authorization)
        job = await jobs.get_job(job_id)
        if not job:
            raise _openai_error("Job not found", http_status=404)
        if job.status != "completed":
            raise _openai_error("Output not ready", http_status=409)

        pick = None
        for item in job.outputs or []:
            if item.filename == output_name:
                pick = item
                break
        if pick is None:
            raise _openai_error("Output not found", http_status=404)

        path = (cfg.runs_dir / job_id / pick.filename).resolve()
        if not path.exists():
            raise _openai_error("Output file missing", http_status=500)

        return FileResponse(path=str(path), media_type=pick.media_type or guess_media_type(pick.filename), filename=pick.filename)

    @app.get("/v1/workflows")
    async def list_workflows(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        items = []
        for wf in await registry.list():
            items.append(
                {
                    "name": wf.name,
                    "kind": wf.capabilities.kind,
                    "mtime_ns": wf.mtime_ns,
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
                    "mtime_ns": load_error.mtime_ns,
                    "available": False,
                    "load_error": load_error.error,
                    "parameter_error": None,
                }
            )
        items.sort(key=lambda item: str(item.get("name") or "").lower())
        return {"workflows_dir": str(cfg.workflows_dir), "items": items}

    async def _find_workflow_load_error(name: str):
        requested = (name or "").strip()
        if not requested:
            return None
        item = await registry.get_load_error(requested)
        if item:
            return item
        for load_error in await registry.list_load_errors():
            if load_error.name.lower() == requested.lower():
                return load_error
        return None

    async def _resolve_workflow_name(name: str):
        requested = (name or "").strip()
        if not requested:
            raise _openai_error("Missing workflow name", http_status=400)

        wf = await registry.get(requested)
        if wf:
            return wf
        for item in await registry.list():
            if item.name.lower() == requested.lower():
                return item

        if not requested.lower().endswith(".json"):
            maybe = requested + ".json"
            wf = await registry.get(maybe)
            if wf:
                return wf
            for item in await registry.list():
                if item.name.lower() == maybe.lower():
                    return item
            load_error = await _find_workflow_load_error(maybe)
            if load_error:
                raise _openai_error(
                    f"Workflow '{load_error.name}' failed to load: {load_error.error}",
                    http_status=400,
                )

        load_error = await _find_workflow_load_error(requested)
        if load_error:
            raise _openai_error(
                f"Workflow '{load_error.name}' failed to load: {load_error.error}",
                http_status=400,
            )

        raise _openai_error("Workflow not found", http_status=404)

    async def _resolve_workflow_for_kind(*, kind: str, requested_name: str) -> Any:
        resolved_name = (requested_name or "").strip() or await _pick_default_workflow(kind)
        wf = await _resolve_workflow_name(resolved_name)
        if not _workflow_supports_kind(wf, kind):
            raise _openai_error(_workflow_kind_error_message(wf=wf, kind=kind), http_status=400)
        return wf

    @app.get("/v1/workflows/{name}/targets")
    async def workflow_targets(name: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        wf = await _resolve_workflow_name(name)

        prompt, _extra_data = extract_prompt_and_extra(wf.workflow_obj)
        pos, neg = find_text_prompt_targets(prompt)
        img = find_load_image_targets(prompt)

        def _as_candidates(items: list[tuple[str, str, str, str]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for node_id, input_key, cls, title in items:
                out.append(
                    {
                        "ref": f"{node_id}.{input_key}",
                        "node_id": node_id,
                        "input_key": input_key,
                        "class_type": cls,
                        "title": title or None,
                    }
                )
            return out

        def _try_pick_text(kind: str, candidates: list[tuple[str, str, str, str]]) -> tuple[str | None, str | None]:
            try:
                node_id, input_key = pick_unique_target(kind=kind, candidates=candidates)
                return f"{node_id}.{input_key}", None
            except Exception as e:
                return None, str(e)

        def _try_pick_image(candidates: list[tuple[str, str, str, str]]) -> tuple[str | None, str | None]:
            try:
                node_id, input_key = pick_unique_load_image_target(candidates)
                return f"{node_id}.{input_key}", None
            except Exception as e:
                return None, str(e)

        pos_auto, pos_err = _try_pick_text("positive", pos)
        neg_auto, neg_err = _try_pick_text("negative", neg)
        img_auto, img_err = _try_pick_image(img)

        return {
            "workflow": {"name": wf.name, "kind": wf.capabilities.kind, "mtime_ns": wf.mtime_ns},
            "targets": {
                "positive_prompt": {"autodetect": pos_auto, "autodetect_error": pos_err, "candidates": _as_candidates(pos)},
                "negative_prompt": {"autodetect": neg_auto, "autodetect_error": neg_err, "candidates": _as_candidates(neg)},
                "image": {"autodetect": img_auto, "autodetect_error": img_err, "candidates": _as_candidates(img)},
            },
        }

    @app.get("/v1/workflows/{name}/parameters")
    async def workflow_parameters(name: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        wf = await _resolve_workflow_name(name)
        suggested_template = generate_parameter_template(
            workflow_obj=wf.workflow_obj,
            kind=wf.capabilities.kind,
            spec=wf.parameter_spec,
        )
        return {
            "workflow": {"name": wf.name, "kind": wf.capabilities.kind, "mtime_ns": wf.mtime_ns},
            "parameter_mapping": public_parameter_spec(wf.parameter_spec),
            "detected_candidates": detect_parameter_candidates(wf.workflow_obj),
            "suggested_template": suggested_template,
            "parameter_error": wf.parameter_error,
        }

    @app.get("/v1/workflows/{name}/parameters/template")
    async def workflow_parameters_template(name: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        wf = await _resolve_workflow_name(name)
        return {
            "workflow": {"name": wf.name, "kind": wf.capabilities.kind, "mtime_ns": wf.mtime_ns},
            "template": generate_parameter_template(
                workflow_obj=wf.workflow_obj,
                kind=wf.capabilities.kind,
                spec=wf.parameter_spec,
            ),
            "parameter_error": wf.parameter_error,
        }

    def _build_upload_filename(*, job_id: str, data: bytes, filename_hint: str | None) -> str:
        ext = ""
        stem = "image"
        if filename_hint:
            p = Path(filename_hint)
            ext = p.suffix
            stem = p.stem or stem

        ext = (ext or guess_image_ext(data)).lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
            ext = guess_image_ext(data)
        if ext == ".jpeg":
            ext = ".jpg"

        safe_stem = sanitize_filename_part(stem, max_len=60)
        safe_prefix = sanitize_filename_part(job_id[:12], max_len=12)
        return f"{safe_prefix}--{safe_stem}{ext}"

    async def _store_input_image_bytes(*, data: bytes, filename_hint: str | None) -> str:
        mode = (cfg.image_upload_mode or "auto").strip().lower()
        if mode not in {"auto", "comfy", "local"}:
            mode = "auto"

        if len(data) > max(1, int(cfg.max_image_bytes)):
            raise _openai_error(f"Image too large ({len(data)} bytes)", http_status=413)

        if mode in {"auto", "comfy"}:
            try:
                upload_name = _build_upload_filename(job_id=_uuid_now_hex(), data=data, filename_hint=filename_hint)
                return await comfy.upload_image_bytes(
                    data=data,
                    filename=upload_name,
                    subfolder=cfg.input_subdir,
                    folder_type="input",
                    overwrite=True,
                )
            except Exception:
                if mode == "comfy":
                    raise

        return save_input_image(
            input_dir=cfg.comfyui_input_dir,
            subdir=cfg.input_subdir,
            job_id=_uuid_now_hex(),
            data=data,
            filename_hint=filename_hint,
            max_bytes=cfg.max_image_bytes,
        )

    def _is_global_public_ip(host: str) -> bool:
        try:
            info = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except Exception:
            return False
        if not info:
            return False
        for item in info:
            ip_str = item[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return False
            if not ip.is_global:
                return False
        return True

    async def _download_image_url(url: str) -> bytes:
        u = httpx.URL(url)
        if u.scheme not in {"http", "https"}:
            raise _openai_error("Only http/https image URLs are supported", http_status=400)
        if not u.host:
            raise _openai_error("Invalid image URL", http_status=400)
        if not _is_global_public_ip(u.host):
            raise _openai_error("Blocked image URL host", http_status=400)

        timeout = httpx.Timeout(timeout=cfg.http_timeout_s)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            current = u
            for _ in range(3):
                async with client.stream("GET", current, headers={"Accept": "image/*,*/*"}) as resp:
                    if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                        nxt = httpx.URL(resp.headers["location"])
                        current = nxt if nxt.scheme else current.join(nxt)
                        if current.scheme not in {"http", "https"} or not current.host or not _is_global_public_ip(current.host):
                            raise _openai_error("Blocked image URL redirect", http_status=400)
                        continue

                    try:
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        raise _openai_error(f"Failed to download image: HTTP {e.response.status_code}", http_status=400) from e

                    content_type = (resp.headers.get("content-type") or "").lower()
                    if content_type and not content_type.startswith("image/"):
                        raise _openai_error("URL did not return an image", http_status=400)

                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max(1, int(cfg.max_image_bytes)):
                            raise _openai_error("Image too large", http_status=413)
                    return bytes(buf)

            raise _openai_error("Too many redirects downloading image", http_status=400)

    async def _store_input_image_value(image_value: str, *, filename_hint: str | None = None) -> str:
        s = (image_value or "").strip()
        if not s:
            raise _openai_error("Missing 'image'", http_status=400)
        if s.startswith("http://") or s.startswith("https://"):
            data = await _download_image_url(s)
            return await _store_input_image_bytes(data=data, filename_hint=filename_hint or "image")
        data = decode_data_url_base64(s)
        return await _store_input_image_bytes(data=data, filename_hint=filename_hint)

    def _base_url(request: Request) -> str:
        return (cfg.public_base_url or str(request.base_url)).rstrip("/")

    def _abs_url(request: Request, maybe_path: str) -> str:
        if not maybe_path:
            return ""
        if maybe_path.startswith("/"):
            return _base_url(request) + maybe_path
        return maybe_path

    def _authorized_url_parts(request: Request, maybe_path: str, authorization: str | None) -> tuple[str, int | None]:
        url = _abs_url(request, maybe_path)
        if not url or not cfg.api_token:
            return url, None

        secret = signing_secret(configured_secret=cfg.signed_url_secret, api_token=cfg.api_token)
        if not secret:
            return url, None

        parts = urlsplit(url)
        params = parse_qsl(parts.query, keep_blank_values=True)
        params = [(key, value) for key, value in params if key not in {"sig", "exp", "authorization", "api_key", "token", "access_token"}]
        signed_query = create_signed_query(
            path=parts.path,
            ttl_seconds=cfg.signed_url_ttl_seconds,
            secret=secret,
        )
        params.extend(signed_query.items())
        signed_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
        return signed_url, int(signed_query["exp"])

    def _authorized_url(request: Request, maybe_path: str, authorization: str | None) -> str:
        url, _expires_at = _authorized_url_parts(request, maybe_path, authorization)
        return url

    def _output_relpath(job_id: str, filename: str) -> str:
        return f"/runs/{job_id}/{filename}"

    def _authorized_output_url(request: Request, *, job_id: str, output: Mapping[str, Any], authorization: str | None) -> str:
        filename = str(output.get("filename") or "").strip()
        if not filename:
            raw_url = str(output.get("url") or "").strip()
            filename = Path(raw_url).name if raw_url else ""
        if not filename:
            return ""
        return _authorized_url(request, _output_relpath(job_id, filename), authorization)

    def _rewrite_public_job_urls(request: Request, public: Dict[str, Any], authorization: str | None) -> Dict[str, Any]:
        rewritten = dict(public)
        outputs = []
        raw_outputs = public.get("outputs") or []
        for item in raw_outputs if isinstance(raw_outputs, list) else []:
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            copied["url"] = _authorized_output_url(
                request,
                job_id=str(public.get("job_id") or ""),
                output=copied,
                authorization=authorization,
            )
            outputs.append(copied)
        rewritten["outputs"] = outputs

        raw_primary = str(public.get("url") or "").strip()
        primary_name = Path(raw_primary).name if raw_primary else ""
        if primary_name:
            rewritten["url"] = _authorized_url(request, _output_relpath(str(public.get("job_id") or ""), primary_name), authorization)
        elif outputs:
            rewritten["url"] = outputs[0].get("url")
        else:
            rewritten["url"] = None
        return rewritten

    def _response_output_urls(
        request: Request,
        *,
        job_id: str,
        outputs: list[dict[str, Any]],
        authorization: str | None,
    ) -> list[str]:
        urls: list[str] = []
        for item in outputs:
            url = _authorized_output_url(request, job_id=job_id, output=item, authorization=authorization)
            if url:
                urls.append(url)
        return urls

    def _first_output_filename(outputs: list[dict[str, Any]]) -> str:
        for item in outputs:
            filename = str(item.get("filename") or "").strip()
            if filename:
                return filename
            raw_url = str(item.get("url") or "").strip()
            if raw_url:
                return Path(raw_url).name
        return ""

    def _normalize_image_response_format(value: Any) -> str:
        raw = str(value or "url").strip().lower()
        if not raw:
            return "url"
        if raw in {"b64_json", "b64", "base64", "base64_json"}:
            return "b64_json"
        return "url"

    def _extract_chat_message_content(content: Any) -> tuple[list[str], str | None]:
        texts: list[str] = []
        image_value: str | None = None
        if isinstance(content, str):
            cleaned = content.strip()
            if cleaned:
                texts.append(cleaned)
            return texts, None
        if not isinstance(content, list):
            return texts, None

        for part in content:
            if isinstance(part, str):
                cleaned = part.strip()
                if cleaned:
                    texts.append(cleaned)
                continue
            if not isinstance(part, dict):
                continue

            part_type = str(part.get("type") or "").strip().lower()
            if part_type in {"text", "input_text"}:
                raw_text = part.get("text")
                if raw_text is None and part_type == "input_text":
                    raw_text = part.get("input_text")
                cleaned = str(raw_text or "").strip()
                if cleaned:
                    texts.append(cleaned)
                continue

            if image_value is not None:
                continue
            if part_type not in {"image_url", "input_image", "image"}:
                continue

            raw_image: Any = part.get("image_url")
            if raw_image is None:
                raw_image = part.get("input_image")
            if raw_image is None:
                raw_image = part.get("url")
            if raw_image is None:
                raw_image = part.get("image")
            if isinstance(raw_image, dict):
                raw_image = raw_image.get("url") or raw_image.get("image_url")
            cleaned_image = str(raw_image or "").strip()
            if cleaned_image:
                image_value = cleaned_image

        return texts, image_value

    def _extract_chat_prompt_and_image(messages: Any) -> tuple[str, str | None]:
        prompt = ""
        image_value: str | None = None
        if not isinstance(messages, list):
            return prompt, image_value

        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role != "user":
                continue
            texts, maybe_image = _extract_chat_message_content(item.get("content"))
            if not prompt and texts:
                prompt = "\n\n".join(texts).strip()
            if image_value is None and maybe_image:
                image_value = maybe_image
            if prompt and image_value is not None:
                break

        return prompt, image_value

    def _pick_chat_generation_kind(*, wf: Any, has_image: bool) -> str:
        detected_kind = str(getattr(getattr(wf, "capabilities", None), "kind", "") or "").strip().lower()
        if has_image:
            if detected_kind == "img2video" and _workflow_supports_kind(wf, "img2video"):
                return "img2video"
            if detected_kind == "img2img" and _workflow_supports_kind(wf, "img2img"):
                return "img2img"
            if _workflow_supports_kind(wf, "img2video") and not _workflow_supports_kind(wf, "img2img"):
                return "img2video"
            if _workflow_supports_kind(wf, "img2img") and not _workflow_supports_kind(wf, "img2video"):
                return "img2img"
            if _workflow_supports_kind(wf, "img2video"):
                return "img2video"
            if _workflow_supports_kind(wf, "img2img"):
                return "img2img"
            raise _openai_error(_workflow_kind_error_message(wf=wf, kind="img2video"), http_status=400)

        if detected_kind == "txt2video" and _workflow_supports_kind(wf, "txt2video"):
            return "txt2video"
        if detected_kind == "txt2img" and _workflow_supports_kind(wf, "txt2img"):
            return "txt2img"
        if _workflow_supports_kind(wf, "txt2video") and not _workflow_supports_kind(wf, "txt2img"):
            return "txt2video"
        if _workflow_supports_kind(wf, "txt2img") and not _workflow_supports_kind(wf, "txt2video"):
            return "txt2img"
        if _workflow_supports_kind(wf, "txt2video"):
            return "txt2video"
        if _workflow_supports_kind(wf, "txt2img"):
            return "txt2img"
        raise _openai_error(_workflow_kind_error_message(wf=wf, kind="txt2img"), http_status=400)

    def _chat_completion_response(*, model: str, content_payload: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "id": f"chatcmpl_{_uuid_now_hex()}",
            "object": "chat.completion",
            "created": utc_now_unix(),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(dict(content_payload), ensure_ascii=False),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @app.get("/v1/models")
    async def openai_models(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        data = []
        for wf in await registry.list():
            data.append(
                {
                    "id": Path(wf.name).stem,
                    "object": "model",
                    "created": 0,
                    "owned_by": "comfyui",
                    "metadata": {"kind": wf.capabilities.kind},
                }
            )
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(
        request: Request,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_comfyui_async: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        if body.get("stream") is True:
            raise _openai_error("Streaming is not supported for /v1/chat/completions", http_status=400)

        requested_model = str(body.get("model") or "").strip()
        if not requested_model:
            raise _openai_error("Missing 'model'")

        wf = await _resolve_workflow_name(requested_model)
        prompt = str(body.get("prompt") or "").strip()
        image_value = _clean_optional_value(body.get("image"))
        if image_value is not None:
            image_value = str(image_value).strip()
        if not prompt or not image_value:
            message_prompt, message_image = _extract_chat_prompt_and_image(body.get("messages"))
            if not prompt:
                prompt = message_prompt
            if not image_value and message_image:
                image_value = message_image
        if not prompt:
            raise _openai_error("Missing prompt text in 'messages'")

        kind = _pick_chat_generation_kind(wf=wf, has_image=bool(image_value))
        negative_prompt = str(body.get("negative_prompt") or "").strip()
        standard_params = _collect_standard_params({key: body.get(key) for key in STANDARD_PARAMETER_ORDER if key in body})
        seconds_value = _clean_optional_value(body.get("seconds"))
        if seconds_value is not None and "duration" not in standard_params:
            standard_params["duration"] = seconds_value

        image_rel = ""
        if image_value:
            image_rel = await _store_input_image_value(str(image_value), filename_hint="chat_input")

        job = await jobs.create_job(
            kind=kind,
            workflow=wf.name,
            requested_model=requested_model,
            seconds=str(seconds_value or ""),
            size=str(body.get("size") or "").strip(),
            quality=str(body.get("quality") or "").strip() or "standard",
            metadata="",
            prompt=prompt,
            negative_prompt=negative_prompt,
            image=image_rel,
            standard_params=standard_params,
        )

        public_model = requested_model or Path(wf.name).stem
        if x_comfyui_async and str(x_comfyui_async).strip() not in {"0", "false", "False"}:
            payload: dict[str, Any] = {
                "type": "generation_job",
                "kind": kind,
                "job_id": job.job_id,
                "status": "pending",
            }
            if kind.endswith("video"):
                payload["video_id"] = _video_id(job.job_id)
            return _chat_completion_response(model=public_model, content_payload=payload)

        done = await _openai_wait(job.job_id)
        outputs = [o for o in (done.get("outputs") or []) if isinstance(o, dict)]
        response_format = _normalize_image_response_format(body.get("response_format"))

        payload = {
            "type": "generation_result",
            "kind": kind,
            "job_id": str(done.get("job_id") or job.job_id),
            "status": "completed",
        }
        if kind.endswith("video"):
            payload["video_id"] = _video_id(str(done.get("job_id") or job.job_id))

        if response_format == "b64_json" and kind in {"txt2img", "img2img"}:
            filename = _first_output_filename(outputs)
            if not filename:
                raise _openai_error("No outputs produced", http_status=500)
            p = Path(cfg.runs_dir) / job.job_id / filename
            payload["data"] = [{"b64_json": base64.b64encode(p.read_bytes()).decode("ascii")}]
        else:
            urls = _response_output_urls(
                request,
                job_id=str(done.get("job_id") or job.job_id),
                outputs=outputs,
                authorization=authorization,
            )
            payload["data"] = [{"url": u} for u in urls]

        return _chat_completion_response(model=public_model, content_payload=payload)

    async def _pick_default_workflow(kind: str) -> str:
        if kind == "txt2img":
            return cfg.default_txt2img_workflow
        if kind == "img2img":
            return cfg.default_img2img_workflow
        if kind == "txt2video":
            if not cfg.default_txt2video_workflow:
                raise _openai_error("DEFAULT_TXT2VIDEO_WORKFLOW is not set", http_status=400)
            return cfg.default_txt2video_workflow
        if kind == "img2video":
            return cfg.default_img2video_workflow
        raise _openai_error(f"Unsupported kind: {kind}", http_status=400)

    @app.post("/v1/jobs")
    async def submit_job(
        request: Request,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_auth(cfg, authorization)

        kind = str(body.get("kind") or "").strip()
        requested_workflow = str(body.get("workflow") or "").strip()
        wf = await _resolve_workflow_for_kind(kind=kind, requested_name=requested_workflow)
        workflow = wf.name
        prompt = str(body.get("prompt") or "").strip()
        negative_prompt = str(body.get("negative_prompt") or "").strip()

        prompt_node = str(body.get("prompt_node") or "").strip()
        negative_prompt_node = str(body.get("negative_prompt_node") or "").strip()
        image_node = str(body.get("image_node") or "").strip()

        image_rel = ""
        if body.get("image"):
            image_rel = str(body.get("image") or "").strip()
        elif body.get("image_base64"):
            img_bytes = decode_data_url_base64(str(body.get("image_base64") or ""))
            filename_hint = str(body.get("image_filename") or "") or None
            image_rel = await _store_input_image_bytes(data=img_bytes, filename_hint=filename_hint)

        overrides: list[tuple[str, str, Any]] = []
        raw_overrides = body.get("overrides")
        if isinstance(raw_overrides, dict):
            for k, v in raw_overrides.items():
                if not isinstance(k, str) or "." not in k:
                    continue
                node_id, input_key = k.split(".", 1)
                overrides.append((node_id.strip(), input_key.strip(), v))

        standard_params = _collect_standard_params(
            {key: body.get(key) for key in STANDARD_PARAMETER_ORDER if key in body},
            aliases={"seconds": "duration"},
        )
        seconds_value = _clean_optional_value(body.get("seconds"))
        if seconds_value is not None and "duration" not in standard_params:
            standard_params["duration"] = seconds_value
        standard_params.update(
            await _collect_workflow_request_params(
                spec=wf.parameter_spec,
                values=body,
                store_input_image_bytes=_store_input_image_bytes,
                store_input_image_value=_store_input_image_value,
                skip={
                    *STANDARD_PARAMETER_ORDER,
                    "kind",
                    "workflow",
                    "model",
                    "prompt",
                    "negative_prompt",
                    "image",
                    "image_base64",
                    "image_filename",
                    "prompt_node",
                    "negative_prompt_node",
                    "image_node",
                    "overrides",
                    "seconds",
                },
            )
        )

        job = await jobs.create_job(
            kind=kind,
            workflow=workflow,
            prompt=prompt,
            negative_prompt=negative_prompt,
            image=image_rel,
            prompt_node=prompt_node,
            negative_prompt_node=negative_prompt_node,
            image_node=image_node,
            overrides=overrides,
            standard_params=standard_params,
        )

        base = _base_url(request)
        job_url = f"{base}/v1/jobs/{job.job_id}"
        ws_url = f"{base.replace('http://', 'ws://').replace('https://', 'wss://')}/v1/jobs/{job.job_id}/ws"
        return {"job": jobs.public_job(job), "job_url": job_url, "ws_url": ws_url}

    @app.get("/v1/jobs/{job_id}")
    async def get_job(request: Request, job_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        job = await jobs.get_job(job_id)
        if not job:
            raise _openai_error("Job not found", http_status=404)
        public = _rewrite_public_job_urls(request, jobs.public_job(job), authorization)
        return {"job": public}

    @app.get("/v1/queue")
    async def get_queue(request: Request, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        items = await jobs.list_jobs(limit=200)
        counts: Dict[str, int] = {}
        for j in items:
            counts[j.status] = counts.get(j.status, 0) + 1
        result_items = []
        for j in items:
            result_items.append(_rewrite_public_job_urls(request, jobs.public_job(j), authorization))
        return {"counts": counts, "items": result_items}

    @app.websocket("/v1/jobs/{job_id}/ws")
    async def job_ws(ws: WebSocket, job_id: str) -> None:
        try:
            _require_auth(cfg, _auth_value_from_ws(ws))
        except HTTPException:
            await ws.close(code=1008)
            return
        await ws.accept()
        job = await jobs.get_job(job_id)
        if not job:
            await ws.send_json({"type": "error", "data": {"message": "Job not found"}})
            await ws.close(code=1008)
            return

        await jobs.subscribe(job_id, ws)
        try:
            await ws.send_json({"type": "job_snapshot", "data": jobs.public_job(job)})
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await jobs.unsubscribe(job_id, ws)

    async def _openai_wait(job_id: str) -> Dict[str, Any]:
        job = await jobs.get_job(job_id)
        if not job:
            raise _openai_error("Job not found", http_status=404)
        await job.done.wait()
        job = await jobs.get_job(job_id)
        if not job:
            raise _openai_error("Job not found", http_status=404)
        if job.status != "completed":
            error_message = job.error or "Job failed"
            extra = {"job_id": job_id}
            status_code = _extract_status_code(error_message)
            if "ComfyApiError: ComfyUI " in error_message:
                extra["upstream"] = "comfyui"
                extra["comfyui_base_url"] = cfg.comfy_base_url
                if status_code is not None and status_code >= 500:
                    error_message = (
                        f"ComfyUI upstream unavailable. COMFYUI_BASE_URL={cfg.comfy_base_url}. {error_message}"
                    )
                else:
                    error_message = (
                        f"ComfyUI upstream request failed. COMFYUI_BASE_URL={cfg.comfy_base_url}. {error_message}"
                    )
            raise _openai_error(
                error_message,
                code="server_error",
                http_status=500,
                extra=extra,
            )
        return jobs.public_job(job)

    @app.post("/v1/images/generations")
    async def openai_images_generations(
        request: Request,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_comfyui_async: Optional[str] = Header(default=None),
    ) -> Any:
        _require_auth(cfg, authorization)
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise _openai_error("Missing 'prompt'")
        wf = await _resolve_workflow_for_kind(
            kind="txt2img",
            requested_name=str(body.get("workflow") or body.get("model") or "").strip(),
        )
        workflow = wf.name
        negative_prompt = str(body.get("negative_prompt") or "").strip()
        response_format = _normalize_image_response_format(body.get("response_format"))
        standard_params = _collect_standard_params({key: body.get(key) for key in STANDARD_PARAMETER_ORDER if key in body})

        job = await jobs.create_job(
            kind="txt2img",
            workflow=workflow,
            prompt=prompt,
            negative_prompt=negative_prompt,
            standard_params=standard_params,
        )
        if x_comfyui_async and str(x_comfyui_async).strip() not in {"0", "false", "False"}:
            return {"job_id": job.job_id, "status": "pending"}

        done = await _openai_wait(job.job_id)
        outputs = [o for o in (done.get("outputs") or []) if isinstance(o, dict)]
        urls = _response_output_urls(
            request,
            job_id=str(done.get("job_id") or job.job_id),
            outputs=outputs,
            authorization=authorization,
        )
        if response_format == "b64_json":
            filename = _first_output_filename(outputs)
            if not filename:
                raise _openai_error("No outputs produced", http_status=500)
            p = Path(cfg.runs_dir) / job.job_id / filename
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            return {"created": utc_now_unix(), "data": [{"b64_json": data}]}
        return {"created": utc_now_unix(), "data": [{"url": u} for u in urls]}

    @app.post("/v1/images/edits")
    async def openai_images_edits(
        request: Request,
        image: UploadFile = File(...),
        prompt: str = Form(""),
        model: str = Form(""),
        workflow: str = Form(""),
        negative_prompt: str = Form(""),
        response_format: str = Form("url"),
        size: str = Form(""),
        width: str = Form(""),
        height: str = Form(""),
        steps: str = Form(""),
        cfg_scale: str = Form("", alias="cfg"),
        seed: str = Form(""),
        authorization: Optional[str] = Header(default=None),
        x_comfyui_async: Optional[str] = Header(default=None),
    ) -> Any:
        _require_auth(cfg, authorization)
        wf = await _resolve_workflow_for_kind(kind="img2img", requested_name=(workflow or model or "").strip())
        raw = await image.read()
        image_rel = await _store_input_image_bytes(data=raw, filename_hint=image.filename)

        standard_params = _collect_standard_params(
            {
                "size": size,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg_scale,
                "seed": seed,
            }
        )
        job = await jobs.create_job(
            kind="img2img",
            workflow=wf.name,
            prompt=(prompt or "").strip(),
            negative_prompt=(negative_prompt or "").strip(),
            image=image_rel,
            standard_params=standard_params,
        )
        if x_comfyui_async and str(x_comfyui_async).strip() not in {"0", "false", "False"}:
            return {"job_id": job.job_id, "status": "pending"}

        done = await _openai_wait(job.job_id)
        outputs = [o for o in (done.get("outputs") or []) if isinstance(o, dict)]
        urls = _response_output_urls(
            request,
            job_id=str(done.get("job_id") or job.job_id),
            outputs=outputs,
            authorization=authorization,
        )
        if _normalize_image_response_format(response_format) == "b64_json":
            filename = _first_output_filename(outputs)
            if not filename:
                raise _openai_error("No outputs produced", http_status=500)
            p = Path(cfg.runs_dir) / job.job_id / filename
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            return {"created": utc_now_unix(), "data": [{"b64_json": data}]}
        return {"created": utc_now_unix(), "data": [{"url": u} for u in urls]}

    @app.post("/v1/images/variations")
    async def openai_images_variations(
        request: Request,
        image: UploadFile = File(...),
        model: str = Form(""),
        workflow: str = Form(""),
        response_format: str = Form("url"),
        size: str = Form(""),
        width: str = Form(""),
        height: str = Form(""),
        steps: str = Form(""),
        cfg_scale: str = Form("", alias="cfg"),
        seed: str = Form(""),
        authorization: Optional[str] = Header(default=None),
        x_comfyui_async: Optional[str] = Header(default=None),
    ) -> Any:
        _require_auth(cfg, authorization)
        wf = await _resolve_workflow_for_kind(kind="img2img", requested_name=(workflow or model or "").strip())
        raw = await image.read()
        image_rel = await _store_input_image_bytes(data=raw, filename_hint=image.filename)

        standard_params = _collect_standard_params(
            {
                "size": size,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg_scale,
                "seed": seed,
            }
        )
        job = await jobs.create_job(
            kind="img2img",
            workflow=wf.name,
            prompt="",
            image=image_rel,
            standard_params=standard_params,
        )
        if x_comfyui_async and str(x_comfyui_async).strip() not in {"0", "false", "False"}:
            return {"job_id": job.job_id, "status": "pending"}

        done = await _openai_wait(job.job_id)
        outputs = [o for o in (done.get("outputs") or []) if isinstance(o, dict)]
        urls = _response_output_urls(
            request,
            job_id=str(done.get("job_id") or job.job_id),
            outputs=outputs,
            authorization=authorization,
        )
        if _normalize_image_response_format(response_format) == "b64_json":
            filename = _first_output_filename(outputs)
            if not filename:
                raise _openai_error("No outputs produced", http_status=500)
            p = Path(cfg.runs_dir) / job.job_id / filename
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            return {"created": utc_now_unix(), "data": [{"b64_json": data}]}
        return {"created": utc_now_unix(), "data": [{"url": u} for u in urls]}

    @app.post("/v1/videos/generations")
    async def openai_videos_generations(
        request: Request,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_comfyui_async: Optional[str] = Header(default=None),
    ) -> Any:
        _require_auth(cfg, authorization)
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise _openai_error("Missing 'prompt'")
        wf = await _resolve_workflow_for_kind(
            kind="txt2video",
            requested_name=str(body.get("workflow") or body.get("model") or "").strip(),
        )
        workflow = wf.name

        negative_prompt = str(body.get("negative_prompt") or "").strip()
        standard_params = _collect_standard_params({key: body.get(key) for key in STANDARD_PARAMETER_ORDER if key in body})
        seconds_value = _clean_optional_value(body.get("seconds"))
        if seconds_value is not None and "duration" not in standard_params:
            standard_params["duration"] = seconds_value
        job = await jobs.create_job(
            kind="txt2video",
            workflow=workflow,
            prompt=prompt,
            negative_prompt=negative_prompt,
            standard_params=standard_params,
        )
        if x_comfyui_async and str(x_comfyui_async).strip() not in {"0", "false", "False"}:
            return {"job_id": job.job_id, "status": "pending"}

        done = await _openai_wait(job.job_id)
        outputs = [o for o in (done.get("outputs") or []) if isinstance(o, dict)]
        urls = _response_output_urls(
            request,
            job_id=str(done.get("job_id") or job.job_id),
            outputs=outputs,
            authorization=authorization,
        )
        return {"created": utc_now_unix(), "data": [{"url": u} for u in urls]}

    @app.post("/v1/videos/edits")
    async def openai_videos_edits(
        request: Request,
        image: UploadFile = File(...),
        prompt: str = Form(""),
        model: str = Form(""),
        workflow: str = Form(""),
        negative_prompt: str = Form(""),
        size: str = Form(""),
        fps: str = Form(""),
        duration: str = Form(""),
        frames: str = Form(""),
        width: str = Form(""),
        height: str = Form(""),
        steps: str = Form(""),
        cfg_scale: str = Form("", alias="cfg"),
        seed: str = Form(""),
        authorization: Optional[str] = Header(default=None),
        x_comfyui_async: Optional[str] = Header(default=None),
    ) -> Any:
        _require_auth(cfg, authorization)
        wf = await _resolve_workflow_for_kind(kind="img2video", requested_name=(workflow or model or "").strip())
        raw = await image.read()
        image_rel = await _store_input_image_bytes(data=raw, filename_hint=image.filename)

        standard_params = _collect_standard_params(
            {
                "size": size,
                "fps": fps,
                "duration": duration,
                "frames": frames,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg_scale,
                "seed": seed,
            }
        )
        job = await jobs.create_job(
            kind="img2video",
            workflow=wf.name,
            prompt=(prompt or "").strip(),
            negative_prompt=(negative_prompt or "").strip(),
            image=image_rel,
            standard_params=standard_params,
        )
        if x_comfyui_async and str(x_comfyui_async).strip() not in {"0", "false", "False"}:
            return {"job_id": job.job_id, "status": "pending"}

        done = await _openai_wait(job.job_id)
        outputs = [o for o in (done.get("outputs") or []) if isinstance(o, dict)]
        urls = _response_output_urls(
            request,
            job_id=str(done.get("job_id") or job.job_id),
            outputs=outputs,
            authorization=authorization,
        )
        return {"created": utc_now_unix(), "data": [{"url": u} for u in urls]}

    def _as_job_id_from_video_id(video_id: str) -> str:
        raw = (video_id or "").strip()
        if raw.startswith("video_"):
            return raw[len("video_") :]
        return raw

    def _video_id(job_id: str) -> str:
        return f"video_{job_id}"

    def _progress_percent(job_progress: Dict[str, Any], *, completed: bool) -> int:
        if completed:
            return 100
        if not isinstance(job_progress, dict):
            return 0
        try:
            value = float(job_progress.get("value") or 0)
            total = float(job_progress.get("max") or 0)
        except Exception:
            return 0
        if total <= 0:
            return 0
        pct = int((value / total) * 100.0)
        return max(0, min(99, pct))

    async def _workflow_from_model_or_default(*, kind: str, model: str) -> tuple[str, str]:
        requested = (model or "").strip()
        wf = await _resolve_workflow_for_kind(kind=kind, requested_name=requested)
        return wf.name, requested or wf.name

    async def _parse_videos_create_payload(request: Request) -> Dict[str, Any]:
        content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            try:
                body = await request.json()
            except Exception as e:
                raise _openai_error("Invalid JSON body", http_status=400) from e
            if not isinstance(body, dict):
                raise _openai_error("JSON body must be an object", http_status=400)

            raw_metadata = body.get("metadata")
            if isinstance(raw_metadata, str):
                metadata = raw_metadata.strip()
            elif raw_metadata is None:
                metadata = ""
            else:
                metadata = json.dumps(raw_metadata, ensure_ascii=False)

            raw_input_reference = _clean_optional_value(body.get("input_reference"))
            if raw_input_reference is None:
                raw_input_reference = _clean_optional_value(body.get("image"))

            return {
                "prompt": str(body.get("prompt") or "").strip(),
                "model": str(body.get("model") or body.get("workflow") or "").strip(),
                "seconds": str(
                    _clean_optional_value(body.get("seconds"))
                    or _clean_optional_value(body.get("duration"))
                    or ""
                ).strip(),
                "size": str(body.get("size") or "").strip(),
                "fps": str(body.get("fps") or "").strip(),
                "frames": str(body.get("frames") or "").strip(),
                "width": str(body.get("width") or "").strip(),
                "height": str(body.get("height") or "").strip(),
                "quality": str(body.get("quality") or "").strip(),
                "metadata": metadata,
                "input_reference_upload": None,
                "input_reference_value": str(raw_input_reference or "").strip(),
                "raw_values": body,
            }

        form = await request.form()
        raw_input_reference = form.get("input_reference")
        input_reference_upload = raw_input_reference if hasattr(raw_input_reference, "read") else None
        input_reference_value = ""
        if raw_input_reference is not None and input_reference_upload is None:
            input_reference_value = str(raw_input_reference or "").strip()

        return {
            "prompt": str(form.get("prompt") or "").strip(),
            "model": str(form.get("model") or form.get("workflow") or "").strip(),
            "seconds": str(form.get("seconds") or form.get("duration") or "").strip(),
            "size": str(form.get("size") or "").strip(),
            "fps": str(form.get("fps") or "").strip(),
            "frames": str(form.get("frames") or "").strip(),
            "width": str(form.get("width") or "").strip(),
            "height": str(form.get("height") or "").strip(),
            "quality": str(form.get("quality") or "").strip(),
            "metadata": str(form.get("metadata") or "").strip(),
            "input_reference_upload": input_reference_upload,
            "input_reference_value": input_reference_value,
            "raw_values": form,
        }

    @app.post("/v1/videos", status_code=200)
    async def openai_videos_create(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_auth(cfg, authorization)

        payload = await _parse_videos_create_payload(request)
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise _openai_error("Missing 'prompt'")

        kind = "txt2video"
        image_rel = ""
        input_reference = payload.get("input_reference_upload")
        input_reference_value = str(payload.get("input_reference_value") or "").strip()
        if input_reference is not None:
            raw = await input_reference.read()
            image_rel = await _store_input_image_bytes(data=raw, filename_hint=input_reference.filename)
            kind = "img2video"
        elif input_reference_value:
            image_rel = await _store_input_image_value(input_reference_value, filename_hint="input_reference")
            kind = "img2video"

        workflow, requested_model = await _workflow_from_model_or_default(kind=kind, model=str(payload.get("model") or ""))
        wf = await _resolve_workflow_name(workflow)
        standard_params = _collect_standard_params(
            {
                "duration": payload.get("seconds"),
                "size": payload.get("size"),
                "fps": payload.get("fps"),
                "frames": payload.get("frames"),
                "width": payload.get("width"),
                "height": payload.get("height"),
            }
        )
        standard_params.update(
            await _collect_workflow_request_params(
                spec=wf.parameter_spec,
                values=payload.get("raw_values") or {},
                store_input_image_bytes=_store_input_image_bytes,
                store_input_image_value=_store_input_image_value,
                skip={
                    *STANDARD_PARAMETER_ORDER,
                    "prompt",
                    "model",
                    "workflow",
                    "seconds",
                    "duration",
                    "size",
                    "fps",
                    "frames",
                    "width",
                    "height",
                    "quality",
                    "metadata",
                    "input_reference",
                    "image",
                },
            )
        )
        job = await jobs.create_job(
            kind=kind,
            workflow=workflow,
            requested_model=requested_model,
            seconds=str(payload.get("seconds") or "").strip(),
            size=str(payload.get("size") or "").strip(),
            quality=str(payload.get("quality") or "").strip() or "standard",
            metadata=str(payload.get("metadata") or "").strip(),
            prompt=prompt,
            image=image_rel,
            standard_params=standard_params,
        )

        return {
            "id": _video_id(job.job_id),
            "object": "video",
            "model": job.requested_model or workflow,
            "created_at": job.created_at,
            "status": "queued",
            "progress": 0,
        }

    @app.get("/v1/videos/{video_id}")
    async def openai_videos_get(
        request: Request,
        video_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        job_id = _as_job_id_from_video_id(video_id)
        job = await jobs.get_job(job_id)
        if not job:
            raise _openai_error("Video not found", http_status=404)

        status = "queued"
        if job.status == "running":
            status = "in_progress"
        elif job.status == "completed":
            status = "completed"
        elif job.status == "failed":
            status = "failed"

        completed = status == "completed"
        progress = _progress_percent(job.progress or {}, completed=completed)
        if status == "failed":
            progress = 0

        seconds = (job.seconds or "").strip() or "4"
        size = (job.size or "").strip() or "720x1280"
        quality = (job.quality or "").strip() or "standard"

        url = None
        expires_at = None
        if completed:
            url, expires_at = _authorized_url_parts(request, f"/v1/videos/{video_id}/content", authorization)

        err = None
        if status == "failed":
            err = {"message": job.error or "failed", "type": "server_error"}

        return {
            "id": _video_id(job.job_id),
            "object": "video",
            "model": job.requested_model or job.workflow,
            "created_at": job.created_at,
            "status": status,
            "progress": progress,
            "seconds": seconds,
            "size": size,
            "quality": quality,
            "url": url,
            "expires_at": expires_at,
            "remixed_from_video_id": None,
            "error": err,
        }

    @app.get("/v1/videos/{video_id}/content")
    async def openai_videos_content(
        request: Request,
        video_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Any:
        _require_download_access(cfg, request, authorization)
        job_id = _as_job_id_from_video_id(video_id)
        job = await jobs.get_job(job_id)
        if not job:
            raise _openai_error("Video not found", http_status=404)
        if job.status != "completed":
            raise _openai_error("Video not ready", http_status=409)

        pick = None
        for o in job.outputs or []:
            mt = (o.media_type or "").lower()
            if mt.startswith("video/"):
                pick = o
                break
            fn = (o.filename or "").lower()
            if fn.endswith((".mp4", ".webm", ".mov", ".gif")):
                pick = o
                break
        if pick is None and job.outputs:
            pick = job.outputs[0]
        if pick is None:
            raise _openai_error("No outputs produced", http_status=500)

        path = (cfg.runs_dir / job_id / pick.filename).resolve()
        if not path.exists():
            raise _openai_error("Output file missing", http_status=500)

        return FileResponse(path=str(path), media_type=pick.media_type or None, filename=pick.filename)

    @app.post("/v1/video/generations")
    async def newapi_video_generations_create(
        request: Request,
        body: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_auth(cfg, authorization)

        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            raise _openai_error("Missing 'prompt'")

        model = str(body.get("model") or "").strip()
        duration = body.get("duration")
        seconds = str(duration) if duration is not None else ""
        size = str(body.get("size") or "").strip()

        image_rel = ""
        kind = "txt2video"
        image_val = body.get("image")
        if isinstance(image_val, str) and image_val.strip():
            kind = "img2video"
            image_rel = await _store_input_image_value(image_val, filename_hint="image")

        workflow, requested_model = await _workflow_from_model_or_default(kind=kind, model=model)
        wf = await _resolve_workflow_name(workflow)
        standard_params = _collect_standard_params(
            {
                "duration": body.get("duration"),
                "size": body.get("size"),
                "fps": body.get("fps"),
                "frames": body.get("frames"),
                "width": body.get("width"),
                "height": body.get("height"),
                "steps": body.get("steps"),
                "cfg": body.get("cfg"),
                "seed": body.get("seed"),
            }
        )
        standard_params.update(
            await _collect_workflow_request_params(
                spec=wf.parameter_spec,
                values=body,
                store_input_image_bytes=_store_input_image_bytes,
                store_input_image_value=_store_input_image_value,
                skip={
                    *STANDARD_PARAMETER_ORDER,
                    "prompt",
                    "model",
                    "workflow",
                    "duration",
                    "size",
                    "fps",
                    "frames",
                    "width",
                    "height",
                    "steps",
                    "cfg",
                    "seed",
                    "image",
                    "metadata",
                    "response_format",
                },
            )
        )

        meta_obj: Dict[str, Any] = {}
        raw_meta = body.get("metadata")
        if isinstance(raw_meta, dict):
            meta_obj.update(raw_meta)
        for k in ("fps", "seed", "response_format"):
            if k in body:
                meta_obj[k] = body.get(k)

        job = await jobs.create_job(
            kind=kind,
            workflow=workflow,
            requested_model=requested_model,
            seconds=seconds,
            size=size,
            quality="standard",
            metadata=json.dumps(meta_obj, ensure_ascii=False) if meta_obj else "",
            prompt=prompt,
            image=image_rel,
            standard_params=standard_params,
        )

        return {"task_id": job.job_id, "status": "queued"}

    @app.get("/v1/video/generations/{task_id}")
    async def newapi_video_generations_get(
        request: Request,
        task_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_auth(cfg, authorization)
        job = await jobs.get_job(task_id)
        if not job:
            raise _openai_error("Task not found", http_status=404)

        status = "queued"
        if job.status == "running":
            status = "in_progress"
        elif job.status == "completed":
            status = "completed"
        elif job.status == "failed":
            status = "failed"

        url = None
        expires_at = None
        fmt = None
        if status == "completed":
            url, expires_at = _authorized_url_parts(request, f"/v1/videos/{_video_id(job.job_id)}/content", authorization)
            for o in job.outputs or []:
                fn = (o.filename or "")
                if "." in fn:
                    fmt = fn.rsplit(".", 1)[-1].lower()
                    break

        meta_out: Any = None
        if (job.metadata or "").strip():
            try:
                meta_out = json.loads(job.metadata)
            except Exception:
                meta_out = job.metadata

        err = None
        if status == "failed":
            err = {"code": 500, "message": job.error or "failed"}

        return {
            "task_id": job.job_id,
            "status": status,
            "url": url,
            "expires_at": expires_at,
            "format": fmt,
            "metadata": meta_out,
            "error": err,
        }

    return app


app = create_app()
