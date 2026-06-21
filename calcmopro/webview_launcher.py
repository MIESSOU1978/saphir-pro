"""Native Python WebView launcher for the exact SAPHIR Pro HTML interface."""

from __future__ import annotations

import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox


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


def show_error(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("SAPHIR Pro", message)
    root.destroy()


def show_splash() -> None:
    splash = tk.Tk()
    splash.title("SAPHIR Pro")
    splash.configure(bg="#0a1628")
    splash.geometry("420x150+420+260")
    splash.resizable(False, False)
    splash.attributes("-topmost", True)
    tk.Label(
        splash,
        text="SAPHIR Pro",
        bg="#0a1628",
        fg="#38bdf8",
        font=("Segoe UI", 18, "bold"),
    ).pack(pady=(28, 4))
    tk.Label(
        splash,
        text="Chargement de l'interface...",
        bg="#0a1628",
        fg="#f1f5f9",
        font=("Segoe UI", 10),
    ).pack()
    splash.update()
    splash.after(900, splash.destroy)
    splash.mainloop()


def open_in_browser(html_path: Path) -> None:
    webbrowser.open(html_path.as_uri(), new=2)


def main() -> None:
    try:
        import webview

        html_path = find_interface_file().resolve()
        html = html_path.read_text(encoding="utf-8")
        show_splash()
        webview.create_window(
            "SAPHIR Pro - Orientation BEPC",
            html=html,
            width=1220,
            height=820,
            min_size=(1020, 700),
            maximized=True,
            focus=True,
            on_top=True,
            background_color="#0a1628",
        )
        webview.start(debug=False, gui="edgechromium", private_mode=False)
    except Exception as exc:  # noqa: BLE001 - user-facing launcher boundary.
        try:
            html_path = find_interface_file().resolve()
            open_in_browser(html_path)
            show_error(
                "La fenetre Python native n'a pas pu demarrer. "
                "La meme interface exacte a ete ouverte dans le navigateur.\n\n"
                f"Detail : {exc}"
            )
        except Exception:
            show_error(str(exc))


if __name__ == "__main__":
    main()
