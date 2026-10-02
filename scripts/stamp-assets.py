#!/usr/bin/env python3
"""Setzt einen Inhalts-Hash als Cache-Buster an die Explorer-Assets.

GitHub Pages cached style.css/explorer.js im Browser; ohne Buster sehen
Leser nach einem Deploy weiter die alte Fassung. Der Stempel ergibt sich
aus dem Inhalt aller Asset-Dateien und aendert sich nur bei echten
Aenderungen. Nach jedem Pipeline-Lauf ausfuehren.
"""
import hashlib
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
ASSETS = ["data.json", "explorer.js", "style.css", "field-diffs.json",
          "field-diff-render.js", "diff.js", "release-diff.json"]
# Der Stempel steht selbst in den Skripten — vor dem Hashen entfernen, sonst
# aendert er sich bei jedem Lauf.
raw = b"".join(re.sub(rb"\?v=[0-9a-f]{8}", b"", (DOCS / f).read_bytes())
               for f in ASSETS if (DOCS / f).exists())
stamp = hashlib.sha1(raw).hexdigest()[:8]

# Jede Referenz auf ein Asset (href/src in den Seiten, fetch in den Skripten)
# bekommt denselben Stempel.
for name in ("index.html", "diff.html", "explorer.js", "diff.js"):
    path = DOCS / name
    text = path.read_text(encoding="utf-8")
    for f in ASSETS:
        text = re.sub(rf'((?:href|src)="|fetch\("){re.escape(f)}(\?v=[0-9a-f]+)?"',
                      rf'\g<1>{f}?v={stamp}"', text)
    path.write_text(text, encoding="utf-8")
print(f"Cache-Buster gesetzt: {stamp}")
