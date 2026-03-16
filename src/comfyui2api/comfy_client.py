from __future__ import annotations

import asyncio
import ipaddress
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional
from urllib.parse import urlencode, urljoin, urlparse

import httpx
import websockets


class ComfyApiError(RuntimeError):
    pass


def _truncate_text(value: str, *, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_http_error(endpoint: str, exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    request = exc.request
    body = _truncate_text(response.text)
    headers = {k: v for k, v in response.headers.items()}
    return (
        f"ComfyUI {endpoint} failed: status={response.status_code}, "
        f"url={request.url}, headers={headers}, body={body!r}"
    )


def _join(base_url: str, path: str) -> str:
    base = base_url.rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _base_host(base_url: str) -> str:
    raw = base_url if "://" in base_url else f"http://{base_url}"
    return (urlparse(raw).hostname or "").strip().lower()


def _should_trust_env(base_url: str) -> bool:
    host = _base_host(base_url)
    if not host:
        return True
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def _ws_url(base_url: str, *, client_id: str) -> str:
    u = urlparse(base_url)
    scheme = "wss" if u.scheme == "https" else "ws"
    netloc = u.netloc or u.path  # allow base_url like "127.0.0.1:8188"
    path = u.path if u.netloc else ""
    qs = urlencode({"clientId": client_id})
    return f"{scheme}://{netloc}{path.rstrip('/')}/ws?{qs}"


@dataclass(frozen=True)
class QueuedPrompt:
    prompt_id: str
    client_id: str
    number: Optional[int] = None


class ComfyUIClient:
    def __init__(self, base_url: str, *, http_timeout_s: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout=http_timeout_s)
        self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=_should_trust_env(self.base_url))
        self._object_info_lock = asyncio.Lock()
        self._object_info_cache: Optional[Dict[str, Any]] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def system_stats(self) -> Any:
        url = _join(self.base_url, "/system_stats")
        r = await self._client.get(url, headers={"Accept": "application/json"})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ComfyApiError(_format_http_error("/system_stats", e)) from e
        return r.json()

    async def get_queue(self) -> Any:
        url = _join(self.base_url, "/queue")
        r = await self._client.get(url, headers={"Accept": "application/json"})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ComfyApiError(_format_http_error("/queue", e)) from e
        return r.json()

    async def object_info(self, *, force: bool = False) -> Dict[str, Any]:
        async with self._object_info_lock:
            if self._object_info_cache is not None and not force:
                return self._object_info_cache
            url = _join(self.base_url, "/object_info")
            r = await self._client.get(url, headers={"Accept": "application/json"})
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise ComfyApiError(_format_http_error("/object_info", e)) from e
            payload = r.json()
            if not isinstance(payload, dict):
                raise ComfyApiError(f"Unexpected /object_info response type: {type(payload).__name__}")
            self._object_info_cache = payload
            return payload

    async def queue_prompt(
        self,
        *,
        prompt: Dict[str, Any],
        client_id: str,
        extra_data: Optional[Dict[str, Any]] = None,
        prompt_id: Optional[str] = None,
    ) -> QueuedPrompt:
        url = _join(self.base_url, "/prompt")
        payload: Dict[str, Any] = {"prompt": prompt, "client_id": client_id}
        if prompt_id:
            payload["prompt_id"] = prompt_id
        if isinstance(extra_data, dict) and extra_data:
            payload["extra_data"] = extra_data
        r = await self._client.post(url, json=payload, headers={"Accept": "application/json"})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ComfyApiError(_format_http_error("/prompt", e)) from e
        data = r.json()
        if not isinstance(data, dict):
            raise ComfyApiError(f"Unexpected /prompt response type: {type(data).__name__}")
        node_errors = data.get("node_errors")
        if isinstance(node_errors, dict) and node_errors:
            raise ComfyApiError(f"ComfyUI rejected the prompt (node_errors present): {json.dumps(node_errors, indent=2)}")
        pid = data.get("prompt_id")
        if not isinstance(pid, str) or not pid.strip():
            raise ComfyApiError(f"Missing prompt_id in /prompt response: {data}")
        number = data.get("number")
        return QueuedPrompt(prompt_id=pid.strip(), client_id=client_id, number=number if isinstance(number, int) else None)

    async def get_history_entry(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        url = _join(self.base_url, f"/history/{prompt_id}")
        r = await self._client.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and prompt_id in data and isinstance(data[prompt_id], dict):
            return data[prompt_id]
        return None

    async def view_bytes(self, *, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        qs = urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
        url = _join(self.base_url, "/view?" + qs)
        r = await self._client.get(url, headers={"Accept": "*/*"})
        r.raise_for_status()
        return r.content

    async def upload_image(
        self,
        *,
        data: bytes,
        filename: str,
        subfolder: str = "",
        folder_type: str = "input",
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        url = _join(self.base_url, "/upload/image")
        safe_subfolder = (subfolder or "").strip().replace("\\", "/").strip("/")
        form = {
            "subfolder": safe_subfolder,
            "type": (folder_type or "input").strip(),
            "overwrite": "true" if overwrite else "false",
        }
        files = {"image": (filename or "image.png", data, "application/octet-stream")}
        r = await self._client.post(url, data=form, files=files, headers={"Accept": "application/json"})
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise ComfyApiError(_format_http_error("/upload/image", e)) from e
        payload = r.json()
        if not isinstance(payload, dict):
            raise ComfyApiError(f"Unexpected /upload/image response type: {type(payload).__name__}")
        return payload

    async def upload_image_bytes(
        self,
        *,
        data: bytes,
        filename: str,
        subfolder: str = "",
        folder_type: str = "input",
        overwrite: bool = True,
    ) -> str:
        resp = await self.upload_image(
            data=data, filename=filename, subfolder=subfolder, folder_type=folder_type, overwrite=overwrite
        )
        name = resp.get("name")
        if not isinstance(name, str) or not name:
            raise ComfyApiError(f"Missing 'name' in /upload/image response: {resp}")
        resp_sub = resp.get("subfolder") or ""
        if not isinstance(resp_sub, str):
            resp_sub = ""
        resp_sub = resp_sub.strip().replace("\\", "/").strip("/")
        rel = f"{resp_sub}/{name}" if resp_sub else name
        return rel.replace("\\", "/")

    async def ws_events(self, *, client_id: str) -> AsyncIterator[Dict[str, Any]]:
        url = _ws_url(self.base_url, client_id=client_id)
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            async for raw in ws:
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict):
                    yield msg

    async def wait_for_history_complete(
        self,
        *,
        prompt_id: str,
        timeout_s: int,
        poll_interval_s: float,
    ) -> Dict[str, Any]:
        deadline = None if timeout_s <= 0 else (asyncio.get_running_loop().time() + timeout_s)
        while True:
            entry = await self.get_history_entry(prompt_id)
            if entry:
                status = entry.get("status", {})
                if isinstance(status, dict) and status.get("completed") is True:
                    return entry
            if deadline is not None and asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"Timed out waiting for completion after {timeout_s}s (prompt_id={prompt_id}).")
            await asyncio.sleep(max(0.05, float(poll_interval_s)))
