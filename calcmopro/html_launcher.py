"""Launcher for the exact SAPHIR Pro HTML interface."""

from __future__ import annotations

import sys
import os
import shutil
import subprocess
import tempfile
import webbrowser
from pathlib import Path
from tkinter import messagebox

from calcmopro.api_server import start_server


HTML_NAME = "CALCUL_MOYENNE_ORIENTATION.html"


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def find_interface_file() -> Path:
    bundled = resource_root() / "web" / HTML_NAME
    if bundled.exists():
        return bundled
    local = Path(__file__).resolve().parents[1] / "web" / HTML_NAME
    if local.exists():
        return local
    raise FileNotFoundError(f"Interface HTML introuvable : {HTML_NAME}")


def materialize_interface() -> Path:
    """Copy the bundled page to a persistent location before the one-file app exits."""
    source = find_interface_file().resolve()
    local_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    target_dir = local_root / "CALCMO-Pro"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / HTML_NAME
    shutil.copy2(source, target)
    return target


def find_edge() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", ""))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", ""))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def open_app_window(port: int) -> None:
    app_url = f"http://127.0.0.1:{port}/"
    edge = find_edge()
    if edge is None:
        webbrowser.open(app_url, new=2)
        return
    subprocess.Popen(
        [
            str(edge),
            f"--app={app_url}",
            "--start-maximized",
            "--new-window",
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main() -> None:
    import time
    import traceback
    log_path = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "CALCMO-Pro" / "crash.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"CALCMO-Pro starting...\n")
        html_path = materialize_interface()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"HTML: {html_path} (exists={html_path.exists()})\n")
        port = start_server(html_path)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"Server started on port {port}\n")
        open_app_window(port)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"Edge launched\n")
        # Keep the process alive so the server thread keeps running.
        # Sleep in small intervals so the daemon thread stays alive.
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001 - user-facing launcher boundary.
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"ERROR: {exc}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        try:
            messagebox.showerror("CALCMO Pro", str(exc))
        except Exception:
            pass


if __name__ == "__main__":
    main()
