from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from shutil import which

from app_paths import project_dir
from annotator.server import run_server

HOST = os.environ.get("REMASK_ANNOTATOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("REMASK_ANNOTATOR_PORT", "8788"))
URL = f"http://{HOST}:{PORT}/"


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


def open_app_window() -> None:
    if os.environ.get("REMASK_ANNOTATOR_OPEN_BROWSER") == "1":
        webbrowser.open(URL)
        return

    edge_path = find_edge()
    if not edge_path:
        webbrowser.open(URL)
        keep_running()
        return

    profile_dir = project_dir() / "data" / "edge-app-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            str(edge_path),
            f"--app={URL}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-features=Translate",
            "--window-size=1280,840",
        ]
    )
    exit_code = process.wait()
    if exit_code != 0:
        webbrowser.open(URL)
        keep_running()


def find_edge() -> Path | None:
    found = which("msedge")
    if found:
        return Path(found)

    candidates = [
        os.environ.get("REMASK_ANNOTATOR_EDGE"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        str(Path.home() / r"AppData\Local\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


def keep_running() -> None:
    while True:
        time.sleep(3600)


def main() -> None:
    os.chdir(project_dir())
    if os.environ.get("REMASK_ANNOTATOR_SERVER_ONLY") == "1":
        run_server(HOST, PORT)
        return

    if not service_is_running():
        threading.Thread(target=run_server, args=(HOST, PORT), daemon=True).start()
        wait_for_service()

    if os.environ.get("REMASK_ANNOTATOR_NO_WINDOW") != "1":
        open_app_window()


if __name__ == "__main__":
    main()
