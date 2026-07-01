#!/usr/bin/env python3
"""HTML audit script — validates tag balance before commit."""
import re
import sys
from pathlib import Path

FILE = Path(__file__).parent / "web" / "CALCUL_MOYENNE_ORIENTATION.html"

TAGS = ["script", "style", "head", "body", "html"]

def audit(path: Path) -> bool:
    content = path.read_text(encoding="utf-8")
    # Remove HTML comments to avoid false matches
    content_clean = re.sub(r"<!--[\s\S]*?-->", "", content)
    # Remove text inside <script> tags (JS may contain HTML-like strings)
    content_clean = re.sub(r"<script[\s\S]*?</script>", "<script></script>", content_clean)
    # Remove text inside <style> tags (CSS may contain HTML-like patterns)
    content_clean = re.sub(r"<style[\s\S]*?</style>", "<style></style>", content_clean)

    ok = True
    print(f"=== AUDIT: {path.name} ===")
    for tag in TAGS:
        opens = len(re.findall(rf"<{tag}[\s>]", content_clean, re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}>", content_clean, re.IGNORECASE))
        match = opens == closes
        status = "OK" if match else "ERREUR"
        print(f"  <{tag}>: {opens:>3}  </{tag}>: {closes:>3}  [{status}]")
        if not match:
            ok = False

    # Also check that <script> and </script> are balanced (critical!)
    script_opens = len(re.findall(r"<script[\s>]", content, re.IGNORECASE))
    script_closes = len(re.findall(r"</script>", content, re.IGNORECASE))
    if script_opens != script_closes:
        print(f"\n  *** CRITIQUE: {script_opens} <script> vs {script_closes} </script> ***")
        ok = False

    print(f"\n{'=' * 40}")
    print(f"  {'RESULTAT: OK' if ok else 'RESULTAT: ECHEC'}")
    return ok

if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else FILE
    if not path.exists():
        print(f"Fichier introuvable: {path}")
        sys.exit(1)
    sys.exit(0 if audit(path) else 1)
