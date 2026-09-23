#!/usr/bin/env python3
"""Sucht Nachfolger verschwundener Profile in ANDEREN Modulen.

propose-renames.py matcht nur innerhalb eines Moduls. Profile werden aber
auch modulübergreifend konsolidiert — ein Modul stellt ein Profil ein, weil
die Inhalte in einem anderen Modul aufgegangen sind (z.B. Seltene Studie ->
Modul Studie, Legacy Person/Fall/Diagnose/Prozedur -> Base).

Zwei Belegarten, absteigend nach Härte:
  1. inherited — ein Profil eines anderen Moduls deklariert das verschwundene
     Profil als baseDefinition (harter, deklarierter Link)
  2. similar   — strukturelle Ähnlichkeit (Jaccard über Element-IDs) plus
     Namensähnlichkeit, gleicher Ressourcentyp

Ergebnis ist eine Kandidatenliste zur Kuratierung (Spalte confirmed), nicht
automatisch eine Lane-Verschmelzung: modulübergreifende Konsolidierung ist
eine fachliche Aussage, keine mechanische.

    ./scripts/propose-cross-module.py
    Output: data/cross-module-candidates.csv
"""

import argparse
import csv
import difflib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "data" / "profile-element-index.json"
OUT = REPO / "data" / "cross-module-candidates.csv"


def elems(p):
    return {e["id"] for e in p.get("elements", [])}


# Modul-Kuerzel, die im Profilnamen stehen: bei einer modulübergreifenden
# Verschiebung aendert sich genau dieser Teil — der fachliche Kern bleibt.
MODUL_TOKENS = {
    "sd", "mii", "pr", "icu", "kardio", "kardiologie", "person", "studie",
    "patho", "mikrobio", "mikrobiologie", "molgen", "onko", "onkologie",
    "mtb", "seltene", "biobank", "labor", "laborbefund", "medikation",
    "bildgebung", "dokument", "consent", "pro", "pros", "meta", "base",
    "diagnose", "fall", "prozedur", "symptom", "lungenfunktion",
    "soziodemographie", "ext", "vent", "ect", "muv",
}


def core_name(n):
    """Profilname ohne Namens- und Modulpraefixe."""
    parts = [t for t in re.split(r"[_\-\s]+", n or "") if t]
    keep = [t for t in parts if t.lower() not in MODUL_TOKENS]
    return "".join(keep).lower() or "".join(parts).lower()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-similarity", type=float, default=0.35)
    a = ap.parse_args()

    idx = json.load(open(INDEX, encoding="utf-8"))

    # Profile mit bekanntem Governance-Nachfolger (ISiK 6) sind nicht
    # "verschwunden" — ihr Ziel steht fest und darf kein Matching ausloesen.
    known_targets = set()
    gov = REPO / "data" / "icu-isik-governance.csv"
    if gov.exists():
        for r in csv.DictReader(open(gov, encoding="utf-8")):
            if r.get("mii_url"):
                known_targets.add(r["mii_url"])

    # Letzter bekannter Stand je (Modul, URL) und der jeweils letzte Version
    last_of = {}          # url -> (mod, version, profile)
    ever = {}             # url -> set(mod)
    survives = {}         # (mod, url) -> bool (in der neuesten Modulversion vorhanden)
    latest_by_mod = {}    # mod -> {url: profile} der neuesten Version
    for mod, data in idx.items():
        versions = list(data["versions"].keys())
        if not versions:
            continue
        for ver in versions:
            for url, prof in data["versions"][ver].items():
                last_of[url] = (mod, ver, prof)
                ever.setdefault(url, set()).add(mod)
        latest_by_mod[mod] = data["versions"][versions[-1]]
        for url in data["versions"][versions[-1]]:
            survives[(mod, url)] = True

    # Verschwundene Profile: zuletzt in Modul M gesehen, aber nicht in dessen
    # neuester Version
    gone = [(url, mod, ver, prof) for url, (mod, ver, prof) in last_of.items()
            if not survives.get((mod, url)) and url not in known_targets]

    # baseDefinition-Rueckwaertsindex ueber alle aktuellen Profile
    inherits = {}   # base_url -> [(mod, url, prof)]
    for mod, profs in latest_by_mod.items():
        for url, prof in profs.items():
            base = prof.get("base_definition")
            if base:
                inherits.setdefault(base, []).append((mod, url, prof))

    rows = []
    for url, mod, ver, prof in sorted(gone, key=lambda g: (g[1], g[0])):
        # 1) harter Link: erbt jemand aus einem anderen Modul davon?
        heirs = [(m, u, p) for m, u, p in inherits.get(url, []) if m != mod]
        if heirs:
            for m, u, p in heirs:
                rows.append({
                    "module": mod, "last_version": ver, "old_name": prof.get("name", ""),
                    "old_url": url, "evidence": "inherited", "target_module": m,
                    "target_name": p.get("name", ""), "target_url": u,
                    "jaccard": round(len(elems(prof) & elems(p)) / max(len(elems(prof) | elems(p)), 1), 3),
                    "name_similarity": "", "score": "", "confirmed": "",
                })
            continue
        # 2) weicher Link: strukturell + namentlich aehnlichstes Profil anderswo
        best = None
        for m, profs in latest_by_mod.items():
            if m == mod:
                continue
            for u, p in profs.items():
                if p.get("resource_type") != prof.get("resource_type"):
                    continue
                jac = len(elems(prof) & elems(p)) / max(len(elems(prof) | elems(p)), 1)
                nam = difflib.SequenceMatcher(
                    None, core_name(prof.get("name")), core_name(p.get("name"))).ratio()
                score = 0.6 * jac + 0.4 * nam
                if best is None or score > best[0]:
                    best = (score, jac, m, u, p)
        if best and best[0] >= a.min_similarity:
            score, jac, m, u, p = best
            rows.append({
                "module": mod, "last_version": ver, "old_name": prof.get("name", ""),
                "old_url": url, "evidence": "similar", "target_module": m,
                "target_name": p.get("name", ""), "target_url": u,
                "jaccard": round(jac, 3),
                "name_similarity": round(difflib.SequenceMatcher(
                    None, core_name(prof.get("name")), core_name(p.get("name"))).ratio(), 3),
                "score": round(score, 3), "confirmed": "",
            })

    rows.sort(key=lambda r: (r["evidence"] != "inherited", -(r["score"] or 0)
                             if isinstance(r["score"], float) else 0))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    import collections
    ev = collections.Counter(r["evidence"] for r in rows)
    print(f"{len(gone)} verschwundene Profile, {len(rows)} mit modulübergreifendem Kandidaten "
          f"({dict(ev)}) -> {OUT.relative_to(REPO)}")
    for r in rows:
        if r["evidence"] == "inherited":
            print(f"  [erbt] {r['module']}/{r['old_name']} <- {r['target_module']}/{r['target_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
