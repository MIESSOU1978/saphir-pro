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

# Email config (Gmail SMTP)
_EMAIL_HOST: str = os.environ.get("EMAIL_HOST", "")
_EMAIL_PORT: int = int(os.environ.get("EMAIL_PORT", "587"))
_EMAIL_USER: str = os.environ.get("EMAIL_USER", "")
_EMAIL_PASS: str = os.environ.get("EMAIL_PASSWORD", "")
_MAILGUN_DOMAIN: str = os.environ.get("MAILGUN_DOMAIN", "")


def _mailgun_send(to: str, subject: str, body: str) -> None:
    """Send email via Mailgun REST API (HTTPS, port 443 — works on Render free tier)."""
    if not _EMAIL_PASS or not _MAILGUN_DOMAIN:
        print("[EMAIL] Skip — EMAIL_PASSWORD or MAILGUN_DOMAIN not set")
        return
    import base64
    url = f"https://api.mailgun.net/v3/{_MAILGUN_DOMAIN}/messages"
    payload = urllib.parse.urlencode({
        "from": f"SAPHIR Pro <{_EMAIL_USER}>",
        "to": to,
        "subject": subject,
        "text": body,
    }).encode()
    auth = base64.b64encode(f"api:{_EMAIL_PASS}".encode()).decode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        print(f"[EMAIL] Sent to {to} — {resp.status}")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")

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


def _record_success_login(handler, email: str, ip: str, role: str) -> None:
    """Record a successful login in login_attempts table."""
    ua = handler.headers.get("User-Agent", "")
    info = db._parse_user_agent(ua)
    ville = ""
    if ip and ip not in ("127.0.0.1", "::1", ""):
        try:
            req = urllib.request.Request(f"https://ip-api.com/json/{ip}?fields=city,country")
            with urllib.request.urlopen(req, timeout=3) as resp:
                geo = json.loads(resp.read())
                city = geo.get("city", "")
                country = geo.get("country", "")
                country = "Côte d'Ivoire" if country in ("Ivory Coast", "Ivory Coast") else country
                ville = f"{city}, {country}" if city else country
        except Exception:
            pass
    try:
        db.save_login_attempt(
            email=email, ip=ip, ville=ville,
            appareil=info.get("appareil", ""), os_name=info.get("os", ""),
            navigateur=info.get("navigateur", ""), raison="Connexion réussie", niveau="normal",
        )
    except Exception as exc:
        print(f"[ERROR] save_login_attempt success: {exc}")


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
    """Send email via Mailgun API on successful login (non-blocking)."""
    now = time.strftime("%d/%m/%Y %H:%M:%S")
    role_label = "Administrateur" if role == "admin" else "Élève"
    subject = f"[SAPHIR Pro] Connexion {role_label}"
    body = f"Nouvelle connexion détectée sur SAPHIR Pro\n\nRôle : {role_label}\nIP : {ip}\nDate : {now}"
    if email:
        body += f"\nEmail : {email}"
    try:
        _mailgun_send(_EMAIL_USER, subject, body)
    except Exception as e:
        print(f"[EMAIL ERROR] _send_login_email: {e}")

def _send_failed_login_email(ip: str, email: str, ville: str, niveau: str, raison: str, count: int) -> None:
    """Send email via Mailgun API to user on failed login attempt (non-blocking)."""
    if not email:
        print("[EMAIL] Skip — no recipient address")
        return
    now = time.strftime("%d/%m/%Y %H:%M:%S")
    niveau_label = {"normal": "Normale", "suspect": "Suspecte", "critique": "Critique"}.get(niveau, niveau)
    subject = f"[SAPHIR Pro] Échec de connexion — {niveau_label}"
    body = (
        f"Bonjour,\n\n"
        f"Une tentative de connexion échouée a été détectée sur votre compte SAPHIR Pro.\n\n"
        f"Tentative n°{count}\n"
        f"Date : {now}\n"
        f"IP : {ip}\n"
        f"Ville : {ville or 'Inconnue'}\n"
        f"Niveau d'alerte : {niveau_label}\n"
        f"Raison : {raison}\n\n"
        f"Si ce n'était pas vous, veuillez contacter votre administrateur.\n"
        f"Si c'était vous, ignorez ce message.\n\n"
        f"---\n"
        f"SAPHIR Pro — Système d'aide à l'orientation scolaire"
    )
    try:
        _mailgun_send(email, subject, body)
    except Exception as e:
        print(f"[EMAIL ERROR] _send_failed_login_email: {e}")


def _device_fingerprint(ua: str, machine_fp: str = "") -> str:
    """Generate a stable device fingerprint.
    Combines client machine fingerprint (screen, tz, CPU) with UA if available.
    Falls back to UA-only if client fingerprint not provided."""
    if machine_fp:
        return machine_fp[:16]
    info = db._parse_user_agent(ua)
    key = f"{info['os']}|{info['navigateur']}|{info['appareil']}"
    h = 0x811c9dc5
    for c in key:
        h ^= ord(c)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, '08x')[:16]


def _check_unknown_device(ua: str, ip: str, email: str, role: str, machine_fp: str = "") -> None:
    """Check if login is from an unknown device. Register it and notify admin."""
    fp = _device_fingerprint(ua, machine_fp)
    info = db._parse_user_agent(ua)
    label = f"{info['appareil']} — {info['os']} / {info['navigateur']}"
    if db.is_device_known(fp):
        db.update_known_device_label(fp, label)
    else:
        db.add_known_device(fp, label, trusted=0)
        now = time.strftime("%d/%m/%Y %H:%M:%S")
        msg = (
            f"Nouvel appareil détecté.\n"
            f"Role: {role}\nAppareil: {info['appareil']} | {info['os']}\n"
            f"Navigateur: {info['navigateur']}\nIP: {ip}\nDate: {now}"
        )
        if email:
            msg += f"\nEmail: {email}"
        db.add_notification("Nouvel appareil : " + (email or "inconnu"), msg, "warning")
        _sse_emit("unknown_device", {"message": "Nouvel appareil : " + (email or "inconnu"), "user": email, "device": label, "ip": ip})


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
            return db.get_session_email(int(sid))
        except Exception:
            pass
        return ""

    def _get_session_id(self) -> int:
        """Get session ID from cookie."""
        sid = self._get_cookie("saphir_session_id")
        if not sid:
            return 0
        try:
            return int(sid)
        except (ValueError, TypeError):
            return 0

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

    def _check_eleve_access(self, eid: int) -> bool:
        """Return True if the current user may access the given eleve.
        Admins can access everything.  Students may only access their own records
        (matched by created_by == session email)."""
        if self._get_role() == "admin":
            return True
        row = db.get_eleve(eid)
        if row is None:
            return False
        return (row.get("created_by") or "") == (self._get_email() or "")

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
            self.send_header("Access-Control-Allow-Origin", self._cors_header())
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
            closed = set(db.get_closed_message_ids(email))
            for m in msgs:
                m["closed"] = m["id"] in closed
            return self._json(msgs)

        if path == "/api/messages/history":
            email = ""
            qs = parsed.query
            if qs:
                for p in qs.split("&"):
                    if p.startswith("email="):
                        email = urllib.parse.unquote(p.split("=", 1)[1])
            if not email:
                email = self._get_email()
            if not email:
                return self._json([], 401)
            msgs = db.get_messages(email)
            closed = set(db.get_closed_message_ids(email))
            for m in msgs:
                m["closed"] = m["id"] in closed
            return self._json(msgs)

        if path == "/api/test-email":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            if not _EMAIL_PASS or not _MAILGUN_DOMAIN:
                return self._json({"ok": False, "error": "EMAIL_PASSWORD ou MAILGUN_DOMAIN non configuré"})
            try:
                _mailgun_send(_EMAIL_USER, "[SAPHIR Pro] Test email", "Test SAPHIR Pro — Mailgun API fonctionne !")
                return self._json({"ok": True, "message": "Email envoyé avec succès"})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)})

        if not self._require_auth():
            return

        if path == "" or path == "/index.html":
            return self._html(_html_path)

        if path == "/api/role":
            return self._json({"role": self._get_role(), "email": self._get_email(), "session_id": self._get_session_id()})

        if path == "/api/login-attempts":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                return self._json(db.list_login_attempts())
            except Exception as exc:
                return self._json([], 500)

        if path == "/api/eleves":
            try:
                role = self._get_role()
                if role == "admin":
                    rows = db.list_eleves(include_deleted=True)
                    print(f"[API] GET /api/eleves → admin (incl. deleted) → {len(rows)} rows")
                    return self._json(rows)
                email = self._get_email()
                print(f"[API] GET /api/eleves → role={role} email={email!r}")
                if not email:
                    print(f"[API] GET /api/eleves → no email, returning empty")
                    return self._json([], 200)
                db.claim_legacy_eleves(email)
                rows = db.list_eleves(created_by=email, include_legacy=True)
                print(f"[API] GET /api/eleves → filtered by created_by={email!r} → {len(rows)} rows")
                return self._json(rows)
            except Exception as exc:
                print(f"[ERROR] list_eleves: {exc}")
                return self._json([], 500)

        if path.startswith("/api/eleves/"):
            try:
                eid = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            if not self._check_eleve_access(eid):
                return self._json({"error": "Accès refusé"}, 403)
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
            machine_fp = (body.get("machine_fp") or "").strip()
            ip = self._get_real_ip()
            ua = self.headers.get("User-Agent", "")
            session_fp = _device_fingerprint(ua, machine_fp)

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
                    sid = db.create_session("student", ip, ua, email, fingerprint=session_fp)
                    _record_success_login(self, email, ip, "student")
                    threading.Thread(target=_send_login_sms, args=("student", ip, email), daemon=True).start()
                    threading.Thread(target=_send_login_email, args=("student", ip, email), daemon=True).start()
                    threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "student", machine_fp), daemon=True).start()
                    db.add_notification("Connexion réussie", json.dumps({"user": email, "ip": ip, "role": "student"}, ensure_ascii=False), "success")
                    _sse_emit("login_success", {"message": "Nouvelle connexion : " + email, "user": email, "role": "student", "ip": ip})
                    return self._json_with_cookie({"ok": True, "role": "student", "session_id": sid}, "session", token, extra_cookies={"saphir_session_id": str(sid)})
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                token = _create_session("admin")
                sid = db.create_session("admin", ip, ua, email, fingerprint=session_fp)
                _record_success_login(self, email, ip, "admin")
                threading.Thread(target=_send_login_sms, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "admin", machine_fp), daemon=True).start()
                db.add_notification("Connexion réussie", json.dumps({"user": email, "ip": ip, "role": "admin"}, ensure_ascii=False), "success")
                _sse_emit("login_success", {"message": "Nouvelle connexion : " + email, "user": email, "role": "admin", "ip": ip})
                return self._json_with_cookie({"ok": True, "role": "admin", "session_id": sid}, "session", token, extra_cookies={"saphir_session_id": str(sid)})

            if _verify_password(pwd, _hash_password(_APP_PASSWORD)):
                token = _create_session("admin")
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("admin", ip, ua, email, fingerprint=session_fp)
                _record_success_login(self, email, ip, "admin")
                threading.Thread(target=_send_login_sms, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("admin", ip, email), daemon=True).start()
                threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "admin", machine_fp), daemon=True).start()
                db.add_notification("Connexion réussie", json.dumps({"user": email, "ip": ip, "role": "admin"}, ensure_ascii=False), "success")
                _sse_emit("login_success", {"message": "Nouvelle connexion : " + email, "user": email, "role": "admin", "ip": ip})
                return self._json_with_cookie({"ok": True, "role": "admin", "session_id": sid}, "session", token, extra_cookies={"saphir_session_id": str(sid)})

            if _STUDENT_PASSWORD and _verify_password(pwd, _hash_password(_STUDENT_PASSWORD)):
                token = _create_session("student")
                ip = self._get_real_ip()
                ua = self.headers.get("User-Agent", "")
                sid = db.create_session("student", ip, ua, email, fingerprint=session_fp)
                _record_success_login(self, email, ip, "student")
                threading.Thread(target=_send_login_sms, args=("student", ip, email), daemon=True).start()
                threading.Thread(target=_send_login_email, args=("student", ip, email), daemon=True).start()
                threading.Thread(target=_check_unknown_device, args=(ua, ip, email, "student", machine_fp), daemon=True).start()
                db.add_notification("Connexion réussie", json.dumps({"user": email, "ip": ip, "role": "student"}, ensure_ascii=False), "success")
                _sse_emit("login_success", {"message": "Nouvelle connexion : " + email, "user": email, "role": "student", "ip": ip})
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
            info = db._parse_user_agent(ua)
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
                    appareil=info.get("appareil", ""), os_name=info.get("os", ""),
                    navigateur=info.get("navigateur", ""), raison=raison, niveau=niveau,
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
                db.add_notification("Connexion échouée : " + email, detail_data, "error")
            except Exception as exc:
                print(f"[ERROR] add_notification login_failed: {exc}")
            _sse_emit("login_failed", {
                "message": "Échec de connexion : " + email,
                "user": email, "ip": client_ip, "ville": ville,
                "appareil": info.get("device", ""), "os": info.get("os", ""),
                "navigateur": info.get("browser", ""), "raison": raison,
                "niveau": niveau, "email_count": email_count + 1, "ip_count": ip_count + 1,
            })
            threading.Thread(target=_send_failed_login_email, args=(client_ip, email, ville, niveau, raison, count), daemon=True).start()
            return self._json({"error": msg, "attempts": count}, 401)

        if path == "/api/logout":
            try:
                body = self._read_body()
                sess_id = body.get("session_id")
                logout_email = self._get_email()
                if sess_id:
                    db.close_session(int(sess_id))
                    db.add_notification("Déconnexion", json.dumps({"user": logout_email or "", "session_id": int(sess_id)}, ensure_ascii=False), "info")
                    _sse_emit("logout", {"message": "Déconnexion : " + (logout_email or ""), "user": logout_email or "", "session_id": int(sess_id)})
            except Exception:
                pass
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.send_header("Set-Cookie", "saphir_session_id=; Path=/; Max-Age=0")
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

        # ── Message read/close/read-all/admin-status (POST) ──
        if path.startswith("/api/messages/") and path.endswith("/read"):
            try:
                nid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            db.mark_message_read(nid)
            email = self._get_email()
            if email:
                db.mark_message_status(nid, email, "read")
            return self._json({"ok": True})

        if path.startswith("/api/messages/") and path.endswith("/close"):
            try:
                nid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            email = ""
            try:
                body = self._read_body()
                email = body.get("email", "").strip()
            except Exception:
                pass
            if not email:
                email = self._get_email()
            if not email:
                return self._json({"error": "Non authentifié"}, 401)
            db.mark_message_status(nid, email, "closed")
            return self._json({"ok": True})

        if path == "/api/messages/read-all":
            email = self._get_email()
            if not email:
                return self._json({"error": "Non authentifié"}, 401)
            db.mark_all_read(email)
            return self._json({"ok": True})

        if path == "/api/messages/admin-status":
            if self._get_role() != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            statuses = db.get_all_message_statuses()
            return self._json(statuses)

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

        if path == "/api/heartbeat":
            body = self._read_body()
            session_id = int(body.get("session_id", 0))
            if session_id:
                db.heartbeat(session_id)
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
                    created_by=body.get("created_by", "") or self._get_email(),
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
                email = self._get_email()
                if not email:
                    return self._json({"error": "Accès refusé"}, 403)
                db.claim_legacy_eleves(email)
                owned = [eid for eid in ids if self._check_eleve_access(eid)]
                if not owned:
                    return self._json({"ok": True, "deleted": 0})
                ids = owned
            try:
                count = db.delete_multiple_eleves(ids)
            except Exception as exc:
                print(f"[ERROR] delete_multiple_eleves: {exc}")
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("eleve_deleted", {"ids": ids})
            db.add_notification("Suppression", f"{count} calcul(s) supprimé(s)", "warning")
            return self._json({"ok": True, "deleted": count})

        if path.startswith("/api/eleves/") and path.endswith("/restore"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                eid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            try:
                db.restore_eleve(eid)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("eleve_restored", {"id": eid})
            return self._json({"ok": True})

        if path == "/api/eleves/purge":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                n = db.purge_deleted_eleves()
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("eleves_purged", {"count": n})
            return self._json({"ok": True, "purged": n})

        if path.startswith("/api/eleves/") and path.endswith("/duplicate"):
            try:
                eid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            if not self._check_eleve_access(eid):
                return self._json({"error": "Accès refusé"}, 403)
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
            email = self._get_email()
            try:
                db.log_activity(session_id, role, action, module, detail, resultat, email=email)
            except Exception as exc:
                print(f"[ERROR] log_activity: {exc}")
            return self._json({"ok": True})

        if path == "/api/sessions/create":
            body = self._read_body()
            role = body.get("role", self._get_role())
            ip = self._get_real_ip()
            ua = self.headers.get("User-Agent", "")
            machine_fp = (body.get("machine_fp") or "").strip()
            session_fp = _device_fingerprint(ua, machine_fp)
            sid = db.create_session(role, ip, ua, fingerprint=session_fp)
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

        if path.startswith("/api/eleves/") and path.endswith("/printed"):
            try:
                eid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            if not self._check_eleve_access(eid):
                return self._json({"error": "Accès refusé"}, 403)
            try:
                db.mark_printed(eid)
            except Exception as exc:
                print(f"[ERROR] mark_printed: {exc}")
                return self._json({"error": "Erreur interne du serveur"}, 500)
            return self._json({"ok": True})

        if path == "/api/sessions/batch-delete":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            body = self._read_body()
            ids = body.get("ids", [])
            if not ids or not isinstance(ids, list):
                return self._json({"error": "ids requis (liste)"}, 400)
            try:
                int_ids = [int(i) for i in ids]
            except (ValueError, TypeError):
                return self._json({"error": "ids invalides"}, 400)
            # Exclude active session
            my_sid = self._get_session_id()
            int_ids = [i for i in int_ids if i != my_sid]
            if not int_ids:
                return self._json({"ok": True, "deleted": 0})
            try:
                count = db.delete_sessions_batch(int_ids)
            except Exception as exc:
                print(f"[ERROR] batch-delete: {exc}")
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("sessions_cleared", {"message": f"{count} session(s) déconnectée(s) supprimée(s)"})
            return self._json({"ok": True, "deleted": count})

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
            try:
                eid = int(path.split("/")[-1])
            except ValueError:
                return self._json({"error": "id invalide"}, 400)
            if not self._check_eleve_access(eid):
                return self._json({"error": "Accès refusé"}, 403)
            try:
                db.delete_eleve(eid)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("eleve_deleted", {"id": eid})
            db.add_notification("Suppression", f"Calcul #{eid} supprimé", "warning")
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

        if path == "/api/sessions/clear-offline":
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                count = db.clear_offline_sessions()
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("sessions_cleared", {"message": f"{count} session(s) déconnectée(s) supprimée(s)"})
            return self._json({"ok": True, "deleted": count})

        # ── DISCONNECT USER ──
        if path.startswith("/api/sessions/") and path.endswith("/disconnect"):
            if role != "admin":
                return self._json({"error": "Accès refusé"}, 403)
            try:
                sid = int(path.split("/")[-2])
            except (ValueError, IndexError):
                return self._json({"error": "id invalide"}, 400)
            try:
                kicked_email = db.get_session_email(sid)
                db.close_session(sid)
                db.add_notification("Session déconnectée", json.dumps({"user": kicked_email, "session_id": sid, "action": "deconnexion par admin"}, ensure_ascii=False), "warning")
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("session_kicked", {"message": "Session déconnectée : " + kicked_email, "user": kicked_email, "session_id": sid})
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
                session_email = db.get_session_email(sid)
                db.ban_user(email, banned_by=session_email or "admin", motif=motif)
                db.close_session(sid)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("user_banned", {"message": "Banni : " + email, "user": email, "motif": motif})
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
            _sse_emit("user_unbanned", {"message": "Débanni : " + email, "user": email})
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
                del_email = db.get_session_email(sid)
                db.delete_session(sid)
            except Exception as exc:
                return self._json({"error": "Erreur interne du serveur"}, 500)
            _sse_emit("session_deleted", {"message": "Session supprimée : " + del_email, "user": del_email, "session_id": sid})
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
            if not self._check_eleve_access(eid):
                return self._json({"error": "Accès refusé"}, 403)
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
    # Background thread: mark stale sessions offline every 15s
    def _heartbeat_checker():
        while True:
            time.sleep(15)
            try:
                n = db.mark_stale_sessions(timeout_seconds=1200)
                if n:
                    print(f"[HEARTBEAT] Marked {n} stale session(s) offline")
                    try:
                        _sse_emit("session_offline", {"message": f"{n} session(s) marquée(s) hors ligne"})
                    except Exception:
                        pass
            except Exception as e:
                print(f"[HEARTBEAT ERROR] {e}")
    _hc = threading.Thread(target=_heartbeat_checker, daemon=True)
    _hc.start()
    return bound


def serve_forever_blocking() -> None:
    if _server:
        _server.serve_forever()


def stop_server() -> None:
    if _server:
        _server.shutdown()
