from __future__ import annotations

import os
import socket
import traceback
import threading
import time
import webbrowser
from datetime import datetime

from app_paths import project_dir
from annotator.server import run_server

HOST = os.environ.get("MOTION_MOSAIC_HOST") or os.environ.get("REMASK_ANNOTATOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("MOTION_MOSAIC_PORT") or os.environ.get("REMASK_ANNOTATOR_PORT", "8788"))
URL = f"http://{HOST}:{PORT}/"
APP_TITLE = "Motion Mosaic"


def service_is_running() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.35):
            return True
    except OSError:
        return False


def wait_for_service(timeout_seconds: float = 20.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if service_is_running():
            return
        time.sleep(0.1)
    raise RuntimeError(f"Service did not start at {URL}")


def open_browser_window(keep_process: bool) -> None:
    webbrowser.open(URL)
    if keep_process:
        keep_running()


def log_launcher_error(context: str) -> None:
    log_path = project_dir() / "data" / "launcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(f"[{datetime.now().isoformat(timespec='seconds')}] {context}\n")
        file.write(traceback.format_exc())
        file.write("\n")


def show_launch_error(context: str) -> None:
    try:
        import ctypes

        message = (
            f"{context}\n\n"
            f"Web UI is still available at {URL} if the local service is running.\n"
            "Set MOTION_MOSAIC_OPEN_BROWSER=1 only when you explicitly want browser mode.\n\n"
            "Details were written to data\\launcher.log."
        )
        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
    except Exception:
        pass


def fallback_to_browser_window(context: str, keep_process: bool) -> None:
    log_launcher_error(context)
    try:
        open_browser_window(keep_process)
    except Exception:
        show_launch_error(context)


def open_app_window(keep_process: bool) -> None:
    if os.environ.get("MOTION_MOSAIC_OPEN_BROWSER") == "1" or os.environ.get("REMASK_ANNOTATOR_OPEN_BROWSER") == "1":
        open_browser_window(keep_process)
        return

    try:
        import webview
    except Exception:
        fallback_to_browser_window("Native desktop window dependency failed to load.", keep_process)
        return

    root = project_dir()
    profile_dir = root / "data" / "webview-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    icon_path = root / "assets" / "motion-mosaic-icon.png"

    webview.create_window(
        APP_TITLE,
        URL,
        width=1280,
        height=840,
        min_size=(960, 680),
        text_select=True,
        background_color="#f4f8f6",
    )
    try:
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(profile_dir),
            icon=str(icon_path) if icon_path.is_file() else None,
        )
    except Exception:
        fallback_to_browser_window("Native desktop window failed to start.", keep_process)


def keep_running() -> None:
    while True:
        time.sleep(3600)


def main() -> None:
    os.chdir(project_dir())
    if os.environ.get("MOTION_MOSAIC_SERVER_ONLY") == "1" or os.environ.get("REMASK_ANNOTATOR_SERVER_ONLY") == "1":
        run_server(HOST, PORT)
        return

    started_service = False
    if not service_is_running():
        threading.Thread(target=run_server, args=(HOST, PORT), daemon=True).start()
        wait_for_service()
        started_service = True

    if os.environ.get("MOTION_MOSAIC_NO_WINDOW") != "1" and os.environ.get("REMASK_ANNOTATOR_NO_WINDOW") != "1":
        open_app_window(started_service)
    elif started_service:
        keep_running()


if __name__ == "__main__":
    main()
