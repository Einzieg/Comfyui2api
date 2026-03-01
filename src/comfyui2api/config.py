from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return default if v is None else str(v).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name, "")
    if not raw:
        return int(default)
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name, "")
    if not raw:
        return float(default)
    return float(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name, "")
    if not raw:
        return bool(default)
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _default_comfyui_input_dir(project_root: Path) -> Path:
    workspace_root = project_root.parent
    candidate = workspace_root / "ComfyUI_windows_portable" / "ComfyUI" / "input"
    return candidate if candidate.exists() else (project_root / "comfyui-input")


@dataclass(frozen=True)
class Config:
    api_listen: str
    api_port: int
    api_token: str
    public_base_url: str

    comfy_base_url: str
    workflows_dir: Path
    runs_dir: Path
    comfyui_input_dir: Path
    input_subdir: str
    image_upload_mode: str

    max_body_bytes: int
    max_image_bytes: int
    timeout_s: int
    poll_interval_s: float
    http_timeout_s: int

    worker_concurrency: int
    enable_workflow_watch: bool

    default_txt2img_workflow: str
    default_img2img_workflow: str
    default_txt2video_workflow: str
    default_img2video_workflow: str


def load_config() -> Config:
    project_root = Path(__file__).resolve().parents[2]

    workflows_dir = Path(_env_str("WORKFLOWS_DIR", str(project_root / "comfyui-api-workflows"))).expanduser()
    runs_dir = Path(_env_str("RUNS_DIR", str(project_root / "runs"))).expanduser()

    comfyui_input_dir = Path(
        _env_str("COMFYUI_INPUT_DIR", str(_default_comfyui_input_dir(project_root)))
    ).expanduser()

    comfy_base_url = _env_str("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/")

    return Config(
        api_listen=_env_str("API_LISTEN", "0.0.0.0"),
        api_port=_env_int("API_PORT", 8000),
        api_token=_env_str("API_TOKEN", ""),
        public_base_url=_env_str("PUBLIC_BASE_URL", "").rstrip("/"),
        comfy_base_url=comfy_base_url,
        workflows_dir=workflows_dir,
        runs_dir=runs_dir,
        comfyui_input_dir=comfyui_input_dir,
        input_subdir=_env_str("INPUT_SUBDIR", "comfyui2api").strip("/").strip("\\"),
        image_upload_mode=_env_str("IMAGE_UPLOAD_MODE", "auto"),
        max_body_bytes=_env_int("MAX_BODY_BYTES", 30_000_000),
        max_image_bytes=_env_int("MAX_IMAGE_BYTES", 20_000_000),
        timeout_s=_env_int("TIMEOUT_S", 3600),
        poll_interval_s=_env_float("POLL_INTERVAL_S", 0.5),
        http_timeout_s=_env_int("HTTP_TIMEOUT_S", 30),
        worker_concurrency=max(1, _env_int("WORKER_CONCURRENCY", 1)),
        enable_workflow_watch=_env_bool("ENABLE_WORKFLOW_WATCH", True),
        default_txt2img_workflow=_env_str("DEFAULT_TXT2IMG_WORKFLOW", "文生图_z_image_turbo.json"),
        default_img2img_workflow=_env_str("DEFAULT_IMG2IMG_WORKFLOW", "图生图_flux2.json"),
        default_txt2video_workflow=_env_str("DEFAULT_TXT2VIDEO_WORKFLOW", ""),
        default_img2video_workflow=_env_str("DEFAULT_IMG2VIDEO_WORKFLOW", "img2video.json"),
    )
