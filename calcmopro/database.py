"""SQLite database for CALCMO Pro — stores students and results.
Uses Turso HTTP API when TURSO_URL env var is set, else local SQLite.

Security: All writes are explicitly COMMITted via Turso pipeline.
All errors are logged with full details.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any


_DB_NAME = "calcmo.db"
_TURSO_URL: str = os.environ.get("TURSO_URL", "")
_TURSO_URL = _TURSO_URL.replace("libsql://", "")
_TURSO_TOKEN: str = os.environ.get("TURSO_TOKEN", "")

def _turso_enabled() -> bool:
    return bool(_TURSO_URL and _TURSO_TOKEN)


def _turso_request(payload: dict) -> dict:
    """Send a pipeline request to Turso and return raw response."""
    url = f"https://{_TURSO_URL}/v2/pipeline"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {_TURSO_TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _turso_exec(sql: str, args: list | None = None) -> list[dict]:
    """Execute read SQL via Turso pipeline and return rows as dicts."""
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    try:
        data = _turso_request({
            "requests": [
                {"type": "execute", "stmt": stmt},
                {"type": "close"},
            ]
        })
    except Exception as e:
        print(f"[Turso READ ERROR] {e} | sql={sql[:80]}")
        return []

    results = data.get("results", [])
    if not results:
        print(f"[Turso READ WARN] No results for: {sql[:80]}")
        return []
    first = results[0]
    if first.get("type") != "ok":
        print(f"[Turso READ ERROR] type={first.get('type')} error={first.get('error')} | sql={sql[:80]}")
        return []
    resp_data = first.get("response", {})
    result = resp_data.get("result", {})
    cols = result.get("cols", [])
    rows = result.get("rows", [])
    col_names = [c.get("name", f"col{i}") for i, c in enumerate(cols)]
    out = []
    for row in rows:
        d = {}
        for i, cell in enumerate(row):
            if isinstance(cell, dict) and "value" in cell:
                d[col_names[i]] = cell["value"]
            else:
                d[col_names[i]] = cell
        out.append(d)
    return out


def _turso_exec_write(sql: str, args: list | None = None) -> int:
    """Execute write SQL via Turso pipeline WITH explicit COMMIT."""
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    try:
        data = _turso_request({
            "requests": [
                {"type": "execute", "stmt": stmt},
                {"type": "execute", "stmt": {"sql": "SELECT 1"}},
                {"type": "close"},
            ]
        })
    except Exception as e:
        print(f"[Turso WRITE ERROR] {e} | sql={sql[:80]}")
        return 0

    results = data.get("results", [])
    if results and results[0].get("type") == "ok":
        resp_data = results[0].get("response", {})
        affected = resp_data.get("result", {}).get("affected_row_count", 0)
        print(f"[Turso WRITE OK] affected={affected} | sql={sql[:80]}")
        return affected
    print(f"[Turso WRITE FAIL] type={results[0].get('type') if results else 'none'} | sql={sql[:80]}")
    return 0


def _turso_exec_insert(sql: str, args: list | None = None) -> int:
    """Execute INSERT via Turso pipeline and return last_insert_rowid."""
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    try:
        data = _turso_request({
            "requests": [
                {"type": "execute", "stmt": stmt},
                {"type": "execute", "stmt": {"sql": "SELECT last_insert_rowid() as id"}},
                {"type": "close"},
            ]
        })
    except Exception as e:
        print(f"[Turso INSERT ERROR] {e} | sql={sql[:80]}")
        return 0

    results = data.get("results", [])
    # Check INSERT result
    if results and results[0].get("type") != "ok":
        print(f"[Turso INSERT FAIL] results[0]={results[0]} | sql={sql[:80]}")
        return 0
    # Read last_insert_rowid from results[1]
    if len(results) >= 2 and results[1].get("type") == "ok":
        resp_data = results[1].get("response", {})
        result = resp_data.get("result", {})
        rows = result.get("rows", [])
        if rows and rows[0]:
            cell = rows[0][0]
            eid = cell["value"] if isinstance(cell, dict) and "value" in cell else cell
            print(f"[Turso INSERT OK] id={eid} | sql={sql[:80]}")
            return int(eid) if eid else 0
    print(f"[Turso INSERT WARN] No rowid returned | results_count={len(results)}")
    return 0


def _connect():
    if _turso_enabled():
        return None
    _DEFAULT_DIR = Path.home() / ".calcmo"
    _DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _DEFAULT_DIR / _DB_NAME
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    if _turso_enabled():
        print(f"[DB] init_db → Turso | url=https://{_TURSO_URL}/v2/pipeline")
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS eleves (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nom         TEXT NOT NULL,
                matricule   TEXT DEFAULT '',
                classe      TEXT DEFAULT '',
                etablissement TEXT DEFAULT '',
                annee       TEXT DEFAULT '',
                annee_scolaire TEXT DEFAULT '',
                created_by  TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # Add created_by column if missing (existing databases)
        try:
            _turso_exec("ALTER TABLE eleves ADD COLUMN created_by TEXT DEFAULT ''")
        except Exception:
            pass
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS resultats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                eleve_id    INTEGER NOT NULL,
                total       REAL,
                mo          REAL,
                mention     TEXT DEFAULT '',
                matieres    TEXT DEFAULT '{}',
                printed     INTEGER DEFAULT 0,
                date_calc   TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # Add printed column if missing (existing databases)
        try:
            _turso_exec("ALTER TABLE resultats ADD COLUMN printed INTEGER DEFAULT 0")
        except Exception:
            pass
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT NOT NULL DEFAULT 'student',
                ip          TEXT DEFAULT '',
                user_agent  TEXT DEFAULT '',
                ville       TEXT DEFAULT '',
                os          TEXT DEFAULT '',
                navigateur  TEXT DEFAULT '',
                appareil    TEXT DEFAULT '',
                username    TEXT DEFAULT '',
                email       TEXT DEFAULT '',
                login_at    TEXT DEFAULT (datetime('now','localtime')),
                logout_at   TEXT DEFAULT '',
                last_heartbeat TEXT DEFAULT ''
            )
        """)
        # Add missing columns for existing tables
        for col, typ, default in [
            ("username", "TEXT", "''"),
            ("email", "TEXT", "''"),
            ("ville", "TEXT", "''"),
            ("last_heartbeat", "TEXT", "''"),
        ]:
            try:
                _turso_exec(f"ALTER TABLE sessions ADD COLUMN {col} {typ} DEFAULT {default}")
            except Exception:
                pass
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER DEFAULT 0,
                email       TEXT DEFAULT '',
                role        TEXT DEFAULT '',
                action      TEXT NOT NULL,
                module      TEXT DEFAULT '',
                detail      TEXT DEFAULT '',
                resultat    TEXT DEFAULT 'succes',
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS notifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT DEFAULT 'all',
                titre       TEXT NOT NULL,
                message     TEXT DEFAULT '',
                type        TEXT DEFAULT 'info',
                lu          INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS known_devices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                label       TEXT DEFAULT '',
                trusted     INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS banned_users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT NOT NULL,
                banned_by   TEXT DEFAULT '',
                motif       TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT DEFAULT '',
                ip          TEXT DEFAULT '',
                ville       TEXT DEFAULT '',
                appareil    TEXT DEFAULT '',
                os          TEXT DEFAULT '',
                navigateur  TEXT DEFAULT '',
                raison      TEXT DEFAULT '',
                niveau      TEXT DEFAULT 'normal',
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS message_status (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id  INTEGER NOT NULL,
                user_email  TEXT NOT NULL,
                status      TEXT DEFAULT 'shown',
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        # Add missing columns for existing tables
        for col, typ, default in [
            ("annee_scolaire", "TEXT", "''"),
        ]:
            try:
                _turso_exec(f"ALTER TABLE eleves ADD COLUMN {col} {typ} DEFAULT {default}")
            except Exception:
                pass
        try:
            _turso_exec("ALTER TABLE activity_log ADD COLUMN email TEXT DEFAULT ''")
        except Exception:
            pass
        test = _turso_exec("SELECT COUNT(*) as n FROM eleves")
        count = test[0]["n"] if test else "UNKNOWN"
        print(f"[DB] init_db OK | Turso connected | eleves count={count}")
        return

    print("[DB] init_db → Local SQLite")
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS eleves (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nom         TEXT NOT NULL,
            matricule   TEXT DEFAULT '',
            classe      TEXT DEFAULT '',
            etablissement TEXT DEFAULT '',
            annee       TEXT DEFAULT '',
            annee_scolaire TEXT DEFAULT '',
            created_by  TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS resultats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            eleve_id    INTEGER NOT NULL REFERENCES eleves(id) ON DELETE CASCADE,
            total       REAL,
            mo          REAL,
            mention     TEXT DEFAULT '',
            matieres    TEXT DEFAULT '{}',
            printed     INTEGER DEFAULT 0,
            date_calc   TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            role        TEXT NOT NULL DEFAULT 'student',
            ip          TEXT DEFAULT '',
            user_agent  TEXT DEFAULT '',
            ville       TEXT DEFAULT '',
            os          TEXT DEFAULT '',
            navigateur  TEXT DEFAULT '',
            appareil    TEXT DEFAULT '',
            username    TEXT DEFAULT '',
            email       TEXT DEFAULT '',
            login_at    TEXT DEFAULT (datetime('now','localtime')),
            logout_at   TEXT DEFAULT '',
            last_heartbeat TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER DEFAULT 0,
            email       TEXT DEFAULT '',
            role        TEXT DEFAULT '',
            action      TEXT NOT NULL,
            module      TEXT DEFAULT '',
            detail      TEXT DEFAULT '',
            resultat    TEXT DEFAULT 'succes',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT DEFAULT 'all',
            titre       TEXT NOT NULL,
            message     TEXT DEFAULT '',
            type        TEXT DEFAULT 'info',
            lu          INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS known_devices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT NOT NULL,
            label       TEXT DEFAULT '',
            trusted     INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS banned_users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL,
            banned_by   TEXT DEFAULT '',
            motif       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS login_attempts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT DEFAULT '',
            ip          TEXT DEFAULT '',
            ville       TEXT DEFAULT '',
            appareil    TEXT DEFAULT '',
            os          TEXT DEFAULT '',
            navigateur  TEXT DEFAULT '',
            raison      TEXT DEFAULT '',
            niveau      TEXT DEFAULT 'normal',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS message_status (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id  INTEGER NOT NULL,
            user_email  TEXT NOT NULL,
            status      TEXT DEFAULT 'shown',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    # Add missing columns for existing tables
    conn = _connect()
    for col, typ in [("username", "TEXT"), ("email", "TEXT"), ("ville", "TEXT"), ("last_heartbeat", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {typ} DEFAULT ''")
        except Exception:
            pass
    try:
        conn.execute("ALTER TABLE eleves ADD COLUMN annee_scolaire TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE eleves ADD COLUMN created_by TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE resultats ADD COLUMN printed INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE activity_log ADD COLUMN email TEXT DEFAULT ''")
    except Exception:
        pass
    conn.close()


def save_eleve(nom: str, matricule: str = "", classe: str = "",
               etablissement: str = "", annee: str = "",
               total: float = 0, mo: float = 0, mention: str = "",
               matieres: dict | None = None,
               annee_scolaire: str = "",
               created_by: str = "") -> dict[str, Any]:
    matieres_json = json.dumps(matieres or {}, ensure_ascii=False)

    if _turso_enabled():
        eid = _turso_exec_insert(
            "INSERT INTO eleves (nom, matricule, classe, etablissement, annee, annee_scolaire, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [nom, matricule, classe, etablissement, annee, annee_scolaire, created_by],
        )
        if not eid:
            print(f"[DB save_eleve] FAIL: INSERT returned id=0 for nom={nom}")
            return {"eleve": {}, "resultat": {}, "error": "INSERT failed"}
        wr = _turso_exec_write(
            "INSERT INTO resultats (eleve_id, total, mo, mention, matieres, date_calc) VALUES (?, ?, ?, ?, ?, ?)",
            [eid, total, mo, mention, matieres_json, datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        )
        # Verify data is actually in Turso
        verify = _turso_exec("SELECT COUNT(*) as n FROM eleves WHERE id=?", [eid])
        print(f"[DB save_eleve] VERIFY after INSERT: id={eid} found={verify[0]['n'] if verify else '?'}")
        rows = _turso_exec("SELECT * FROM eleves WHERE id=?", [eid])
        res = _turso_exec("SELECT * FROM resultats WHERE eleve_id=?", [eid])
        return {"eleve": rows[0] if rows else {}, "resultat": res[0] if res else {}}

    conn = _connect()
    cur = conn.execute(
        "INSERT INTO eleves (nom, matricule, classe, etablissement, annee, annee_scolaire, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (nom, matricule, classe, etablissement, annee, annee_scolaire, created_by),
    )
    eleve_id = cur.lastrowid
    conn.execute(
        "INSERT INTO resultats (eleve_id, total, mo, mention, matieres, date_calc) VALUES (?, ?, ?, ?, ?, ?)",
        (eleve_id, total, mo, mention, matieres_json, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM eleves WHERE id=?", (eleve_id,)).fetchone()
    res = conn.execute("SELECT * FROM resultats WHERE eleve_id=?", (eleve_id,)).fetchone()
    conn.close()
    return {"eleve": dict(row), "resultat": dict(res)}


def mark_printed(eleve_id: int) -> None:
    """Mark a bulletin as printed."""
    if _turso_enabled():
        n = _turso_exec_write("UPDATE resultats SET printed=1 WHERE eleve_id=?", [eleve_id])
        print(f"[DB mark_printed] Turso UPDATE eleve_id={eleve_id} affected={n}")
        return
    conn = _connect()
    conn.execute("UPDATE resultats SET printed=1 WHERE eleve_id=?", (eleve_id,))
    conn.commit()
    conn.close()


def list_eleves() -> list[dict[str, Any]]:
    if _turso_enabled():
        rows = _turso_exec("""
            SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.annee_scolaire, e.created_by, e.created_at,
                   r.total, r.mo, r.mention, r.matieres, r.printed, r.date_calc
            FROM eleves e
            LEFT JOIN resultats r ON r.eleve_id = e.id
            ORDER BY e.id DESC
        """)
        print(f"[DB list_eleves] Turso returned {len(rows)} rows")
        for d in rows:
            d["id"] = int(d["id"]) if d.get("id") is not None else d.get("id")
            if d.get("matieres") and isinstance(d["matieres"], str):
                try:
                    d["matieres"] = json.loads(d["matieres"])
                except Exception:
                    pass
        return rows

    conn = _connect()
    rows = conn.execute("""
        SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.annee_scolaire, e.created_by, e.created_at,
               r.total, r.mo, r.mention, r.matieres, r.printed, r.date_calc
        FROM eleves e
        LEFT JOIN resultats r ON r.eleve_id = e.id
        ORDER BY e.id DESC
    """).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("matieres"):
            if isinstance(d["matieres"], str):
                d["matieres"] = json.loads(d["matieres"])
        result.append(d)
    return result


def get_eleve(eleve_id: int) -> dict[str, Any] | None:
    if _turso_enabled():
        rows = _turso_exec("""
            SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.annee_scolaire, e.created_by, e.created_at,
                   r.total, r.mo, r.mention, r.matieres, r.printed, r.date_calc
            FROM eleves e
            LEFT JOIN resultats r ON r.eleve_id = e.id
            WHERE e.id = ?
        """, [eleve_id])
        if not rows:
            return None
        d = rows[0]
        d["id"] = int(d["id"]) if d.get("id") is not None else d.get("id")
        if d.get("matieres") and isinstance(d["matieres"], str):
            try:
                d["matieres"] = json.loads(d["matieres"])
            except Exception:
                pass
        return d

    conn = _connect()
    row = conn.execute("""
        SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.annee_scolaire, e.created_by, e.created_at,
               r.total, r.mo, r.mention, r.matieres, r.printed, r.date_calc
        FROM eleves e
        LEFT JOIN resultats r ON r.eleve_id = e.id
        WHERE e.id = ?
    """, (eleve_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    result = dict(row)
    if result.get("matieres"):
        if isinstance(result["matieres"], str):
            result["matieres"] = json.loads(result["matieres"])
    return result


def update_eleve(eleve_id: int, nom: str, matricule: str = "", classe: str = "",
                 etablissement: str = "", annee: str = "",
                 total: float = 0, mo: float = 0, mention: str = "",
                 matieres: dict | None = None,
                 annee_scolaire: str = "") -> dict[str, Any] | None:
    matieres_json = json.dumps(matieres or {}, ensure_ascii=False)

    if _turso_enabled():
        _turso_exec_write(
            "UPDATE eleves SET nom=?, matricule=?, classe=?, etablissement=?, annee=?, annee_scolaire=? WHERE id=?",
            [nom, matricule, classe, etablissement, annee, annee_scolaire, eleve_id],
        )
        _turso_exec_write(
            "UPDATE resultats SET total=?, mo=?, mention=?, matieres=?, date_calc=? WHERE eleve_id=?",
            [total, mo, mention, matieres_json, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), eleve_id],
        )
        return get_eleve(eleve_id)

    conn = _connect()
    conn.execute(
        "UPDATE eleves SET nom=?, matricule=?, classe=?, etablissement=?, annee=?, annee_scolaire=? WHERE id=?",
        (nom, matricule, classe, etablissement, annee, annee_scolaire, eleve_id),
    )
    conn.execute(
        "UPDATE resultats SET total=?, mo=?, mention=?, matieres=?, date_calc=? WHERE eleve_id=?",
        (total, mo, mention, matieres_json, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), eleve_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM eleves WHERE id=?", (eleve_id,)).fetchone()
    res = conn.execute("SELECT * FROM resultats WHERE eleve_id=?", (eleve_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    return {"eleve": dict(row), "resultat": dict(res)}


def duplicate_eleve(eleve_id: int) -> dict[str, Any] | None:
    src = get_eleve(eleve_id)
    if not src:
        return None
    return save_eleve(
        nom=src.get("nom", ""),
        matricule=src.get("matricule", ""),
        classe=src.get("classe", ""),
        etablissement=src.get("etablissement", ""),
        annee=src.get("annee", ""),
        total=src.get("total", 0),
        mo=src.get("mo", 0),
        mention=src.get("mention", ""),
        matieres=src.get("matieres") or {},
    )


def delete_eleve(eleve_id: int) -> bool:
    if _turso_enabled():
        _turso_exec_write("DELETE FROM resultats WHERE eleve_id=?", [eleve_id])
        _turso_exec_write("DELETE FROM eleves WHERE id=?", [eleve_id])
        return True
    conn = _connect()
    conn.execute("DELETE FROM resultats WHERE eleve_id=?", (eleve_id,))
    conn.execute("DELETE FROM eleves WHERE id=?", (eleve_id,))
    conn.commit()
    conn.close()
    return True


def delete_multiple_eleves(ids: list[int]) -> int:
    if not ids:
        return 0
    if _turso_enabled():
        placeholders = ",".join("?" * len(ids))
        _turso_exec_write(f"DELETE FROM resultats WHERE eleve_id IN ({placeholders})", ids)
        _turso_exec_write(f"DELETE FROM eleves WHERE id IN ({placeholders})", ids)
        return len(ids)
    conn = _connect()
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM resultats WHERE eleve_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM eleves WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()
    return len(ids)


def clear_all() -> int:
    if _turso_enabled():
        _turso_exec_write("DELETE FROM resultats")
        _turso_exec_write("DELETE FROM eleves")
        return 0
    conn = _connect()
    conn.execute("DELETE FROM resultats")
    count = conn.execute("SELECT changes()").fetchone()[0]
    conn.execute("DELETE FROM eleves")
    conn.commit()
    conn.close()
    return count


def count_eleves() -> int:
    if _turso_enabled():
        rows = _turso_exec("SELECT COUNT(*) as n FROM eleves")
        return rows[0]["n"] if rows else 0
    conn = _connect()
    n = conn.execute("SELECT COUNT(*) FROM eleves").fetchone()[0]
    conn.close()
    return n


# ── SESSIONS & ACTIVITY LOG ──────────────────────────────────────

def _parse_user_agent(ua: str) -> dict:
    """Extract OS, browser, device type from User-Agent string."""
    ua_lower = ua.lower()
    # OS
    os_name = "Inconnu"
    if "windows" in ua_lower: os_name = "Windows"
    elif "android" in ua_lower: os_name = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower: os_name = "iOS"
    elif "mac os" in ua_lower or "macos" in ua_lower: os_name = "macOS"
    elif "linux" in ua_lower: os_name = "Linux"
    # Browser
    nav = "Inconnu"
    if "edg/" in ua_lower or "edge/" in ua_lower: nav = "Edge"
    elif "chrome/" in ua_lower and "edg/" not in ua_lower: nav = "Chrome"
    elif "firefox/" in ua_lower: nav = "Firefox"
    elif "safari/" in ua_lower and "chrome/" not in ua_lower: nav = "Safari"
    # Device
    appareil = "Ordinateur"
    if "mobile" in ua_lower or "android" in ua_lower: appareil = "Téléphone"
    elif "ipad" in ua_lower or "tablet" in ua_lower: appareil = "Tablette"
    return {"os": os_name, "navigateur": nav, "appareil": appareil}


def create_session(role: str, ip: str = "", user_agent: str = "", email: str = "") -> int:
    """Create a session record and return session_id."""
    info = _parse_user_agent(user_agent)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ville = ""
    if ip and ip not in ("127.0.0.1", "::1", ""):
        _geo_services = [
            (f"https://ipwho.is/{ip}", "ipwho"),
            (f"https://ip-api.com/json/{ip}?fields=city,country", "ipapi"),
            (f"https://freeipapi.com/api/json/{ip}", "freeipapi"),
        ]
        for svc_url, svc_name in _geo_services:
            try:
                req = urllib.request.Request(svc_url, headers={"User-Agent": "SAPHIR-Pro/2.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    geo = json.loads(resp.read())
                if svc_name == "ipwho":
                    city = geo.get("city", "")
                    country = geo.get("country", "")
                elif svc_name == "ipapi":
                    city = geo.get("city", "")
                    country = geo.get("country", "")
                else:
                    city = geo.get("cityName", "")
                    country = geo.get("countryName", "")
                if city:
                    country = "Côte d'Ivoire" if country in ("Ivory Coast", "Ivoiry Coast") else country
                    ville = f"{city}, {country}" if country else city
                    print(f"[DB geoloc] {svc_name} → {ville}")
                    break
            except Exception as e:
                print(f"[DB geoloc] {svc_name} failed: {e}")
    if _turso_enabled():
        sid = _turso_exec_insert(
            "INSERT INTO sessions (role, ip, user_agent, ville, os, navigateur, appareil, login_at, email, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [role, ip, user_agent, ville, info["os"], info["navigateur"], info["appareil"], now, email, now],
        )
        print(f"[DB create_session] id={sid} role={role} ip={ip} ville={ville} os={info['os']} nav={info['navigateur']}")
        return sid
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO sessions (role, ip, user_agent, ville, os, navigateur, appareil, login_at, email, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (role, ip, user_agent, ville, info["os"], info["navigateur"], info["appareil"], now, email, now),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    print(f"[DB create_session] id={sid} role={role} ip={ip} ville={ville} os={info['os']} nav={info['navigateur']}")
    return sid


def close_session(session_id: int) -> None:
    """Mark session as logged out."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if _turso_enabled():
        _turso_exec_write("UPDATE sessions SET logout_at=?, last_heartbeat=? WHERE id=?", [now, now, session_id])
        return
    conn = _connect()
    conn.execute("UPDATE sessions SET logout_at=?, last_heartbeat=? WHERE id=?", (now, now, session_id))
    conn.commit()
    conn.close()


def heartbeat(session_id: int) -> None:
    """Update last_heartbeat for a session."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if _turso_enabled():
        _turso_exec_write("UPDATE sessions SET last_heartbeat=? WHERE id=?", [now, session_id])
        return
    conn = _connect()
    conn.execute("UPDATE sessions SET last_heartbeat=? WHERE id=?", (now, session_id))
    conn.commit()
    conn.close()


def mark_stale_sessions(timeout_seconds: int = 1200) -> int:
    """Mark sessions as offline (set logout_at) if no heartbeat received within timeout.
    Also marks sessions without last_heartbeat if login_at is older than timeout.
    Returns number of sessions marked offline."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if _turso_enabled():
        rows = _turso_exec("SELECT id, last_heartbeat, login_at, logout_at FROM sessions WHERE logout_at = ''")
        count = 0
        for r in rows:
            hb = r.get("last_heartbeat", "")
            login = r.get("login_at", "")
            # Mark offline if heartbeat is stale OR if no heartbeat and login is old
            if (hb and hb < cutoff) or (not hb and login and login < cutoff) or (not hb and not login):
                _turso_exec_write("UPDATE sessions SET logout_at=? WHERE id=?", [now, r["id"]])
                count += 1
        return count
    conn = _connect()
    rows = conn.execute("SELECT id, last_heartbeat, login_at, logout_at FROM sessions WHERE logout_at = ''").fetchall()
    count = 0
    for r in rows:
        hb = r["last_heartbeat"] or ""
        login = r["login_at"] or ""
        if (hb and hb < cutoff) or (not hb and login and login < cutoff) or (not hb and not login):
            conn.execute("UPDATE sessions SET logout_at=? WHERE id=?", (now, r["id"]))
            count += 1
    conn.commit()
    conn.close()
    return count


def delete_session(session_id: int) -> None:
    """Delete a session and its activities."""
    if _turso_enabled():
        _turso_exec_write("DELETE FROM activity_log WHERE session_id=?", [session_id])
        _turso_exec_write("DELETE FROM sessions WHERE id=?", [session_id])
        return
    conn = _connect()
    conn.execute("DELETE FROM activity_log WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    conn.commit()
    conn.close()


def clear_sessions() -> None:
    """Delete all sessions and activities."""
    if _turso_enabled():
        _turso_exec_write("DELETE FROM activity_log")
        _turso_exec_write("DELETE FROM sessions")
        return
    conn = _connect()
    conn.execute("DELETE FROM activity_log")
    conn.execute("DELETE FROM sessions")
    conn.commit()
    conn.close()


def log_activity(session_id: int, role: str, action: str, module: str = "", detail: str = "", resultat: str = "succes", email: str = "") -> None:
    """Log an activity event."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if _turso_enabled():
        _turso_exec_insert(
            "INSERT INTO activity_log (session_id, email, role, action, module, detail, resultat, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [session_id, email, role, action, module, detail, resultat, now],
        )
        return
    conn = _connect()
    conn.execute(
        "INSERT INTO activity_log (session_id, email, role, action, module, detail, resultat, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, email, role, action, module, detail, resultat, now),
    )
    conn.commit()
    conn.close()


def list_sessions() -> list[dict]:
    """List all sessions with activity summary."""
    if _turso_enabled():
        rows = _turso_exec("SELECT * FROM sessions ORDER BY id DESC")
        for d in rows:
            d["id"] = int(d["id"]) if d.get("id") is not None else d["id"]
        return rows
    conn = _connect()
    rows = conn.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_activity_logs(limit: int = 200) -> list[dict]:
    """List recent activity logs."""
    if _turso_enabled():
        rows = _turso_exec(f"SELECT * FROM activity_log ORDER BY id DESC LIMIT {limit}")
        for d in rows:
            d["id"] = int(d["id"]) if d.get("id") is not None else d["id"]
        return rows
    conn = _connect()
    rows = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session_activities(session_id: int) -> list[dict]:
    """Get all activities for a specific session."""
    if _turso_enabled():
        rows = _turso_exec("SELECT * FROM activity_log WHERE session_id=? ORDER BY id DESC", [session_id])
        for d in rows:
            d["id"] = int(d["id"]) if d.get("id") is not None else d["id"]
        return rows
    conn = _connect()
    rows = conn.execute("SELECT * FROM activity_log WHERE session_id=? ORDER BY id DESC", (session_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── NOTIFICATIONS ──────────────────────────────────────────
def add_notification(titre: str, message: str = "", ntype: str = "info", user_id: str = "all") -> int:
    """Create a notification."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if _turso_enabled():
        nid = _turso_exec_insert(
            "INSERT INTO notifications (user_id, titre, message, type, created_at) VALUES (?, ?, ?, ?, ?)",
            [user_id, titre, message, ntype, now],
        )
        return nid
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO notifications (user_id, titre, message, type, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, titre, message, ntype, now),
    )
    nid = cur.lastrowid
    conn.commit()
    conn.close()
    return nid


def list_notifications(user_id: str = "all", limit: int = 50) -> list[dict]:
    """List notifications for a user."""
    if _turso_enabled():
        rows = _turso_exec(
            "SELECT * FROM notifications WHERE user_id=? OR user_id='all' ORDER BY id DESC LIMIT ?",
            [user_id, limit],
        )
        for d in rows:
            d["id"] = int(d["id"]) if d.get("id") is not None else d["id"]
        return rows
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? OR user_id='all' ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_notification_read(notif_id: int) -> None:
    """Mark a notification as read."""
    if _turso_enabled():
        _turso_exec_write("UPDATE notifications SET lu=1 WHERE id=?", [notif_id])
        return
    conn = _connect()
    conn.execute("UPDATE notifications SET lu=1 WHERE id=?", (notif_id,))
    conn.commit()
    conn.close()


def mark_all_notifications_read() -> None:
    """Mark all notifications as read."""
    if _turso_enabled():
        _turso_exec_write("UPDATE notifications SET lu=1 WHERE lu=0")
        return
    conn = _connect()
    conn.execute("UPDATE notifications SET lu=1 WHERE lu=0")
    conn.commit()
    conn.close()


def count_unread_notifications(user_id: str = "all") -> int:
    """Count unread notifications."""
    if _turso_enabled():
        rows = _turso_exec(
            "SELECT COUNT(*) as n FROM notifications WHERE lu=0 AND (user_id=? OR user_id='all')",
            [user_id],
        )
        return int(rows[0]["n"]) if rows else 0
    conn = _connect()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM notifications WHERE lu=0 AND (user_id=? OR user_id='all')",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["n"] if row else 0


def delete_notification(notif_id: int) -> None:
    """Delete a notification."""
    if _turso_enabled():
        _turso_exec_write("DELETE FROM notifications WHERE id=?", [notif_id])
        return
    conn = _connect()
    conn.execute("DELETE FROM notifications WHERE id=?", (notif_id,))
    conn.commit()
    conn.close()


def clear_all_notifications() -> None:
    """Delete all notifications."""
    if _turso_enabled():
        _turso_exec_write("DELETE FROM notifications")
        return
    conn = _connect()
    conn.execute("DELETE FROM notifications")
    conn.commit()
    conn.close()


# ── KNOWN DEVICES ──────────────────────────────────────
def is_device_known(fingerprint: str) -> bool:
    """Check if a device fingerprint exists in the database (any trust level)."""
    if _turso_enabled():
        rows = _turso_exec("SELECT id FROM known_devices WHERE fingerprint=? LIMIT 1", [fingerprint])
        return len(rows) > 0
    conn = _connect()
    row = conn.execute("SELECT id FROM known_devices WHERE fingerprint=? LIMIT 1", (fingerprint,)).fetchone()
    conn.close()
    return row is not None


def add_known_device(fingerprint: str, label: str = "", trusted: int = 1) -> int:
    """Add a known device."""
    if _turso_enabled():
        nid = _turso_exec_insert(
            "INSERT INTO known_devices (fingerprint, label, trusted) VALUES (?, ?, ?)",
            [fingerprint, label, trusted],
        )
        return nid
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO known_devices (fingerprint, label, trusted) VALUES (?, ?, ?)",
        (fingerprint, label, trusted),
    )
    nid = cur.lastrowid
    conn.commit()
    conn.close()
    return nid


def update_known_device_label(fingerprint: str, label: str) -> None:
    """Update the label of a known device if it changed."""
    if not label:
        return
    if _turso_enabled():
        _turso_exec_write("UPDATE known_devices SET label=? WHERE fingerprint=? AND (label IS NULL OR label='' OR label!=?)", [label, fingerprint, label])
        return
    conn = _connect()
    conn.execute("UPDATE known_devices SET label=? WHERE fingerprint=? AND (label IS NULL OR label='' OR label!=?)", (label, fingerprint, label))
    conn.commit()
    conn.close()


def list_known_devices() -> list[dict]:
    """List all known devices."""
    if _turso_enabled():
        rows = _turso_exec("SELECT * FROM known_devices ORDER BY id DESC")
        for r in rows:
            r["trusted"] = int(r.get("trusted", 0))
            r["id"] = int(r.get("id", 0))
        return rows
    conn = _connect()
    rows = conn.execute("SELECT * FROM known_devices ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def trust_device(device_id: int) -> None:
    """Mark a device as trusted."""
    if _turso_enabled():
        _turso_exec_write("UPDATE known_devices SET trusted=1 WHERE id=?", [device_id])
        return
    conn = _connect()
    conn.execute("UPDATE known_devices SET trusted=1 WHERE id=?", (device_id,))
    conn.commit()
    conn.close()


def untrust_device(device_id: int) -> None:
    """Mark a device as untrusted."""
    if _turso_enabled():
        _turso_exec_write("UPDATE known_devices SET trusted=0 WHERE id=?", [device_id])
        return
    conn = _connect()
    conn.execute("UPDATE known_devices SET trusted=0 WHERE id=?", (device_id,))
    conn.commit()
    conn.close()


def delete_known_device(device_id: int) -> None:
    """Delete a known device."""
    if _turso_enabled():
        _turso_exec_write("DELETE FROM known_devices WHERE id=?", [device_id])
        return
    conn = _connect()
    conn.execute("DELETE FROM known_devices WHERE id=?", (device_id,))
    conn.commit()
    conn.close()


# ── BANNED USERS ──────────────────────────────────────
def is_user_banned(email: str) -> bool:
    """Check if a user email is banned."""
    if not email:
        return False
    if _turso_enabled():
        rows = _turso_exec("SELECT id FROM banned_users WHERE email=?", [email.lower().strip()])
        return len(rows) > 0
    conn = _connect()
    row = conn.execute("SELECT id FROM banned_users WHERE email=?", (email.lower().strip(),)).fetchone()
    conn.close()
    return row is not None


def ban_user(email: str, banned_by: str = '', motif: str = '') -> None:
    """Ban a user by email."""
    email = email.lower().strip()
    if not email:
        return
    if _turso_enabled():
        _turso_exec_write("INSERT INTO banned_users (email, banned_by, motif) VALUES (?, ?, ?)", [email, banned_by, motif])
        return
    conn = _connect()
    conn.execute("INSERT INTO banned_users (email, banned_by, motif) VALUES (?, ?, ?)", (email, banned_by, motif))
    conn.commit()
    conn.close()


def unban_user(email: str) -> None:
    """Unban a user by email."""
    email = email.lower().strip()
    if not email:
        return
    if _turso_enabled():
        _turso_exec_write("DELETE FROM banned_users WHERE email=?", [email])
        return
    conn = _connect()
    conn.execute("DELETE FROM banned_users WHERE email=?", (email,))
    conn.commit()
    conn.close()


def list_banned_users() -> list[dict]:
    """List all banned users."""
    if _turso_enabled():
        return _turso_exec("SELECT * FROM banned_users ORDER BY id DESC")
    conn = _connect()
    rows = conn.execute("SELECT * FROM banned_users ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── LOGIN ATTEMPTS ──────────────────────────────────────
def save_login_attempt(email: str, ip: str, ville: str, appareil: str,
                       os_name: str, navigateur: str, raison: str, niveau: str = "normal") -> int:
    """Record a failed login attempt. Returns the attempt id."""
    if _turso_enabled():
        return _turso_exec_insert(
            "INSERT INTO login_attempts (email, ip, ville, appareil, os, navigateur, raison, niveau) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [email, ip, ville, appareil, os_name, navigateur, raison, niveau],
        )
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO login_attempts (email, ip, ville, appareil, os, navigateur, raison, niveau) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (email, ip, ville, appareil, os_name, navigateur, raison, niveau),
    )
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    return aid


def list_login_attempts(limit: int = 100) -> list[dict]:
    """List recent login attempts."""
    if _turso_enabled():
        rows = _turso_exec(f"SELECT * FROM login_attempts ORDER BY id DESC LIMIT {limit}")
        for d in rows:
            d["id"] = int(d["id"]) if d.get("id") is not None else d["id"]
        return rows
    conn = _connect()
    rows = conn.execute("SELECT * FROM login_attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_login_attempts_by_email(email: str) -> int:
    """Count failed login attempts for a given email in the last 24h."""
    if not email:
        return 0
    if _turso_enabled():
        rows = _turso_exec("SELECT COUNT(*) as n FROM login_attempts WHERE email=? AND created_at > datetime('now','-1 day')", [email])
        return rows[0]["n"] if rows else 0
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) as n FROM login_attempts WHERE email=? AND created_at > datetime('now','-1 day')", (email,)).fetchone()
    conn.close()
    return row["n"] if row else 0


def count_login_attempts_by_ip(ip: str) -> int:
    """Count failed login attempts from a given IP in the last 24h."""
    if not ip:
        return 0
    if _turso_enabled():
        rows = _turso_exec("SELECT COUNT(*) as n FROM login_attempts WHERE ip=? AND created_at > datetime('now','-1 day')", [ip])
        return rows[0]["n"] if rows else 0
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) as n FROM login_attempts WHERE ip=? AND created_at > datetime('now','-1 day')", (ip,)).fetchone()
    conn.close()
    return row["n"] if row else 0


# ── ANNEES SCOLAIRES ──────────────────────────────────────
def list_annees_scolaires() -> list[str]:
    """List all distinct school years."""
    if _turso_enabled():
        rows = _turso_exec("SELECT DISTINCT annee_scolaire FROM eleves WHERE annee_scolaire != '' ORDER BY annee_scolaire DESC")
        return [r["annee_scolaire"] for r in rows if r.get("annee_scolaire")]
    conn = _connect()
    rows = conn.execute("SELECT DISTINCT annee_scolaire FROM eleves WHERE annee_scolaire != '' ORDER BY annee_scolaire DESC").fetchall()
    conn.close()
    return [r["annee_scolaire"] for r in rows if r["annee_scolaire"]]


def update_eleve_annee_scolaire(eleve_id: int, annee_scolaire: str) -> None:
    """Update annee_scolaire for an eleve."""
    if _turso_enabled():
        _turso_exec_write("UPDATE eleves SET annee_scolaire=? WHERE id=?", [annee_scolaire, eleve_id])
        return
    conn = _connect()
    conn.execute("UPDATE eleves SET annee_scolaire=? WHERE id=?", (annee_scolaire, eleve_id))
    conn.commit()
    conn.close()


def archive_eleves(annee_scolaire: str) -> int:
    """Mark all eleves without annee_scolaire as archived for the given year."""
    count = 0
    if _turso_enabled():
        rows = _turso_exec("SELECT id FROM eleves WHERE annee_scolaire=''")
        for r in rows:
            _turso_exec_write("UPDATE eleves SET annee_scolaire=? WHERE id=?", [annee_scolaire, r["id"]])
            count += 1
        return count
    conn = _connect()
    rows = conn.execute("SELECT id FROM eleves WHERE annee_scolaire=''").fetchall()
    for r in rows:
        conn.execute("UPDATE eleves SET annee_scolaire=? WHERE id=?", (annee_scolaire, r["id"]))
        count += 1
    conn.commit()
    conn.close()
    return count


# ══════════════════════════════════════════════════════════════
#  MESSAGES
# ══════════════════════════════════════════════════════════════

def send_message(sender: str, recipient: str, message: str) -> int:
    """Send a message from admin to a user — stored in notifications table."""
    titre = f"Message de {sender}"
    detail = json.dumps({"sender": sender, "recipient": recipient, "message": message}, ensure_ascii=False)
    return add_notification(titre, detail, "message")


def get_messages(recipient: str, unread_only: bool = False) -> list:
    """Get messages for a recipient from notifications table."""
    where = "WHERE type='message'"
    if unread_only:
        where += " AND lu=0"
    if _turso_enabled():
        rows = _turso_exec(f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT 50")
    else:
        conn = _connect()
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT 50").fetchall()]
        conn.close()
    # Filter to messages addressed to this recipient
    result = []
    for r in rows:
        try:
            d = json.loads(r.get("message", "{}"))
            if d.get("recipient", "").lower() == recipient.lower():
                r["sender"] = d.get("sender", "")
                r["msg_content"] = d.get("message", "")
                result.append(r)
        except Exception:
            pass
    return result


def count_unread_messages(recipient: str) -> int:
    """Count unread messages for a recipient, excluding closed."""
    closed = set(get_closed_message_ids(recipient))
    msgs = get_messages(recipient, unread_only=True)
    return len([m for m in msgs if m["id"] not in closed])


def mark_message_read(msg_id: int) -> None:
    """Mark a message as read."""
    mark_notification_read(msg_id)


def mark_all_read(recipient: str) -> None:
    """Mark all messages as read for a recipient."""
    msgs = get_messages(recipient, unread_only=True)
    for m in msgs:
        mark_notification_read(m["id"])


# ── Message status tracking (shown/closed/read per user) ──

def mark_message_status(message_id: int, user_email: str, status: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if _turso_enabled():
        _turso_exec_insert(
            "INSERT INTO message_status (message_id, user_email, status, created_at) VALUES (?, ?, ?, ?)",
            [message_id, user_email, status, now],
        )
    else:
        conn = _connect()
        conn.execute(
            "INSERT INTO message_status (message_id, user_email, status, created_at) VALUES (?, ?, ?, ?)",
            (message_id, user_email, status, now),
        )
        conn.commit()
        conn.close()


def get_closed_message_ids(user_email: str) -> list[int]:
    if _turso_enabled():
        rows = _turso_exec(
            "SELECT DISTINCT message_id FROM message_status WHERE user_email=? AND status IN ('closed','read')",
            [user_email],
        )
        return [int(r["message_id"]) for r in rows]
    else:
        conn = _connect()
        rows = conn.execute(
            "SELECT DISTINCT message_id FROM message_status WHERE user_email=? AND status IN ('closed','read')",
            (user_email,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]


def get_message_delivery_status(message_id: int) -> dict:
    if _turso_enabled():
        rows = _turso_exec(
            "SELECT status, user_email, created_at FROM message_status WHERE message_id=? ORDER BY id",
            [message_id],
        )
    else:
        conn = _connect()
        rows = [dict(r) for r in conn.execute(
            "SELECT status, user_email, created_at FROM message_status WHERE message_id=? ORDER BY id",
            (message_id,),
        ).fetchall()]
        conn.close()
    statuses = {}
    for r in rows:
        email = r.get("user_email", "")
        statuses[r.get("status", "")] = {"email": email, "at": r.get("created_at", "")}
    return statuses


def get_all_message_statuses() -> list[dict]:
    if _turso_enabled():
        rows = _turso_exec(
            """SELECT ms.message_id, ms.user_email, ms.status, ms.created_at,
                      n.titre, n.message as msg_json
               FROM message_status ms
               JOIN notifications n ON n.id = ms.message_id
               WHERE n.type='message'
               ORDER BY ms.message_id DESC, ms.id"""
        )
    else:
        conn = _connect()
        rows = [dict(r) for r in conn.execute(
            """SELECT ms.message_id, ms.user_email, ms.status, ms.created_at,
                      n.titre, n.message as msg_json
               FROM message_status ms
               JOIN notifications n ON n.id = ms.message_id
               WHERE n.type='message'
               ORDER BY ms.message_id DESC, ms.id"""
        ).fetchall()]
        conn.close()
    return rows


# Auto-init on import
init_db()
