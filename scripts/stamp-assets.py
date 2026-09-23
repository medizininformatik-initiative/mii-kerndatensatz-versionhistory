#!/usr/bin/env python3
"""Setzt einen Inhalts-Hash als Cache-Buster an die Explorer-Assets.

GitHub Pages cached style.css/explorer.js im Browser; ohne Buster sehen
Leser nach einem Deploy weiter die alte Fassung. Der Stempel ergibt sich
aus dem Inhalt aller vier Dateien und aendert sich nur bei echten
Aenderungen. Nach jedem Pipeline-Lauf ausfuehren.
"""
import hashlib
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
files = ["data.json", "explorer.js", "style.css", "field-diffs.json"]
raw = b"".join((DOCS / f).read_bytes() for f in files if (DOCS / f).exists())
stamp = hashlib.sha1(raw).hexdigest()[:8]

h = (DOCS / "index.html").read_text(encoding="utf-8")
h = re.sub(r'href="style\.css(\?v=[0-9a-f]+)?"', f'href="style.css?v={stamp}"', h)
h = re.sub(r'src="explorer\.js(\?v=[0-9a-f]+)?"', f'src="explorer.js?v={stamp}"', h)
(DOCS / "index.html").write_text(h, encoding="utf-8")

j = (DOCS / "explorer.js").read_text(encoding="utf-8")
for f in ("data.json", "field-diffs.json"):
    j = re.sub(rf'fetch\("{re.escape(f)}(\?v=[0-9a-f]+)?"\)', f'fetch("{f}?v={stamp}")', j)
(DOCS / "explorer.js").write_text(j, encoding="utf-8")
print(f"Cache-Buster gesetzt: {stamp}")
