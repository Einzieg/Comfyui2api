from __future__ import annotations

import os
import argparse
import sys
import threading
import time
import urllib.request
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


def _make_server(app, *, host: str, port: int, log_level: str) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level=log_level))
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    return server


def _serve_app(app, *, host: str, port: int, log_level: str) -> None:
    _make_server(app, host=host, port=port, log_level=log_level).run()


def _wait_for_health(url: str, *, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    health_url = url.rsplit("/", 1)[0] + "/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _run_desktop_window(app, *, host: str, port: int, log_level: str, should_open: bool) -> None:
    url = f"http://{host}:{port}/ui"
    server = _make_server(app, host=host, port=port, log_level=log_level)
    server_thread = threading.Thread(target=server.run, name="comfyui2api-server", daemon=True)
    server_thread.start()

    ready = _wait_for_health(url)
    if should_open and ready:
        open_browser_later(url, delay_s=0.1)

    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        server_thread.join()
        return

    root = tk.Tk()
    root.title("comfyui2api")
    root.geometry("420x220")
    root.minsize(420, 220)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="comfyui2api", font=("", 16, "bold")).pack(anchor="w")
    status = ttk.Label(frame, text=("Running" if ready else "Starting"), foreground=("#0b7a45" if ready else "#9a6700"))
    status.pack(anchor="w", pady=(8, 0))
    ttk.Label(frame, text=url).pack(anchor="w", pady=(4, 18))

    actions = ttk.Frame(frame)
    actions.pack(anchor="w")

    open_button = ttk.Button(actions, text="Open Dashboard", command=lambda: webbrowser.open(url))
    open_button.pack(side="left")

    def request_quit() -> None:
        status.configure(text="Shutting down", foreground="#9a3412")
        open_button.configure(state="disabled")
        quit_button.configure(state="disabled")
        server.should_exit = True
        root.after(200, wait_for_exit)

    def wait_for_exit() -> None:
        if server_thread.is_alive():
            root.after(200, wait_for_exit)
            return
        root.destroy()

    quit_button = ttk.Button(actions, text="Quit", command=request_quit)
    quit_button.pack(side="left", padx=(10, 0))
    root.protocol("WM_DELETE_WINDOW", request_quit)

    def watch_server() -> None:
        if server_thread.is_alive():
            root.after(500, watch_server)
            return
        if root.winfo_exists():
            root.destroy()

    root.after(500, watch_server)
    root.mainloop()


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

    from comfyui2api.app import app

    if command == "ui" and getattr(sys, "frozen", False) and not _env_bool("COMFYUI2API_NO_WINDOW", False):
        _run_desktop_window(app, host=host, port=port, log_level=args.log_level, should_open=should_open)
        return

    if command == "ui" and should_open:
        open_browser_later(f"http://{host}:{port}/ui")

    _serve_app(app, host=host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
