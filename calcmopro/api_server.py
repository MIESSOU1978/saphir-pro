"""Lightweight HTTP API server for CALCMO Pro.

Serves the HTML interface and exposes REST endpoints backed by SQLite.
Runs in a daemon thread so the main process can keep the Edge window alive.
"""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from calcmopro import database as db


_PORT = 0  # auto-assign
_html_path: Path | None = None
_server: HTTPServer | None = None
_thread: threading.Thread | None = None


class _Handler(BaseHTTPRequestHandler):
    """Route requests between the HTML file and /api/* endpoints."""

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        pass  # silence request logs

    # ── helpers ──────────────────────────────────────────────
    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _html(self, path: Path) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── routing ──────────────────────────────────────────────
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/index.html":
            return self._html(_html_path)

        if path == "/api/eleves":
            return self._json(db.list_eleves())

        if path.startswith("/api/eleves/"):
            try:
                eid = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            row = db.get_eleve(eid)
            return self._json(row if row else {"error": "introuvable"}, 404 if row is None else 200)

        if path == "/api/stats":
            rows = db.list_eleves()
            total = len(rows)
            admitted = sum(1 for r in rows if (r.get("mo") or 0) >= 10)
            return self._json({
                "effectif": total,
                "admis": admitted,
                "insuffisants": total - admitted,
            })

        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/eleves":
            body = self._read_body()
            nom = (body.get("nom") or "").strip()
            if not nom:
                return self._json({"error": "Le nom est obligatoire."}, 400)
            result = db.save_eleve(
                nom=nom,
                matricule=body.get("matricule", ""),
                classe=body.get("classe", ""),
                etablissement=body.get("etablissement", ""),
                annee=body.get("annee", ""),
                total=body.get("total", 0),
                mo=body.get("mo", 0),
                mention=body.get("mention", ""),
                matieres=body.get("matieres"),
            )
            return self._json(result, 201)

        self.send_error(404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/eleves/clear":
            db.clear_all()
            return self._json({"ok": True})

        if path.startswith("/api/eleves/"):
            try:
                eid = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            db.delete_eleve(eid)
            return self._json({"ok": True})

        self.send_error(404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def start_server(html_path: Path, port: int = 0, host: str = "127.0.0.1") -> int:
    """Start the server in a daemon thread. Returns the bound port."""
    global _html_path, _server, _thread
    _html_path = html_path
    _server = HTTPServer((host, port), _Handler)
    bound = _server.server_address[1]
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    return bound


def serve_forever_blocking() -> None:
    """Block the main thread keeping the server alive (for web deployments)."""
    if _server:
        _server.serve_forever()


def stop_server() -> None:
    if _server:
        _server.shutdown()
