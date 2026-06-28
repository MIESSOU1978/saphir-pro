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
import re
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
_EMAIL_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW = 300  # 5 minutes
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')

# ── SSE (Server-Sent Events) ──
import queue
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()
_sse_event_counter = 0

def _sse_emit(event_type: str, data: dict) -> None:
    """Push an event to all connected SSE clients."""
    global _sse_event_counter
    _sse_event_counter += 1
    payload = json.dumps({"id": _sse_event_counter, "type": event_type, **data})
    dead = []
    with _sse_lock:
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _sse_clients.remove(q)
            except ValueError:
                pass

# Twilio SMS config
_TWILIO_SID: str = os.environ.get("TWILIO_ACCOUNT_SID", "")
_TWILIO_TOKEN: str = os.environ.get("TWILIO_AUTH_TOKEN", "")
_TWILIO_FROM: str = os.environ.get("TWILIO_FROM", "")
_TWILIO_TO: str = os.environ.get("TWILIO_TO", "")

# Email config (SendGrid API)
_EMAIL_API_KEY: str = os.environ.get("SENDGRID_API_KEY", "")
_EMAIL_FROM: str = os.environ.get("EMAIL_FROM", "miessou8@gmail.com")
_EMAIL_TO: str = os.environ.get("EMAIL_TO", "")

# Security: PBKDF2-HMAC-SHA256 salt — new derived + legacy fallback
def _derive_salt() -> bytes:
    """Derive a deterministic salt from app secrets (changes when passwords change)."""
    combined = (_APP_PASSWORD + _STUDENT_PASSWORD + "calcmo-saphir-pro-v2").encode()
    return hashlib.sha256(combined).digest()[:16]

_PASSWORD_SALT = _derive_salt()
_PASSWORD_SALT_LEGACY = b"calcmo-saphir-pro-salt-2024-v2"


def _hash_password(pwd: str, salt: bytes = None) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 (100k iterations)."""
    s = salt or _PASSWORD_SALT
    return hashlib.pbkdf2_hmac("sha256", pwd.encode(), s, 100000).hex()

def _verify_password(pwd: str, stored_hash: str) -> bool:
    """Verify password against stored hash, trying both new and legacy salt."""
    if hmac.compare_digest(_hash_password(pwd), stored_hash):
        return True
    return hmac.compare_digest(_hash_password(pwd, _PASSWORD_SALT_LEGACY), stored_hash)


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


def _is_rate_limited(ip: str, email: str = "") -> tuple[bool, int]:
    """Check if IP or email has exceeded login rate limit. Returns (limited, attempt_count)."""
    now = time.time()
    # Clean old IP entries
    _LOGIN_ATTEMPTS[ip] = [
        t for t in _LOGIN_ATTEMPTS[ip] if now - t < _LOGIN_WINDOW
    ]
    # Clean old email entries
    if email:
        _EMAIL_ATTEMPTS[email] = [
            t for t in _EMAIL_ATTEMPTS[email] if now - t < _LOGIN_WINDOW
        ]
        email_count = len(_EMAIL_ATTEMPTS[email])
    else:
        email_count = 0
    ip_count = len(_LOGIN_ATTEMPTS[ip])
    if ip_count >= _MAX_LOGIN_ATTEMPTS:
        return True, max(ip_count, email_count)
    if email and email_count >= _MAX_LOGIN_ATTEMPTS:
        return True, email_count
    return False, max(ip_count, email_count)


def _record_failed_login(ip: str, email: str = "") -> int:
    """Record a failed login attempt for rate limiting. Returns current count."""
    _LOGIN_ATTEMPTS[ip].append(time.time())
    if email:
        _EMAIL_ATTEMPTS[email].append(time.time())
        return len(_EMAIL_ATTEMPTS[email])
    return len(_LOGIN_ATTEMPTS[ip])


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
    """Send email via SendGrid API on successful login (non-blocking)."""
    if not all([_EMAIL_API_KEY, _EMAIL_TO]):
        return
    now = time.strftime("%d/%m/%Y %H:%M:%S")
    role_label = "Administrateur" if role == "admin" else "Élève"
    subject = f"[SAPHIR Pro] Connexion {role_label}"
    body = f"Nouvelle connexion détectée sur SAPHIR Pro\n\nRôle : {role_label}\nIP : {ip}\nDate : {now}"
    if email:
        body += f"\nEmail : {email}"
    payload = json.dumps({
        "personalizations": [{"to": [{"email": _EMAIL_TO}]}],
        "from": {"email": _EMAIL_FROM, "name": "SAPHIR Pro"},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}]
    }).encode()
    req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {_EMAIL_API_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        urllib.request.urlopen(req, timeout=10)
        print(f"[EMAIL] Sent to {_EMAIL_TO} for {role} login from {ip}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


def _device_fingerprint(ua: str) -> str:
    """Generate a device fingerprint from User-Agent string (matches frontend FNV-1a)."""
    h = 0x811c9dc5
    for c in ua:
        h ^= ord(c)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, '08x')[:16]


def _parse_user_agent(ua: str) -> dict:
    """Parse User-Agent string to extract OS, browser, device type."""
    os_name = "Inconnu"
    browser = "Inconnu"
    device = "Ordinateur"
    ua_lower = ua.lower()
    if "windows" in ua_lower: os_name = "Windows"
    elif "mac os" in ua_lower or "macos" in ua_lower: os_name = "macOS"
    elif "linux" in ua_lower: os_name = "Linux"
    elif "android" in ua_lower: os_name = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower: os_name = "iOS"
    if "edg/" in ua_lower: browser = "Edge"
    elif "chrome" in ua_lower and "edg" not in ua_lower: browser = "Chrome"
    elif "firefox" in ua_lower: browser = "Firefox"
    elif "safari" in ua_lower and "chrome" not in ua_lower: browser = "Safari"
    if "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        device = "Téléphone"
    elif "ipad" in ua_lower or "tablet" in ua_lower:
        device = "Tablette"
    return {"os": os_name, "browser": browser, "device": device}


def _check_unknown_device(ua: str, ip: str, email: str, role: str) -> None:
    """Check if login is from an unknown device. Register it and notify admin."""
    fp = _device_fingerprint(ua)
    if not db.is_device_known(fp):
        info = _parse_user_agent(ua)
        label = f"{info['device']} — {info['os']} / {info['browser']}"
        db.add_known_device(fp, label, trusted=0)
        now = time.strftime("%d/%m/%Y %H:%M:%S")
        msg = (
            f"Nouvel appareil détecté.\n"
            f"Role: {role}\nAppareil: {info['device']} | {info['os']}\n"
            f"Navigateur: {info['browser']}\nIP: {ip}\nDate: {now}"
        )
        if email:
            msg += f"\nEmail: {email}"
        db.add_notification("Nouvel appareil", msg, "warning")
        _sse_emit("unknown_device", {"message": "Nouvel appareil détecté", "user": email, "device": label, "ip": ip})


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

    def _security_headers(self) -> None:
        """Add common security headers."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        if _IS_RENDER:
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", self._cors_header())
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_with_cookie(self, data: object, cookie_name: str, cookie_val: str, status: int = 200, extra_cookies: dict | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        secure = "; Secure" if _IS_RENDER else ""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", self._cors_header())
        self.send_header("Set-Cookie", f"{cookie_name}={cookie_val}; Path=/; HttpOnly; SameSite=Lax{secure}; Max-Age={_SESSION_TTL}")
        if extra_cookies:
            for name, val in extra_cookies.items():
                self.send_header("Set-Cookie", f"{name}={val}; Path=/; SameSite=Lax{secure}; Max-Age={_SESSION_TTL}")
        self._security_headers()
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
        self._security_headers()
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
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, path: Path, status: int = 200) -> None:
        body = path.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self._security_headers()
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

    def _get_email(self) -> str:
        """Get email from the current session."""
        token = self._get_cookie("session")
        if not token:
            return ""
        try:
            parts = token.split("|")
            if len(parts) >= 2:
                expiry = int(parts[1])
                if expiry < int(time.time()):
                    return ""
        except (ValueError, IndexError):
            return ""
        # Find session by looking at the cookie session ID
        sid = self._get_cookie("saphir_session_id")
        if not sid:
            return ""
        try:
            sessions = db.list_sessions()
            for s in sessions:
                if str(s.get("id")) == str(sid):
                    return s.get("email", "")
        except Exception:
            pass
        return ""

    def _extract_session_token(self) -> str | None:
        """Extract session token from cookie."""
        return self._get_cookie("session")

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
                self._json({"error": "Erreur interne du serveur"}, 500)
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

        # ── SSE endpoint (admin only, before auth check) ──
        if path == "/api/admin/events/stream":
            token = self._extract_session_token()
            role = _get_session_role(token) if token else None
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            # Set SSE headers
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._send_cors_headers()
            self.end_headers()
            # Create client queue
            client_q = queue.Queue(maxsize=100)
            with _sse_lock:
                _sse_clients.append(client_q)
            try:
                # Send initial keepalive
                self.wfile.write(b": ok\n\n")
                self.wfile.flush()
                while True:
                    try:
                        data = client_q.get(timeout=30)
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        # Send keepalive comment
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _sse_lock:
                    try:
                        _sse_clients.remove(client_q)
                    except ValueError:
                        pass
            return

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

        # ── Messages ──
        if path == "/api/messages" or path == "/api/messages/unread":
            is_unread = path.endswith("/unread")
            email = ""
            qs = parsed.query
            if qs:
                for p in qs.split("&"):
                    if p.startswith("email="):
                        email = urllib.parse.unquote(p.split("=", 1)[1])
            if not email:
                email = self._get_email()
            if not email:
                if is_unread:
                    return self._json({"count": 0})
                return self._json([], 401)
            if is_unread:
                return self._json({"count": db.count_unread_messages(email)})
            msgs = db.get_messages(email)
            return self._json(msgs)

        if path.startswith("/api/messages/") and path.endswith("/read"):
            try:
                nid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            db.mark_message_read(nid)
            return self._json({"ok": True})

        if path == "/api/messages/read-all":
            email = self._get_email()
            if not email:
                return self._json({"error": "Non authentifié"}, 401)
            db.mark_all_read(email)
            return self._json({"ok": True})

        if path == "/api/test-email":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            if not all([_EMAIL_API_KEY, _EMAIL_TO]):
                return self._json({"ok": False, "error": "Variables email non configurées"})
            try:
                payload = json.dumps({
                    "personalizations": [{"to": [{"email": _EMAIL_TO}]}],
                    "from": {"email": _EMAIL_FROM, "name": "SAPHIR Pro"},
                    "subject": "[SAPHIR Pro] Test email",
                    "content": [{"type": "text/plain", "value": "Test SAPHIR Pro - SendGrid fonctionne !"}]
                }).encode()
                req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=payload, method="POST")
                req.add_header("Authorization", f"Bearer {_EMAIL_API_KEY}")
                req.add_header("Content-Type", "application/json")
                req.add_header("User-Agent", "Mozilla/5.0")
                resp = urllib.request.urlopen(req, timeout=10)
                return self._json({"ok": True, "message": "Email envoye avec succes"})
            except Exception as e:
                return self._json({"ok": False, "error": "Erreur interne du serveur"})

        if not self._require_auth():
            return

        if path == "" or path == "/index.html":
            return self._html(_html_path)

        if path == "/api/role":
            return self._json({"role": self._get_role(), "email": self._get_email()})

        if path == "/api/login-attempts":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                return self._json(db.list_login_attempts())
            except Exception as exc:
                return self._json([], 500)

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
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
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
                "turso_status": turso_status,
                "tables_exist": tables_exist,
                "eleves_count": count,
                "render_env": bool(os.environ.get("PORT")),
            })

        if path == "/api/init-tables":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
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

        if path == "/api/known-devices":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            return self._json(db.list_known_devices())

        self.send_error(404)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        try:
            self._handle_post()
        except Exception as exc:
            print(f"[FATAL] do_POST: {exc}")
            try:
                self._json({"error": "Erreur interne du serveur"}, 500)
            except Exception:
                self.send_error(500)

    def _handle_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/login":
            client_ip = self.client_address[0]
            body = self._read_body()
            pwd = body.get("password", "")
            email = (body.get("email") or "").strip().lower()

            # ── Email is mandatory ──
            if not email:
                return self._json({"error": "Veuillez saisir votre adresse e-mail."}, 400)

            # ── Email format validation ──
            if not _EMAIL_RE.match(email):
                return self._json({"error": "Veuillez saisir une adresse e-mail valide."}, 400)

            # ── Password is mandatory ──
            if not pwd:
                return self._json({"error": "Veuillez entrer le mot de passe."}, 400)

            # ── Rate limiting (IP + email) ──
            limited, attempt_count = _is_rate_limited(client_ip, email)
            if limited:
                return self._json({"error": "Trop de tentatives. Réessayez dans 5 minutes.", "attempts": attempt_count}, 429)

            # ── Banned check ──
            if db.is_user_banned(email):
                return self._json({"error": "Votre compte a été désactivé. Veuillez contacter l'administrateur."}, 403)

            if not _auth_required():
                if _STUDENT_PASSWORD and _verify_password(pwd, _hash_password(_STUDENT_PASSWORD)):
                    token = _create_session("student")
                    ip = self._get_real_ip()
                    ua = self.headers.get("User-Agent", "")
                    sid = db.create_session("student", ip, ua, email)
                    threading.Thread(target=_send_login_sms, args=("student", ip, email), daemon=True).start()
                    threading.Thread(target=_send_login_email, args=("student", ip, email), daemon=True).start()
                    threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "student"), daemon=True).start()
                    _sse_emit("login_success", {"message": "Nouvelle connexion détectée", "user": email, "role": "student", "ip": ip})
                    return self._json_with_cookie({"ok": True, "role": "student", "session_id": sid}, "session", token, extra_cookies={"saphir_session_id": str(sid)})
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("admin", ip, ua, email)
                threading.Thread(target=_send_login_sms, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "admin"), daemon=True).start()
                _sse_emit("login_success", {"message": "Nouvelle connexion détectée", "user": email, "role": "admin", "ip": ip})
                return self._json({"ok": True, "role": "admin", "session_id": sid})

            if _verify_password(pwd, _hash_password(_APP_PASSWORD)):
                token = _create_session("admin")
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("admin", ip, ua, email)
                threading.Thread(target=_send_login_sms, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "admin"), daemon=True).start()
                _sse_emit("login_success", {"message": "Nouvelle connexion détectée", "user": email, "role": "admin", "ip": ip})
                return self._json_with_cookie({"ok": True, "role": "admin", "session_id": sid}, "session", token, extra_cookies={"saphir_session_id": str(sid)})

            if _STUDENT_PASSWORD and _verify_password(pwd, _hash_password(_STUDENT_PASSWORD)):
                token = _create_session("student")
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("student", ip, ua, email)
                threading.Thread(target=_send_login_sms, args=("student", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("student", ip, email), daemon=True).start()
                threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "student"), daemon=True).start()
                _sse_emit("login_success", {"message": "Nouvelle connexion détectée", "user": email, "role": "student", "ip": ip})
                return self._json_with_cookie({"ok": True, "role": "student", "session_id": sid}, "session", token, extra_cookies={"saphir_session_id": str(sid)})

            # ── Failed attempt ──
            count = _record_failed_login(client_ip, email)
            email_count = len(_EMAIL_ATTEMPTS.get(email, []))
            ip_count = len(_LOGIN_ATTEMPTS.get(client_ip, []))
            msg = "Adresse e-mail ou mot de passe incorrect."
            raison = "Mot de passe incorrect"
            if count >= 5:
                msg = "Trop de tentatives. Réessayez dans 5 minutes."
                raison = "Trop de tentatives échouées"
            elif count >= 3:
                msg = "Plusieurs tentatives de connexion échouées ont été détectées pour cette adresse e-mail."
            # Determine alert level
            if count >= 5 or db.is_user_banned(email):
                niveau = "critique"
            elif count >= 3:
                niveau = "suspect"
            else:
                niveau = "normal"
            # Get device info from User-Agent
            ua = self.headers.get("User-Agent", "")
            info = _parse_user_agent(ua)
            # Get city from IP
            ville = ""
            if client_ip and client_ip not in ("127.0.0.1", "::1", ""):
                try:
                    req = urllib.request.Request(f"https://ip-api.com/json/{client_ip}?fields=city,country")
                    with urllib.request.urlopen(req, timeout=3) as resp:
                        geo = json.loads(resp.read())
                        city = geo.get("city", "")
                        country = geo.get("country", "")
                        country = "Côte d'Ivoire" if country in ("Ivory Coast", "Ivoiry Coast") else country
                        ville = f"{city}, {country}" if city else country
                except Exception:
                    pass
            # Record in login_attempts table
            try:
                db.save_login_attempt(
                    email=email, ip=client_ip, ville=ville,
                    appareil=info.get("device", ""), os_name=info.get("os", ""),
                    navigateur=info.get("browser", ""), raison=raison, niveau=niveau,
                )
            except Exception as exc:
                print(f"[ERROR] save_login_attempt: {exc}")
            # Save detailed notification
            try:
                detail_data = json.dumps({
                    "user": email, "ip": client_ip, "ville": ville,
                    "appareil": info.get("device", ""), "os": info.get("os", ""),
                    "navigateur": info.get("browser", ""), "raison": raison,
                    "niveau": niveau, "email_count": email_count + 1, "ip_count": ip_count + 1,
                }, ensure_ascii=False)
                db.add_notification("Tentative de connexion échouée", detail_data, "error")
            except Exception as exc:
                print(f"[ERROR] add_notification login_failed: {exc}")
            _sse_emit("login_failed", {
                "message": "Tentative de connexion échouée",
                "user": email, "ip": client_ip, "ville": ville,
                "appareil": info.get("device", ""), "os": info.get("os", ""),
                "navigateur": info.get("browser", ""), "raison": raison,
                "niveau": niveau, "email_count": email_count + 1, "ip_count": ip_count + 1,
            })
            return self._json({"error": msg, "attempts": count}, 401)

        if path == "/api/logout":
            try:
                body = self._read_body()
                sess_id = body.get("session_id")
                if sess_id:
                    db.close_session(int(sess_id))
                    _sse_emit("logout", {"message": "Déconnexion utilisateur", "session_id": int(sess_id)})
            except Exception:
                pass
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.end_headers()
            return

        if path == "/api/notifications":
            try:
                body = self._read_body()
                titre = body.get("titre", "")
                message = body.get("message", "")
                ntype = body.get("type", "info")
                nid = db.add_notification(titre, message, ntype)
                return self._json({"ok": True, "id": nid}, 201)
            except Exception as exc:
                print(f"[ERROR] POST /api/notifications: {exc}")
                import traceback; traceback.print_exc()
                return self._json({"error": "Erreur interne du serveur"}, 500)

        if path == "/api/notifications/clear":
            db.clear_all_notifications()
            return self._json({"ok": True})

        # ── Send message to user (admin only) ──
        if path == "/api/messages/send":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                body = self._read_body()
                recipient = body.get("recipient", "").strip()
                message = body.get("message", "").strip()
                if not recipient or not message:
                    return self._json({"error": "Destinataire et message requis"}, 400)
                sender = "admin"
                mid = db.send_message(sender, recipient, message)
                print(f"[MSG] sent id={mid} from={sender} to={recipient}")
                _sse_emit("new_message", {"id": mid, "sender": sender, "recipient": recipient, "message": message})
                return self._json({"ok": True, "id": mid}, 201)
            except Exception as exc:
                print(f"[MSG ERROR] {exc}")
                import traceback; traceback.print_exc()
                return self._json({"error": "Erreur interne du serveur"}, 500)

        # ── CHECK BANNED (must be before auth) ──
        if path == "/api/check-banned":
            body = self._read_body()
            email = body.get("email", "").strip().lower()
            if not email:
                return self._json({"banned": False})
            try:
                banned = db.is_user_banned(email)
            except Exception:
                banned = False
            return self._json({"banned": banned})

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
                return self._json({"error": "Erreur interne du serveur"}, 500)

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
                return self._json({"error": "Erreur interne du serveur"}, 500)
            return self._json({"ok": True, "deleted": count})

        if path.startswith("/api/eleves/") and path.endswith("/duplicate"):
            try:
                eid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            try:
                result = db.duplicate_eleve(eid)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
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

        if path == "/api/known-devices":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            body = self._read_body()
            fp = (body.get("fingerprint") or "").strip()
            label = (body.get("label") or "").strip()
            trusted = body.get("trusted", 1)
            if not fp:
                return self._json({"error": "fingerprint requis"}, 400)
            nid = db.add_known_device(fp, label, trusted)
            return self._json({"ok": True, "id": nid}, 201)

        if path.startswith("/api/known-devices/") and path.endswith("/trust"):
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                did = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            db.trust_device(did)
            _sse_emit("device_trusted", {"message": "Appareil marqué comme fiable", "device_id": did})
            return self._json({"ok": True})

        if path.startswith("/api/known-devices/") and path.endswith("/untrust"):
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                did = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            db.untrust_device(did)
            _sse_emit("device_removed", {"message": "Appareil retiré des appareils fiables", "device_id": did})
            return self._json({"ok": True})

        self.send_error(404)

    def do_DELETE(self) -> None:
        try:
            self._handle_delete()
        except Exception as exc:
            print(f"[FATAL] do_DELETE: {exc}")
            try:
                self._json({"error": "Erreur interne du serveur"}, 500)
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
                return self._json({"error": "Erreur interne du serveur"}, 500)
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
                return self._json({"error": "Erreur interne du serveur"}, 500)
            return self._json({"ok": True})

        if path == "/api/sessions/clear":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                db.clear_sessions()
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("sessions_cleared", {"message": "Historique des sessions vidé"})
            return self._json({"ok": True})

        # ── DISCONNECT USER ──
        if path.startswith("/api/sessions/") and path.endswith("/disconnect"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                sid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            try:
                db.close_session(sid)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("session_kicked", {"message": "Session déconnectée par l'administrateur", "session_id": sid})
            return self._json({"ok": True})

        # ── BAN USER ──
        if path.startswith("/api/sessions/") and path.endswith("/ban"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                sid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            body = self._read_body()
            email = body.get("email", "").strip().lower()
            motif = body.get("motif", "")
            if not email:
                return self._json({"error": "Email requis"}, 400)
            try:
                sessions = db.list_sessions()
                target = next((s for s in sessions if s["id"] == sid), None)
                if not target:
                    return self._json({"error": "Session introuvable"}, 404)
                admin_email = target.get("email", "admin")
                db.ban_user(email, banned_by=admin_email, motif=motif)
                db.close_session(sid)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("user_banned", {"message": "Utilisateur banni", "user": email, "motif": motif})
            return self._json({"ok": True})

        # ── UNBAN USER ──
        if path.startswith("/api/banned-users/") and path.endswith("/unban"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            email = path.split("/")[-2]
            try:
                db.unban_user(email)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("user_unbanned", {"message": "Utilisateur débanni", "user": email})
            return self._json({"ok": True})

        # ── LIST BANNED USERS ──
        if path == "/api/banned-users":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                banned = db.list_banned_users()
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            return self._json(banned)

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
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("session_deleted", {"message": "Session supprimée", "session_id": sid})
            return self._json({"ok": True})

        if path.startswith("/api/known-devices/"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                did = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            db.delete_known_device(did)
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
                self._json({"error": "Erreur interne du serveur"}, 500)
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
                return self._json({"error": "Erreur interne du serveur"}, 500)
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
