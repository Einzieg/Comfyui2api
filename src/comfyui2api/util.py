from __future__ import annotations

import base64
import json
import mimetypes
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from binascii import Error as BinasciiError


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def sanitize_filename_part(value: str, max_len: int = 160) -> str:
    invalid = '<>:"/\\\\|?*'
    for ch in invalid:
        value = value.replace(ch, "_")
    value = value.replace("\0", "_").strip().strip(".")
    if not value:
        value = "_"
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if value.upper() in reserved:
        value = f"_{value}_"
    return value


def guess_image_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def decode_data_url_base64(value: str) -> bytes:
    s = (value or "").strip()
    if not s:
        raise ValueError("image_base64 is empty")
    if s.startswith("data:"):
        if "," not in s:
            raise ValueError("Invalid data URL")
        _, s = s.split(",", 1)
    s = "".join(s.split())
    try:
        return base64.b64decode(s, validate=False)
    except BinasciiError as e:
        raise ValueError(f"Invalid base64: {e}") from e


def validate_relpath_in_input(value: str) -> str:
    rel = (value or "").strip().replace("\\", "/")
    if not rel:
        raise ValueError("image is empty")
    if rel.startswith("/") or rel.startswith("\\") or ":" in rel:
        raise ValueError("image must be a relative path under ComfyUI input dir")
    parts = [p for p in rel.split("/") if p]
    if any(p == ".." for p in parts):
        raise ValueError("image path traversal not allowed")
    return "/".join(parts)


def save_input_image(
    *,
    input_dir: Path,
    subdir: str,
    job_id: str,
    data: bytes,
    filename_hint: Optional[str],
    max_bytes: int,
) -> str:
    if len(data) > max(1, int(max_bytes)):
        raise ValueError(f"image too large ({len(data)} bytes)")

    ext = ""
    stem = "image"
    if filename_hint and isinstance(filename_hint, str):
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
    filename = f"{safe_prefix}--{safe_stem}{ext}"

    rel = f"{subdir}/{filename}" if subdir else filename
    out_path = (input_dir / subdir / filename) if subdir else (input_dir / filename)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return validate_relpath_in_input(rel)


def pick_primary_url(outputs: list[dict[str, Any]]) -> Optional[str]:
    if not outputs:
        return None

    preferred_exts = (".mp4", ".webm", ".gif", ".mov")
    for ext in preferred_exts:
        for item in outputs:
            fn = item.get("filename")
            url = item.get("url")
            if isinstance(fn, str) and isinstance(url, str) and fn.lower().endswith(ext):
                return url

    for item in outputs:
        url = item.get("url")
        if isinstance(url, str) and url:
            return url
    return None


def guess_media_type(filename: str) -> str:
    mt, _ = mimetypes.guess_type(filename)
    return mt or "application/octet-stream"


def bearer_authorized(header_value: str, expected: str) -> bool:
    if not expected:
        return True
    raw = (header_value or "").strip()
    if not raw.lower().startswith("bearer "):
        return False
    token = raw.split(" ", 1)[1].strip()
    return secrets.compare_digest(token, expected)

