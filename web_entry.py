"""Entry point for Render deployment."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from calcmopro.api_server import start_server, serve_forever_blocking
from calcmopro import database as db

HTML_NAME = "CALCUL_MOYENNE_ORIENTATION.html"


def prepare_html() -> Path:
    src = Path(__file__).resolve().parent / "web" / HTML_NAME
    tmp_dir = Path(tempfile.gettempdir()) / "calcmo-web"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dst = tmp_dir / HTML_NAME

    content = src.read_text(encoding="utf-8")
    content = content.replace(
        "const API='http://127.0.0.1:'+location.port;",
        "const API='';",
    )
    dst.write_text(content, encoding="utf-8")
    return dst


def main() -> None:
    db.init_db()
    html_path = prepare_html()
    port = int(os.environ.get("PORT", 8080))
    start_server(html_path, port=port, host="0.0.0.0")
    print(f"SAPHIR Pro en ligne sur le port {port}")
    serve_forever_blocking()


if __name__ == "__main__":
    main()
