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
import urllib.request
import urllib.parse
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

# Twilio SMS config
_TWILIO_SID: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_FROM: str = os.environ.get("TWILIO_FROM", "")
_TWILIO_TO: str = os.environ.get("TWILIO_TO", "")

# Resend email config
_EMAIL_API_KEY: str = os.environ.get("RESEND_API_KEY", "")
_EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "SAPHIR Pro <onboarding@resend.dev>")
_EMAIL_TO: str = os.environ.get("EMAIL_TO", "")

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


def _send_login_sms(role: str, ip: str, email: str) -> None:
    """Send WhatsApp message via Twilio on successful login (non-blocking)."""
    if not all([_TWILIO_SID, _TWILIO_TOKEN, _TWILIO_FROM, _TWILIO_TO]):
        return
    now = time.strftime("%d/%m/%Y %H:%M:%S")
    role_label = "Administrateur" if role == "admin" else "Eleve"
    body = f"*[SAPHIR Pro] Connexion detectee*\nRole: {role_label}\nIP: {ip}\nDate: {now}"
    if email:
        body += f"\nEmail: {email}"
    wa_from = "whatsapp:+14155238886"
    wa_to = "whatsapp:" + _TWILIO_TO
    data = urllib.parse.urlencode({"From": wa_from, "To": wa_to, "Body": body}).encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{_TWILIO_SID}/Messages.json",
        data=data,
        method="POST",
    )
    req.add_header("Authorization", "Basic " + __import__("base64").b64encode(f"{_TWILIO_SID}:{_TWILIO_TOKEN}".encode()).decode())
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[TWILIO] WhatsApp sent to {_TWILIO_TO} for {role} login from {ip}")
    except Exception as e:
        print(f"[TWILIO ERROR] {e}")


def _send_login_email(role: str, ip: str, email: str) -> None:
    """Send email via Resend API on successful login (non-blocking)."""
    if not all([_EMAIL_API_KEY, _EMAIL_TO]):
        return
    now = time.strftime("%d/%m/%Y %H:%M:%S")
    role_label = "Administrateur" if role == "admin" else "Élève"
    subject = f"[SAPHIR Pro] Connexion {role_label}"
    body = f"Nouvelle connexion détectée sur SAPHIR Pro\n\nRôle : {role_label}\nIP : {ip}\nDate : {now}"
    if email:
        body += f"\nEmail : {email}"
    payload = json.dumps({"from": _EMAIL_FROM, "to": [_EMAIL_TO], "subject": subject, "text": body}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {_EMAIL_API_KEY}")
    req.add_header("Content-Type", "application/json")
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[EMAIL] Sent to {_EMAIL_TO} for {role} login from {ip}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


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

    def _get_real_ip(self) -> str:
        """Get real client IP from X-Forwarded-For or X-Real-IP headers."""
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = self.headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip.strip()
        return self.client_address[0]

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

        if path == "/api/notifications":
            return self._json(db.list_notifications())

        if path == "/api/notifications/unread":
            return self._json({"count": db.count_unread_notifications()})

        if path.startswith("/api/notifications/") and path.endswith("/read"):
            try:
                nid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            db.mark_notification_read(nid)
            return self._json({"ok": True})

        if path == "/api/notifications/read-all":
            db.mark_all_notifications_read()
            return self._json({"ok": True})

        if path == "/api/test-email":
            if not all([_EMAIL_API_KEY, _EMAIL_TO]):
                return self._json({"ok": False, "error": "Email vars not set", "api_key": bool(_EMAIL_API_KEY), "to": bool(_EMAIL_TO)})
            try:
                payload = json.dumps({"from": _EMAIL_FROM, "to": [_EMAIL_TO], "subject": "[SAPHIR Pro] Test email", "text": "Test SAPHIR Pro - Email fonctionne !"}).encode()
                req = urllib.request.Request("https://api.resend.com/emails", data=payload, method="POST")
                req.add_header("Authorization", f"Bearer {_EMAIL_API_KEY}")
                req.add_header("Content-Type", "application/json")
                resp = urllib.request.urlopen(req, timeout=10)
                return self._json({"ok": True, "message": "Email envoye avec succes"})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})

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

        if path == "/api/debug":
            turso_on = db._turso_enabled()
            turso_url = db._TURSO_URL[:40] + "..." if db._TURSO_URL else ""
            count = db.count_eleves()
            # Test actual Turso connectivity
            turso_status = "unknown"
            turso_error = ""
            if turso_on:
                try:
                    test = db._turso_exec("SELECT 1 as ok")
                    if test:
                        turso_status = "connected"
                    else:
                        turso_status = "query_failed"
                except Exception as e:
                    turso_status = "error"
                    turso_error = str(e)
            # Test table existence
            tables_exist = False
            if turso_on:
                try:
                    t = db._turso_exec("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('eleves','resultats')")
                    tables_exist = len(t) >= 2
                except Exception:
                    pass
            return self._json({
                "turso_enabled": turso_on,
                "turso_url": turso_url,
                "turso_status": turso_status,
                "turso_error": turso_error,
                "tables_exist": tables_exist,
                "eleves_count": count,
                "render_env": bool(os.environ.get("PORT")),
            })

        if path == "/api/init-tables":
            if not db._turso_enabled():
                return self._json({"error": "Turso not enabled"})
            db.init_db()
            test = db._turso_exec("SELECT COUNT(*) as n FROM eleves")
            return self._json({"ok": True, "count": test[0]["n"] if test else -1})

        if path == "/api/sessions":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            return self._json(db.list_sessions())

        if path == "/api/activity-logs":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            return self._json(db.list_activity_logs())

        if path.startswith("/api/sessions/") and path.endswith("/activities"):
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                sid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            return self._json(db.get_session_activities(sid))

        if path == "/api/annees-scolaires":
            return self._json(db.list_annees_scolaires())

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
            email = body.get("email", "")

            if not _auth_required():
                if _STUDENT_PASSWORD and _hash_password(pwd) == _hash_password(_STUDENT_PASSWORD):
                    token = _create_session("student")
                    ip = self._get_real_ip()
                    ua = self.headers.get("User-Agent", "")
                    sid = db.create_session("student", ip, ua, email)
                    threading.Thread(target=_send_login_sms, args=("student", ip, email), daemon=True).start()
                    threading.Thread(target=_send_login_email, args=("student", ip, email), daemon=True).start()
                    return self._json_with_cookie({"ok": True, "role": "student", "session_id": sid}, "session", token)
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("admin", ip, ua, email)
                threading.Thread(target=_send_login_sms, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("admin", ip, email), daemon=True).start()
                return self._json({"ok": True, "role": "admin", "session_id": sid})

            if _hash_password(pwd) == _hash_password(_APP_PASSWORD):
                token = _create_session("admin")
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("admin", ip, ua, email)
                threading.Thread(target=_send_login_sms, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("admin", ip, email), daemon=True).start()
                return self._json_with_cookie({"ok": True, "role": "admin", "session_id": sid}, "session", token)

            if _STUDENT_PASSWORD and _hash_password(pwd) == _hash_password(_STUDENT_PASSWORD):
                token = _create_session("student")
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("student", ip, ua, email)
                threading.Thread(target=_send_login_sms, args=("student", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("student", ip, email), daemon=True).start()
                return self._json_with_cookie({"ok": True, "role": "student", "session_id": sid}, "session", token)

            # Record failed attempt for rate limiting
            _record_failed_login(client_ip)
            return self._json({"error": "Mot de passe incorrect"}, 401)

        if path == "/api/logout":
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.end_headers()
            return

        if path == "/api/notifications":
            body = self._read_body()
            titre = body.get("titre", "")
            message = body.get("message", "")
            ntype = body.get("type", "info")
            nid = db.add_notification(titre, message, ntype)
            return self._json({"ok": True, "id": nid}, 201)

        if path == "/api/notifications/clear":
            db.clear_all_notifications()
            return self._json({"ok": True})

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
                    annee_scolaire=body.get("annee_scolaire", ""),
                )
                if result.get("error"):
                    return self._json(result, 500)
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

        if path == "/api/log-activity":
            body = self._read_body()
            action = body.get("action", "")
            module = body.get("module", "")
            detail = body.get("detail", "")
            resultat = body.get("resultat", "succes")
            session_id = body.get("session_id", 0)
            role = self._get_role()
            try:
                db.log_activity(session_id, role, action, module, detail, resultat)
            except Exception as exc:
                print(f"[ERROR] log_activity: {exc}")
            return self._json({"ok": True})

        if path == "/api/sessions/create":
            body = self._read_body()
            role = body.get("role", self._get_role())
            ip = self._get_real_ip()
            ua = self.headers.get("User-Agent", "")
            sid = db.create_session(role, ip, ua)
            return self._json({"ok": True, "session_id": sid})

        if path.startswith("/api/sessions/") and path.endswith("/close"):
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                sid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            db.close_session(sid)
            return self._json({"ok": True})

        if path == "/api/archive":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            body = self._read_body()
            annee = body.get("annee_scolaire", "")
            if not annee:
                return self._json({"error": "Année scolaire requise"}, 400)
            count = db.archive_eleves(annee)
            db.add_notification("Archivage", f"{count} calcul(s) archivé(s) pour {annee}", "info")
            return self._json({"ok": True, "archived": count})

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

        if path == "/api/sessions/clear":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                db.clear_sessions()
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)
            return self._json({"ok": True})

        if path.startswith("/api/sessions/"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                sid = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            try:
                db.delete_session(sid)
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
                    annee_scolaire=body.get("annee_scolaire", ""),
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
