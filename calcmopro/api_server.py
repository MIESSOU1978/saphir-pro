"""Lightweight HTTP API server for CALCMO Pro.

Serves the HTML interface and exposes REST endpoints backed by SQLite.
Supports optional password authentication with admin/student roles.

Security: PBKDF2-HMAC-SHA256 password hashing, HMAC-signed session tokens,
          CORS restricted to Render domain, rate limiting on login.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from calcmopro import database as db


_html_path: Path | None = None
_login_path: Path | None = None
_server: HTTPServer | None = None
_thread: threading.Thread | None = None

# Auth
_APP_PASSWORD: str = os.environ.get("APP_PASSWORD", "")
_STUDENT_PASSWORD: str = os.environ.get("STUDENT_PASSWORD", "")
_SESSION_TTL = 86400 * 7  # 7 days
_IS_RENDER = bool(os.environ.get("PORT"))

# Security: CORS origin, rate limiting, HMAC secret
_CORS_ORIGIN = "https://saphir-pro.onrender.com"
_HMAC_KEY = hashlib.sha256(
    (_APP_PASSWORD or "calcmo-default-key").encode()
).digest()
_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW = 300  # 5 minutes

# Security: PBKDF2-HMAC-SHA256 salt (fixed, app-specific)
_PASSWORD_SALT = b"calcmo-saphir-pro-salt-2024-v2"


def _hash_password(pwd: str) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 (100k iterations)."""
    return hashlib.pbkdf2_hmac(
        "sha256", pwd.encode(), _PASSWORD_SALT, 100000
    ).hex()


def _create_session(role: str = "admin") -> str:
    """Create HMAC-signed session token: role|expiry|signature."""
    expiry = int(time.time()) + _SESSION_TTL
    payload = f"{role}|{expiry}"
    sig = hmac.new(_HMAC_KEY, payload.encode(), "sha256").hexdigest()[:16]
    return f"{payload}|{sig}"


def _get_session_role(token: str | None) -> str | None:
    """Validate HMAC-signed session token and return role."""
    if not token:
        return None
    parts = token.split("|")
    if len(parts) != 3:
        return None
    role, expiry_str, sig = parts
    # Verify HMAC signature
    expected = hmac.new(
        _HMAC_KEY, f"{role}|{expiry_str}".encode(), "sha256"
    ).hexdigest()[:16]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        if time.time() > int(expiry_str):
            return None
    except ValueError:
        return None
    if role not in ("admin", "student"):
        return None
    return role


def _check_session(token: str | None) -> bool:
    return _get_session_role(token) is not None


def _auth_required() -> bool:
    return bool(_APP_PASSWORD)


def _is_rate_limited(ip: str) -> bool:
    """Check if IP has exceeded login rate limit (only counts failures)."""
    now = time.time()
    # Clean old entries
    _LOGIN_ATTEMPTS[ip] = [
        t for t in _LOGIN_ATTEMPTS[ip] if now - t < _LOGIN_WINDOW
    ]
    return len(_LOGIN_ATTEMPTS[ip]) >= _MAX_LOGIN_ATTEMPTS


def _record_failed_login(ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    _LOGIN_ATTEMPTS[ip].append(time.time())


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

    def _cors_header(self) -> str:
        """Return CORS origin — restricted to Render domain."""
        return _CORS_ORIGIN

    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", self._cors_header())
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_with_cookie(self, data: object, cookie_name: str, cookie_val: str, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        secure = "; Secure" if _IS_RENDER else ""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", self._cors_header())
        self.send_header("Set-Cookie", f"{cookie_name}={cookie_val}; Path=/; HttpOnly; SameSite=Lax{secure}; Max-Age={_SESSION_TTL}")
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
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _xml(self, path: Path, status: int = 200) -> None:
        body = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, path: Path, status: int = 200) -> None:
        body = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
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
        try:
            self._handle_get()
        except Exception as exc:
            print(f"[FATAL] do_GET: {exc}")
            try:
                self._json({"error": str(exc)}, 500)
            except Exception:
                self.send_error(500)

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/login":
            if _login_path and _login_path.exists():
                return self._html(_login_path)
            return self._json({"error": "Login page not found"}, 404)

        if path == "/sitemap.xml":
            sm = _html_path.parent / "sitemap.xml"
            if sm.exists():
                return self._xml(sm)
            return self.send_error(404)

        if path == "/robots.txt":
            rt = _html_path.parent / "robots.txt"
            if rt.exists():
                return self._text(rt)
            return self.send_error(404)

        if not self._require_auth():
            return

        if path == "" or path == "/index.html":
            return self._html(_html_path)

        if path == "/api/role":
            return self._json({"role": self._get_role()})

        if path == "/api/eleves":
            try:
                return self._json(db.list_eleves())
            except Exception as exc:
                print(f"[ERROR] list_eleves: {exc}")
                return self._json([], 500)

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

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except Exception as exc:
            print(f"[FATAL] do_POST: {exc}")
            try:
                self._json({"error": str(exc)}, 500)
            except Exception:
                self.send_error(500)

    def _handle_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/login":
            # Rate limiting: max 5 attempts per IP per 5 minutes
            client_ip = self.client_address[0]
            if _is_rate_limited(client_ip):
                return self._json({"error": "Trop de tentatives. Réessayez dans 5 minutes."}, 429)

            body = self._read_body()
            pwd = body.get("password", "")

            if not _auth_required():
                if _STUDENT_PASSWORD and _hash_password(pwd) == _hash_password(_STUDENT_PASSWORD):
                    token = _create_session("student")
                    return self._json_with_cookie({"ok": True, "role": "student"}, "session", token)
                return self._json({"ok": True, "role": "admin"})

            if _hash_password(pwd) == _hash_password(_APP_PASSWORD):
                token = _create_session("admin")
                return self._json_with_cookie({"ok": True, "role": "admin"}, "session", token)

            if _STUDENT_PASSWORD and _hash_password(pwd) == _hash_password(_STUDENT_PASSWORD):
                token = _create_session("student")
                return self._json_with_cookie({"ok": True, "role": "student"}, "session", token)

            # Record failed attempt for rate limiting
            _record_failed_login(client_ip)
            return self._json({"error": "Mot de passe incorrect"}, 401)

        if path == "/api/logout":
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.end_headers()
            return

        if not self._require_auth():
            return

        role = self._get_role()

        if path == "/api/eleves":
            body = self._read_body()
            nom = (body.get("nom") or "").strip()
            if not nom:
                return self._json({"error": "Le nom est obligatoire."}, 400)
            try:
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
            except Exception as exc:
                print(f"[ERROR] save_eleve: {exc}")
                return self._json({"error": str(exc)}, 500)

        if path == "/api/eleves/delete-multiple":
            body = self._read_body()
            ids = body.get("ids", [])
            if not ids:
                return self._json({"error": "Aucun ID fourni"}, 400)
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                count = db.delete_multiple_eleves(ids)
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            return self._json({"ok": True, "deleted": count})

        if path.startswith("/api/eleves/") and path.endswith("/duplicate"):
            try:
                eid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            try:
                result = db.duplicate_eleve(eid)
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            if result is None:
                return self._json({"error": "Calcul introuvable"}, 404)
            return self._json(result, 201)

        self.send_error(404)

    def do_DELETE(self) -> None:
        try:
            self._handle_delete()
        except Exception as exc:
            print(f"[FATAL] do_DELETE: {exc}")
            try:
                self._json({"error": str(exc)}, 500)
            except Exception:
                self.send_error(500)

    def _handle_delete(self) -> None:
        if not self._require_auth():
            return

        role = self._get_role()

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/eleves/clear":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                db.clear_all()
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            return self._json({"ok": True})

        if path.startswith("/api/eleves/"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                eid = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            try:
                db.delete_eleve(eid)
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            return self._json({"ok": True})

        self.send_error(404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._cors_header())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_PUT(self) -> None:
        try:
            self._handle_put()
        except Exception as exc:
            print(f"[FATAL] do_PUT: {exc}")
            try:
                self._json({"error": str(exc)}, 500)
            except Exception:
                self.send_error(500)

    def _handle_put(self) -> None:
        if not self._require_auth():
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/eleves/"):
            try:
                eid = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            body = self._read_body()
            nom = (body.get("nom") or "").strip()
            if not nom:
                return self._json({"error": "Le nom est obligatoire."}, 400)
            try:
                result = db.update_eleve(
                    eid,
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
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            if result is None:
                return self._json({"error": "Calcul introuvable"}, 404)
            return self._json(result)

        self.send_error(404)


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
