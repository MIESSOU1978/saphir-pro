"""SQLite database for CALCMO Pro — stores students and results."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any


_DEFAULT_DIR = Path.home() / ".calcmo"
_DB_NAME = "calcmo.db"


def _db_path() -> Path:
    _DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_DIR / _DB_NAME


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
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
    conn = _connect()
    cur = conn.execute(
        "INSERT INTO eleves (nom, matricule, classe, etablissement, annee) VALUES (?, ?, ?, ?, ?)",
        (nom, matricule, classe, etablissement, annee),
    )
    eleve_id = cur.lastrowid
    conn.execute(
        "INSERT INTO resultats (eleve_id, total, mo, mention, matieres) VALUES (?, ?, ?, ?, ?)",
        (eleve_id, total, mo, mention, json.dumps(matieres or {}, ensure_ascii=False)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM eleves WHERE id=?", (eleve_id,)).fetchone()
    res = conn.execute("SELECT * FROM resultats WHERE eleve_id=?", (eleve_id,)).fetchone()
    conn.close()
    return {"eleve": dict(row), "resultat": dict(res)}


def list_eleves() -> list[dict[str, Any]]:
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
            d["matieres"] = json.loads(d["matieres"])
        result.append(d)
    return result


def get_eleve(eleve_id: int) -> dict[str, Any] | None:
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
        result["matieres"] = json.loads(result["matieres"])
    return result


def delete_eleve(eleve_id: int) -> bool:
    conn = _connect()
    conn.execute("DELETE FROM eleves WHERE id=?", (eleve_id,))
    conn.commit()
    deleted = conn.total_changes > 0
    conn.close()
    return deleted


def clear_all() -> int:
    conn = _connect()
    conn.execute("DELETE FROM resultats")
    count = conn.execute("SELECT changes()").fetchone()[0]
    conn.execute("DELETE FROM eleves")
    conn.commit()
    conn.close()
    return count


def count_eleves() -> int:
    conn = _connect()
    n = conn.execute("SELECT COUNT(*) FROM eleves").fetchone()[0]
    conn.close()
    return n


# Auto-init on import
init_db()
