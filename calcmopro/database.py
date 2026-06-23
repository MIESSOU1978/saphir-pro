"""SQLite database for CALCMO Pro — stores students and results.
Uses Turso HTTP API when TURSO_URL env var is set, else local SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from typing import Any


_DB_NAME = "calcmo.db"
_TURSO_URL: str = os.environ.get("TURSO_URL", "")
_TURSO_TOKEN: str = os.environ.get("APP_PASSWORD", "")
_TURSO_TOKEN = os.environ.get("TURSO_TOKEN", _TURSO_TOKEN)


def _turso_enabled() -> bool:
    return bool(_TURSO_URL and _TURSO_TOKEN)


def _turso_exec(sql: str, args: list | None = None) -> list[dict]:
    """Execute SQL via Turso HTTP API and return rows as dicts."""
    url = f"https://{_TURSO_URL}/v2/pipeline"
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    payload = json.dumps({
        "requests": [
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ]
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {_TURSO_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[Turso Error] {e}")
        return []

    results = data.get("results", [])
    if not results:
        return []
    first = results[0]
    if first.get("type") != "ok":
        print(f"[Turso Error] type={first.get('type')} error={first.get('error')}")
        return []
    resp_data = first.get("response", {})
    result = resp_data.get("result", {})
    cols = result.get("cols", [])
    rows = result.get("rows", [])
    col_names = [c.get("name", f"col{i}") for i, c in enumerate(cols)]
    return [dict(zip(col_names, row)) for row in rows]


def _turso_exec_write(sql: str, args: list | None = None) -> int:
    """Execute write SQL via Turso HTTP API."""
    url = f"https://{_TURSO_URL}/v2/pipeline"
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    payload = json.dumps({
        "requests": [
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ]
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {_TURSO_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[Turso Error] {e}")
        return 0

    results = data.get("results", [])
    if results and results[0].get("type") == "ok":
        resp_data = results[0].get("response", {})
        return resp_data.get("result", {}).get("affected_row_count", 0)
    return 0


def _turso_exec_insert(sql: str, args: list | None = None) -> int:
    """Execute INSERT via Turso and return last_insert_rowid."""
    url = f"https://{_TURSO_URL}/v2/pipeline"
    stmt = {"sql": sql}
    if args:
        stmt["args"] = [{"type": "text", "value": str(a)} for a in args]
    payload = json.dumps({
        "requests": [
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ]
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {_TURSO_TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[Turso Error] {e}")
        return 0

    results = data.get("results", [])
    if results and results[0].get("type") == "ok":
        resp_data = results[0].get("response", {})
        return resp_data.get("result", {}).get("last_insert_rowid", 0)
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
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS eleves (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nom         TEXT NOT NULL,
                matricule   TEXT DEFAULT '',
                classe      TEXT DEFAULT '',
                etablissement TEXT DEFAULT '',
                annee       TEXT DEFAULT '',
                created_at  TEXT DEFAULT (date('now'))
            )
        """)
        _turso_exec("""
            CREATE TABLE IF NOT EXISTS resultats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                eleve_id    INTEGER NOT NULL,
                total       REAL,
                mo          REAL,
                mention     TEXT DEFAULT '',
                matieres    TEXT DEFAULT '{}',
                date_calc   TEXT DEFAULT (date('now'))
            )
        """)
        return

    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS eleves (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nom         TEXT NOT NULL,
            matricule   TEXT DEFAULT '',
            classe      TEXT DEFAULT '',
            etablissement TEXT DEFAULT '',
            annee       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (date('now'))
        );
        CREATE TABLE IF NOT EXISTS resultats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            eleve_id    INTEGER NOT NULL REFERENCES eleves(id) ON DELETE CASCADE,
            total       REAL,
            mo          REAL,
            mention     TEXT DEFAULT '',
            matieres    TEXT DEFAULT '{}',
            date_calc   TEXT DEFAULT (date('now'))
        );
    """)
    conn.close()


def save_eleve(nom: str, matricule: str = "", classe: str = "",
               etablissement: str = "", annee: str = "",
               total: float = 0, mo: float = 0, mention: str = "",
               matieres: dict | None = None) -> dict[str, Any]:
    matieres_json = json.dumps(matieres or {}, ensure_ascii=False)

    if _turso_enabled():
        eid = _turso_exec_insert(
            "INSERT INTO eleves (nom, matricule, classe, etablissement, annee) VALUES (?, ?, ?, ?, ?)",
            [nom, matricule, classe, etablissement, annee],
        )
        _turso_exec_write(
            "INSERT INTO resultats (eleve_id, total, mo, mention, matieres) VALUES (?, ?, ?, ?, ?)",
            [eid, total, mo, mention, matieres_json],
        )
        rows = _turso_exec("SELECT * FROM eleves WHERE id=?", [eid])
        res = _turso_exec("SELECT * FROM resultats WHERE eleve_id=?", [eid])
        return {"eleve": rows[0] if rows else {}, "resultat": res[0] if res else {}}

    conn = _connect()
    cur = conn.execute(
        "INSERT INTO eleves (nom, matricule, classe, etablissement, annee) VALUES (?, ?, ?, ?, ?)",
        (nom, matricule, classe, etablissement, annee),
    )
    eleve_id = cur.lastrowid
    conn.execute(
        "INSERT INTO resultats (eleve_id, total, mo, mention, matieres) VALUES (?, ?, ?, ?, ?)",
        (eleve_id, total, mo, mention, matieres_json),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM eleves WHERE id=?", (eleve_id,)).fetchone()
    res = conn.execute("SELECT * FROM resultats WHERE eleve_id=?", (eleve_id,)).fetchone()
    conn.close()
    return {"eleve": dict(row), "resultat": dict(res)}


def list_eleves() -> list[dict[str, Any]]:
    if _turso_enabled():
        rows = _turso_exec("""
            SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.created_at,
                   r.total, r.mo, r.mention, r.matieres, r.date_calc
            FROM eleves e
            LEFT JOIN resultats r ON r.eleve_id = e.id
            ORDER BY e.id DESC
        """)
        for d in rows:
            if d.get("matieres") and isinstance(d["matieres"], str):
                try:
                    d["matieres"] = json.loads(d["matieres"])
                except Exception:
                    pass
        return rows

    conn = _connect()
    rows = conn.execute("""
        SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.created_at,
               r.total, r.mo, r.mention, r.matieres, r.date_calc
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
            SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.created_at,
                   r.total, r.mo, r.mention, r.matieres, r.date_calc
            FROM eleves e
            LEFT JOIN resultats r ON r.eleve_id = e.id
            WHERE e.id = ?
        """, [eleve_id])
        if not rows:
            return None
        d = rows[0]
        if d.get("matieres") and isinstance(d["matieres"], str):
            try:
                d["matieres"] = json.loads(d["matieres"])
            except Exception:
                pass
        return d

    conn = _connect()
    row = conn.execute("""
        SELECT e.id, e.nom, e.matricule, e.classe, e.etablissement, e.annee, e.created_at,
               r.total, r.mo, r.mention, r.matieres, r.date_calc
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
                 matieres: dict | None = None) -> dict[str, Any] | None:
    matieres_json = json.dumps(matieres or {}, ensure_ascii=False)

    if _turso_enabled():
        _turso_exec_write(
            "UPDATE eleves SET nom=?, matricule=?, classe=?, etablissement=?, annee=? WHERE id=?",
            [nom, matricule, classe, etablissement, annee, eleve_id],
        )
        _turso_exec_write(
            "DELETE FROM resultats WHERE eleve_id=?", [eleve_id]
        )
        _turso_exec_write(
            "INSERT INTO resultats (eleve_id, total, mo, mention, matieres) VALUES (?, ?, ?, ?, ?)",
            [eleve_id, total, mo, mention, matieres_json],
        )
        return get_eleve(eleve_id)

    conn = _connect()
    conn.execute(
        "UPDATE eleves SET nom=?, matricule=?, classe=?, etablissement=?, annee=? WHERE id=?",
        (nom, matricule, classe, etablissement, annee, eleve_id),
    )
    conn.execute("DELETE FROM resultats WHERE eleve_id=?", (eleve_id,))
    conn.execute(
        "INSERT INTO resultats (eleve_id, total, mo, mention, matieres) VALUES (?, ?, ?, ?, ?)",
        (eleve_id, total, mo, mention, matieres_json),
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


# Auto-init on import
init_db()
