"""Migrate desktop SQLite data to Turso cloud."""
import os
import sqlite3
import json

os.environ["TURSO_URL"] = "saphir-pro-miessou1978.aws-ap-northeast-1.turso.io"
os.environ["TURSO_TOKEN"] = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJqa0RNREdfTkVmR3RNdzZqcHNWb2V3Iiwib3JnX2lkIjoxMDAwMTg2NjIxfQ.IEcXzHaAO2FcA4G-0L3TOxG5OCmbFkOxHfAaZ6SrZ676_X-4bP4FSsgWp5BfWMuIszqZVQV5vg1k2wtj_npWDQ"

from calcmopro import database as db

db.init_db()

rows = db.list_eleves()
print(f"Avant transfert - Turso: {len(rows)} enregistrements")

local_db = os.path.expanduser("~/.calcmo/calcmo.db")
conn = sqlite3.connect(local_db)
conn.row_factory = sqlite3.Row

# Check resultats table
try:
    res_schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='resultats'").fetchone()
    if res_schema:
        print(f"Schema resultats: {res_schema[0]}")
        res_cols = [d[1] for d in conn.execute("PRAGMA table_info(resultats)").fetchall()]
        print(f"Colonnes resultats: {res_cols}")
except Exception as e:
    print(f"Pas de table resultats: {e}")

local_rows = conn.execute("SELECT * FROM eleves").fetchall()
print(f"DB locale: {len(local_rows)} enregistrements")

for row in local_rows:
    r = dict(row)
    eleve_id = r["id"]
    
    # Try to read corresponding resultats
    total = r.get("total", 0) or 0
    mo = r.get("mo", 0) or 0
    mention = r.get("mention", "") or ""
    matieres = {}
    
    try:
        res_rows = conn.execute("SELECT * FROM resultats WHERE eleve_id=?", (eleve_id,)).fetchall()
        for res in res_rows:
            res_dict = dict(res)
            if res_dict.get("total"):
                total = res_dict["total"]
            if res_dict.get("mo"):
                mo = res_dict["mo"]
            if res_dict.get("mention"):
                mention = res_dict["mention"]
            if res_dict.get("matieres"):
                try:
                    matieres = json.loads(res_dict["matieres"])
                except (json.JSONDecodeError, TypeError):
                    matieres = {}
    except Exception as e:
        print(f"  Pas de resultats pour id {eleve_id}: {e}")
    
    result = db.save_eleve(
        nom=r["nom"],
        matricule=r.get("matricule", ""),
        classe=r.get("classe", ""),
        etablissement=r.get("etablissement", ""),
        annee=r.get("annee", ""),
        total=total,
        mo=mo,
        mention=mention,
        matieres=matieres,
    )
    print(f"Transfere: {r['nom']} (MO={mo}) -> id {result['eleve']['id']}")

conn.close()

rows2 = db.list_eleves()
print(f"Apres transfert - Turso: {len(rows2)} enregistrements")
for r in rows2:
    print(f"  - {r['nom']} | MO={r['mo']} | Mention={r['mention']}")
print("Migration terminee!")
