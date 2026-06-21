"""Lightweight HTTP API server for CALCMO Pro.

Serves the HTML interface and exposes REST endpoints backed by SQLite.
Supports optional password authentication with admin/student roles.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from calcmopro import database as db


_html_path: Path | None = None
_login_path: Path | None = None
_server: HTTPServer | None = None
_thread: threading.Thread | None = None

# Auth
_APP_PASSWORD: str = os.environ.get("APP_PASSWORD", "RECEPTIOn8@2024")
_STUDENT_PASSWORD: str = os.environ.get("STUDENT_PASSWORD", "ELEVE2024")
_sessions: dict[str, tuple[float, str]] = {}  # token -> (expiry, role)
_SESSION_TTL = 86400 * 7  # 7 days


def _hash_password(pwd: str) -> str:
    return hashlib.sha256(pwd.encode()).hexdigest()


def _create_session(role: str = "admin") -> str:
    token = secrets.token_hex(32)
    _sessions[token] = (time.time() + _SESSION_TTL, role)
    return token


def _get_session_role(token: str | None) -> str | None:
    if not token:
        return None
    session = _sessions.get(token)
    if session is None:
        return None
    expiry, role = session
    if time.time() > expiry:
        del _sessions[token]
        return None
    return role


def _check_session(token: str | None) -> bool:
    return _get_session_role(token) is not None


def _auth_required() -> bool:
    return bool(_APP_PASSWORD)


class _Handler(BaseHTTPRequestHandler):
    """Route requests between the HTML file and /api/* endpoints."""

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def _get_cookie(self, name: str) -> str | None:
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_with_cookie(self, data: object, cookie_name: str, cookie_val: str, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Set-Cookie", f"{cookie_name}={cookie_val}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_SESSION_TTL}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw)

    def _html(self, path: Path, status: int = 200) -> None:
        body = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url: str) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _is_authenticated(self) -> bool:
        if not _auth_required():
            return True
        token = self._get_cookie("session")
        return _check_session(token)

    def _require_auth(self) -> bool:
        if self._is_authenticated():
            return True
        self._redirect("/login")
        return False

    def _get_role(self) -> str:
        token = self._get_cookie("session")
        role = _get_session_role(token)
        return role or "guest"

    # ── routing ──────────────────────────────────────────────
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/login":
            if _login_path and _login_path.exists():
                return self._html(_login_path)
            return self._json({"error": "Login page not found"}, 404)

        if not self._require_auth():
            return

        if path == "" or path == "/index.html":
            return self._html(_html_path)

        if path == "/api/role":
            return self._json({"role": self._get_role()})

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

        if path == "/api/login":
            if not _auth_required():
                return self._json({"ok": True, "role": "admin"})
            body = self._read_body()
            pwd = body.get("password", "")

            if _hash_password(pwd) == _hash_password(_APP_PASSWORD):
                token = _create_session("admin")
                return self._json_with_cookie({"ok": True, "role": "admin"}, "session", token)

            if _STUDENT_PASSWORD and _hash_password(pwd) == _hash_password(_STUDENT_PASSWORD):
                token = _create_session("student")
                return self._json_with_cookie({"ok": True, "role": "student"}, "session", token)

            return self._json({"error": "Mot de passe incorrect"}, 401)

        if path == "/api/logout":
            token = self._get_cookie("session")
            if token and token in _sessions:
                del _sessions[token]
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.end_headers()
            return

        if not self._require_auth():
            return

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
        if not self._require_auth():
            return

        role = self._get_role()

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/eleves/clear":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            db.clear_all()
            return self._json({"ok": True})

        if path.startswith("/api/eleves/"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
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


def start_server(html_path: Path, port: int = 0, host: str = "127.0.0.1",
                 login_path: Path | None = None) -> int:
    global _html_path, _login_path, _server, _thread
    _html_path = html_path
    _login_path = login_path
    _server = HTTPServer((host, port), _Handler)
    bound = _server.server_address[1]
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()
    return bound


def serve_forever_blocking() -> None:
    if _server:
        _server.serve_forever()


def stop_server() -> None:
    if _server:
        _server.shutdown()
