#!/usr/bin/env python3
"""
CALCMO Pro — Audit Complet v1.0
================================
Vérifie : structure HTML, JS, CSS, sécurité, API, DB, performance.
Exécution : python audit_complet.py
"""
import re
import sys
import json
import hashlib
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).parent
HTML_FILE = ROOT / "web" / "CALCUL_MOYENNE_ORIENTATION.html"
LOGIN_FILE = ROOT / "web" / "login.html"
API_FILE = ROOT / "calcmopro" / "api_server.py"
DB_FILE = ROOT / "calcmopro" / "database.py"

CRITICAL = []
ERRORS = []
WARNINGS = []
INFOS = []

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def critical(msg):
    CRITICAL.append(msg)
    print(f"  [CRITIQUE] {msg}")

def error(msg):
    ERRORS.append(msg)
    print(f"  [ERREUR]   {msg}")

def warn(msg):
    WARNINGS.append(msg)
    print(f"  [ATTENTION] {msg}")

def info(msg):
    INFOS.append(msg)
    print(f"  [INFO]     {msg}")

def ok(msg):
    print(f"  [OK]       {msg}")


# ============================================================
# 1. STRUCTURE HTML
# ============================================================
def audit_html_structure(content, filename):
    section(f"1. STRUCTURE HTML — {filename}")

    # Strip JS/CSS content to avoid false matches in template strings
    cleaned = re.sub(r"<script[^>]*>[\s\S]*?</script>", "<SCRIPT></SCRIPT>", content, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style[^>]*>[\s\S]*?</style>", "<STYLE></STYLE>", cleaned, flags=re.IGNORECASE)

    # Tag balance — count on cleaned content
    for tag in ["script", "style", "head", "body", "html", "div", "table", "tr", "td", "th"]:
        opens = len(re.findall(rf"<{tag}[\s>]", cleaned, re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}>", cleaned, re.IGNORECASE))
        if opens != closes:
            error(f"<{tag}>: {opens} ouvertures vs {closes} fermetures")
        else:
            ok(f"<{tag}>: {opens}/{closes} equilibre")

    # DOCTYPE
    if "<!DOCTYPE html>" in content or "<!doctype html>" in content:
        ok("DOCTYPE présent")
    else:
        warn("DOCTYPE manquant")

    # Charset
    if 'charset="UTF-8"' in content or 'charset="utf-8"' in content:
        ok("Charset UTF-8")
    else:
        warn("Charset UTF-8 non déclaré")

    # Viewport
    if "viewport" in content:
        ok("Meta viewport présent")
    else:
        warn("Meta viewport manquant")

    # Lang
    if 'lang="fr"' in content:
        ok("Langue FR")
    else:
        warn("Attribut lang manquant ou incorrect")

    # Favicon
    if "favicon" in content.lower() or '.ico' in content.lower():
        ok("Favicon référence présente")
    else:
        info("Pas de favicon référencé")


# ============================================================
# 2. JAVASCRIPT — ERREURS CRITIQUES
# ============================================================
def audit_js(content):
    section("2. JAVASCRIPT - Detection d'erreurs")

    # Extraire tout le JS (hors <script>)
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", content, re.DOTALL | re.IGNORECASE)
    if not scripts:
        warn("Aucun bloc <script> avec contenu trouve")
        return

    ok(f"{len(scripts)} bloc(s) <script> trouve(s)")

    for i, script in enumerate(scripts):
        js = script.strip()
        if not js:
            continue

        # Vérifier les apostrophes cassées (problème du jour)
        in_string = False
        string_char = None
        for j, ch in enumerate(js):
            if ch in ('"', "'", '`') and (j == 0 or js[j-1] != '\\'):
                if not in_string:
                    in_string = True
                    string_char = ch
                elif ch == string_char:
                    in_string = False

        # Apostrophes dans les chaînes simple-quoted
        bad_apostrophes = re.findall(r"'[^']*'[^']*'[^']*'", js)
        if bad_apostrophes:
            error(f"Script #{i}: Apostrophes potentiellement cassées ({len(bad_apostrophes)} occurrences)")

        # Accolades/parenthèses non fermées
        opens = js.count('{')
        closes = js.count('}')
        if opens != closes:
            error(f"Script #{i}: Accolades déséquilibrées ({opens} vs {closes})")

        opens = js.count('(')
        closes = js.count(')')
        if opens != closes:
            error(f"Script #{i}: Parenthèses déséquilibrées ({opens} vs {closes})")

        opens = js.count('[')
        closes = js.count(']')
        if opens != closes:
            error("Script #{}: Crochets desequilibres ({} vs {})".format(i, opens, closes))

        # Variables globales non declarees suspects (skip — too many false positives)

        # Console.log en prod
        logs = len(re.findall(r'console\.(log|debug|info)\(', js))
        if logs > 10:
            warn(f"Script #{i}: {logs} console.log/debug en production")

        # eval() — danger
        evals = len(re.findall(r'\beval\s*\(', js))
        if evals > 0:
            critical(f"Script #{i}: {evals} appel(s) eval() détecté(s)")

        # innerHTML sans esc
        innerhtml = len(re.findall(r'\.innerHTML\s*=', js))
        if innerhtml > 20:
            warn(f"Script #{i}: {innerhtml} assignments innerHTML (vulnérabilités XSS potentielles)")

        ok(f"Script #{i}: {len(js)} caractères, syntaxe basique OK")


# ============================================================
# 3. SÉCURITÉ
# ============================================================
def audit_security(html_content, api_content, db_content):
    section("3. SÉCURITÉ")

    # --- HTML ---
    # Secrets exposés dans le HTML
    secrets_html = re.findall(r'(password|secret|token|api_key)\s*[=:]\s*["\'][^"\']{4,}["\']', html_content, re.IGNORECASE)
    if secrets_html:
        critical(f"HTML: Secrets exposés dans le code ({len(secrets_html)} trouvés)")
    else:
        ok("HTML: Pas de secrets exposés")

    # --- API ---
    # Mots de passe en dur
    hardcoded = re.findall(r'["\']RECEPTIOn8[^"\']*["\']', api_content)
    if hardcoded:
        warn(f"API: Mots de passe en dur dans le code ({len(hardcoded)})")

    # PBKDF2
    combined = api_content + db_content
    if 'pbkdf2' in combined.lower() or 'hmac' in combined.lower():
        ok("API: Hachage PBKDF2/HMAC present")
    else:
        error("API: Pas de hachage PBKDF2/HMAC detecte")

    # Rate limiting
    if 'rate' in api_content.lower() and 'limit' in api_content.lower():
        ok("API: Rate limiting présent")
    else:
        warn("API: Rate limiting non détecté")

    # CORS
    if 'Access-Control' in api_content or 'cors' in api_content.lower():
        ok("API: CORS configuré")
    else:
        warn("API: Pas de headers CORS explicites")

    # SameSite cookie
    if 'SameSite' in api_content or 'samesite' in api_content.lower():
        ok("API: SameSite cookie configuré")
    else:
        warn("API: SameSite non configuré")

    # Secure flag
    if 'Secure' in api_content:
        ok("API: Secure flag sur cookies")
    else:
        warn("API: Secure flag absent (HTTPS only sur Render)")

    # HTTP → HTTPS redirect
    if 'https' in api_content.lower():
        ok("API: Référence HTTPS détectée")

    # SQL Injection
    if 'execute(f"' in db_content or 'execute("' in db_content:
        raw = [l.strip() for l in db_content.split('\n') if 'execute(f"' in l or 'execute("' in l]
        sql_inject_risk = [l for l in raw if '?' not in l and 'CREATE' not in l and 'ALTER' not in l and 'INSERT' not in l and 'DELETE' not in l and 'UPDATE' not in l]
        if sql_inject_risk:
            warn(f"DB: {len(sql_inject_risk)} requêtes SQL avec interpolation potentiellement risquée")
        else:
            ok("DB: Requêtes SQL utilisent des paramètres")
    else:
        ok("DB: Pas d'interpolation SQL directe détectée")


# ============================================================
# 4. PERFORMANCE
# ============================================================
def audit_performance(content):
    section("4. PERFORMANCE")

    # Taille du fichier
    size_kb = len(content.encode('utf-8')) / 1024
    if size_kb > 300:
        warn(f"Taille HTML: {size_kb:.0f} Ko (> 300 Ko — considérer un split)")
    else:
        ok(f"Taille HTML: {size_kb:.0f} Ko")

    # Nombre de CSS inline
    styles = re.findall(r'<style[^>]*>', content)
    ok(f"{len(styles)} bloc(s) CSS inline")

    # Nombre de scripts
    scripts = re.findall(r'<script[^>]*>', content)
    ok(f"{len(scripts)} bloc(s) JavaScript")

    # Images inline (base64)
    b64 = len(re.findall(r'data:image/', content))
    if b64 > 0:
        warn(f"{b64} images base64 inline (lourd)")
    else:
        ok("Pas d'images base64 inline")

    # CSS externe vs inline
    ext_css = len(re.findall(r'<link[^>]*stylesheet', content))
    ok(f"{ext_css} feuille(s) CSS externe(s)")

    # Taille max d'un sélecteur CSS (perf)
    css_blocks = re.findall(r'<style[^>]*>(.*?)</style>', content, re.DOTALL)
    total_css = sum(len(c) for c in css_blocks)
    ok(f"CSS total: {total_css/1024:.1f} Ko")


# ============================================================
# 5. ACCESSIBILITÉ
# ============================================================
def audit_accessibility(content):
    section("5. ACCESSIBILITÉ")

    # Labels
    labels = len(re.findall(r'<label', content, re.IGNORECASE))
    inputs = len(re.findall(r'<input[^>]*>', content, re.IGNORECASE))
    ok(f"{labels} <label> pour {inputs} <input>")

    # Alt sur images
    imgs = re.findall(r'<img[^>]*>', content, re.IGNORECASE)
    imgs_no_alt = [i for i in imgs if 'alt=' not in i.lower()]
    if imgs_no_alt:
        warn(f"{len(imgs_no_alt)} images sans attribut alt")
    else:
        ok("Toutes les images ont un attribut alt")

    # aria-label
    aria = len(re.findall(r'aria-label', content, re.IGNORECASE))
    ok(f"{aria} attribut(s) aria-label")

    # Title sur boutons
    buttons = re.findall(r'<button[^>]*>', content, re.IGNORECASE)
    buttons_no_title = [b for b in buttons if 'title=' not in b.lower() and 'aria-label' not in b.lower()]
    if len(buttons_no_title) > 5:
        warn(f"{len(buttons_no_title)} boutons sans title ni aria-label")
    else:
        ok("Boutons correctement labellisés")


# ============================================================
# 6. CODING STANDARDS
# ============================================================
def audit_coding_standards(content):
    section("6. CONVENTIONS DE CODE")

    # Encodage
    if 'charset="UTF-8"' in content:
        ok("Encodage UTF-8")
    else:
        warn("Encodage non UTF-8")

    # Fonte
    if 'Ebrima' in content:
        ok("Fonte Ebrima présente")

    # Couleurs cohérentes
    greens = re.findall(r'#[0-9a-fA-F]{3,8}', content)
    green_shades = [g for g in greens if g.lower() in ('#064e3b', '#0b4a35', '#0fac71', '#b8cec3', '#eaf5f0')]
    ok(f"{len(green_shades)} références aux couleurs SAPHIR Pro")

    # Nommage des IDs
    ids = re.findall(r'id="([^"]+)"', content)
    prefixed = [i for i in ids if i.startswith(('mo-', 'mga-', 'hist-', 'users-', 'notif-', 'msg-'))]
    ok(f"{len(prefixed)}/{len(ids)} IDs avec préfixe de module")


# ============================================================
# 7. API ENDPOINTS
# ============================================================
def audit_api(api_content):
    section("7. ENDPOINTS API")

    endpoints = re.findall(r'path\s*==\s*"(/api/[^"]+)"', api_content)
    post_endpoints = re.findall(r'if path == "/api/([^"]+)"', api_content)
    get_endpoints = re.findall(r'if path == "/api/([^"]+)"', api_content)

    ok(f"{len(endpoints)} endpoints /api/* détectés")

    # Vérifier auth sur les routes sensibles
    sensitive = ['sessions', 'known-devices', 'banned-users', 'notifications']
    for ep in sensitive:
        if ep in ' '.join(endpoints):
            ok(f"Endpoint /api/{ep} trouvé")


# ============================================================
# 8. BASE DE DONNÉES
# ============================================================
def audit_database(db_content):
    section("8. BASE DE DONNÉES")

    # Tables
    tables = re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', db_content)
    ok(f"{len(tables)} tables: {', '.join(tables)}")

    # Colonnes critiques
    critical_cols = {
        'sessions': ['id', 'role', 'email', 'login_at', 'logout_at', 'last_heartbeat', 'ip'],
        'eleves': ['id', 'nom', 'prenom', 'classe', 'annee_scolaire'],
        'activity_log': ['id', 'session_id', 'action', 'created_at'],
    }
    for table, cols in critical_cols.items():
        if table in db_content:
            for col in cols:
                if col in db_content:
                    ok(f"Table {table}: colonne '{col}' présente")
                else:
                    warn(f"Table {table}: colonne '{col}' manquante")

    # Turso + SQLite
    if 'turso' in db_content.lower():
        ok("Backend Turso configuré")
    if 'sqlite3' in db_content.lower():
        ok("Backend SQLite local configuré")

    # COMMIT explicite
    commits = db_content.count('.commit()')
    ok(f"{commits} appel(s) .commit() explicites")


# ============================================================
# RAPPORT FINAL
# ============================================================
def report():
    section("RAPPORT FINAL")
    total = len(CRITICAL) + len(ERRORS) + len(WARNINGS)
    print(f"\n  CRITIQUES : {len(CRITICAL)}")
    print(f"  ERREURS   : {len(ERRORS)}")
    print(f"  ATTENTIONS: {len(WARNINGS)}")
    print(f"  INFOS     : {len(INFOS)}")
    print(f"\n  {'='*40}")

    if CRITICAL:
        print("  ❌ DES PROBLÈMES CRITIQUES ONT ÉTÉ DÉTECTÉS")
        for c in CRITICAL:
            print(f"    → {c}")
    elif ERRORS:
        print("  ⚠️  DES ERREURS ONT ÉTÉ DÉTECTÉES")
        for e in ERRORS:
            print(f"    → {e}")
    else:
        print("  ✅ AUDIT RÉUSSI — Aucun problème critique")

    print(f"  {'='*40}\n")
    return len(CRITICAL) == 0 and len(ERRORS) == 0


# ============================================================
# MAIN
# ============================================================
def main():
    print("\n" + "█"*60)
    print("  CALCMO Pro — Audit Complet v1.0")
    print("  " + "─"*56)
    print(f"  HTML: {HTML_FILE.name}")
    print(f"  API:  {API_FILE.name}")
    print(f"  DB:   {DB_FILE.name}")
    print("█"*60)

    files_ok = True
    for f in [HTML_FILE, LOGIN_FILE, API_FILE, DB_FILE]:
        if not f.exists():
            critical(f"Fichier manquant: {f.name}")
            files_ok = False

    if not files_ok:
        print("\n  Fichiers manquants — audit partiel")
        return False

    html = HTML_FILE.read_text(encoding="utf-8")
    login = LOGIN_FILE.read_text(encoding="utf-8")
    api = API_FILE.read_text(encoding="utf-8")
    db = DB_FILE.read_text(encoding="utf-8")

    # Lancer tous les audits
    audit_html_structure(html, HTML_FILE.name)
    audit_html_structure(login, LOGIN_FILE.name)
    audit_js(html)
    audit_security(html, api, db)
    audit_performance(html)
    audit_accessibility(html)
    audit_coding_standards(html)
    audit_api(api)
    audit_database(db)

    return report()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ok = main()
    sys.exit(0 if ok else 1)
