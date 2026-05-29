from __future__ import annotations

import os
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn


def _try_load_dotenv(path: Path) -> bool:
    try:
        from dotenv import load_dotenv
    except Exception:
        return False
    if not path.exists():
        return False
    load_dotenv(dotenv_path=str(path), override=False)
    return True


def _load_env(env_file_arg: str = "") -> None:
    env_file = (env_file_arg or os.environ.get("ENV_FILE") or "").strip()
    if env_file:
        _try_load_dotenv(Path(env_file))
        return

    _try_load_dotenv(Path.cwd() / ".env")

    project_root = Path(__file__).resolve().parents[2]
    _try_load_dotenv(project_root / ".env")


def _env_file_from_argv(argv: list[str] | None) -> str:
    items = list(argv or [])
    for index, item in enumerate(items):
        if item == "--env-file" and index + 1 < len(items):
            return items[index + 1]
        if item.startswith("--env-file="):
            return item.split("=", 1)[1]
    return ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _ensure_standard_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return

    if getattr(sys, "frozen", False):
        log_dir = Path(sys.executable).resolve().parent / "logs"
    else:
        log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = (log_dir / "comfyui2api-desktop.log").open("a", encoding="utf-8", buffering=1)

    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comfyui2api")
    subparsers = parser.add_subparsers(dest="command")

    def add_common_options(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--host", default="")
        subparser.add_argument("--port", type=int, default=0)
        subparser.add_argument("--env-file", default="")
        subparser.add_argument("--log-level", default="info")
        subparser.add_argument("--disable-ui", action="store_true")

    ui = subparsers.add_parser("ui", help="start API service and open the Web UI")
    add_common_options(ui)
    ui.add_argument("--no-open", action="store_true")

    serve = subparsers.add_parser("serve", help="start API service without opening the Web UI")
    add_common_options(serve)

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def open_browser_later(url: str, *, delay_s: float = 1.0) -> None:
    timer = threading.Timer(delay_s, webbrowser.open, args=(url,))
    timer.daemon = True
    timer.start()


def main(argv: list[str] | None = None) -> None:
    _ensure_standard_streams()
    _load_env(_env_file_from_argv(argv))
    args = parse_args(argv)
    command = args.command or "ui"

    if command == "ui":
        host = (args.host or "127.0.0.1").strip() or "127.0.0.1"
        should_open = not args.no_open and not _env_bool("COMFYUI2API_NO_OPEN", False)
    else:
        host = (args.host or os.environ.get("API_LISTEN", "0.0.0.0")).strip() or "0.0.0.0"
        should_open = False

    port = int(args.port or os.environ.get("API_PORT", "8000"))
    os.environ["API_LISTEN"] = host
    os.environ["API_PORT"] = str(port)

    if args.disable_ui:
        os.environ["COMFYUI2API_DISABLE_UI"] = "1"

    if command == "ui" and should_open:
        open_browser_later(f"http://{host}:{port}/ui")

    from comfyui2api.app import app

    uvicorn.run(app, host=host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
