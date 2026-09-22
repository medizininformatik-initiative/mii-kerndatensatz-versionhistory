#!/usr/bin/env python3
"""Schlaegt Canonical-Renames zwischen zwei KDS-Generationen vor.

Vergleicht je Modul die letzte Vor-2027-Version mit der 2027er Version aus
dem Element-Index: Canonicals, die enden, werden gegen neu beginnende
Canonicals desselben Ressourcentyps gematcht (Jaccard-Aehnlichkeit der
Element-IDs plus Namensaehnlichkeit). Ergebnis ist eine Kandidatenliste
zur manuellen Kuratierung — bestaetigte Zeilen wandern als Rename-Eintraege
in die Lineage (LINEAGE_URL_RENAMES in analyze.py bzw. profile-lineage).

    ./scripts/propose-renames.py                 # alle Module
    ./scripts/propose-renames.py --module icu    # nur eins
    Output: data/rename-candidates-2027.csv
"""

import argparse
import csv
import difflib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "data" / "profile-element-index.json"
OUT = REPO / "data" / "rename-candidates-2027.csv"


def elem_set(profile):
    return {e["id"] for e in profile.get("elements", [])}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", action="append", help="nur diese Module")
    ap.add_argument("--since", default="2026.0.0",
                    help="nur Uebergaenge, deren Zielversion >= diesem Wert ist (Vorgabe 2026.0.0)")
    ap.add_argument("--min-similarity", type=float, default=0.3,
                    help="Mindest-Score fuer Kandidaten (Vorgabe 0.3)")
    a = ap.parse_args()

    idx = json.load(open(INDEX, encoding="utf-8"))
    rows = []
    for mod, data in sorted(idx.items()):
        if a.module and mod not in a.module:
            continue
        versions = list(data["versions"].keys())
        pairs = [(versions[i], versions[i + 1]) for i in range(len(versions) - 1)
                 if versions[i + 1] >= a.since]
        for v_old, v_new in pairs:
          old, new = data["versions"][v_old], data["versions"][v_new]
          ended = {u: p for u, p in old.items() if u not in new}
          started = {u: p for u, p in new.items() if u not in old}
          if not ended or not started:
            continue
          for ou, op in sorted(ended.items()):
            best = []
            for nu, np_ in started.items():
                if np_.get("resource_type") != op.get("resource_type"):
                    continue
                es_o, es_n = elem_set(op), elem_set(np_)
                jac = len(es_o & es_n) / len(es_o | es_n) if (es_o | es_n) else 0.0
                name_sim = difflib.SequenceMatcher(
                    None, op.get("name", ""), np_.get("name", "")).ratio()
                score = 0.7 * jac + 0.3 * name_sim
                if score >= a.min_similarity:
                    best.append((score, jac, name_sim, nu, np_))
            best.sort(reverse=True, key=lambda t: t[0])
            if best:
                score, jac, name_sim, nu, np_ = best[0]
                rows.append({
                    "module": mod, "old_version": v_old, "new_version": v_new,
                    "resource_type": op.get("resource_type", ""),
                    "old_name": op.get("name", ""), "new_name": np_.get("name", ""),
                    "score": round(score, 3), "jaccard": round(jac, 3),
                    "name_similarity": round(name_sim, 3),
                    "old_url": ou, "new_url": nu,
                    "second_best": (f"{best[1][3]} ({best[1][0]:.2f})" if len(best) > 1 else ""),
                    "confirmed": "",  # manuell: yes / no
                })
            else:
                rows.append({
                    "module": mod, "old_version": v_old, "new_version": v_new,
                    "resource_type": op.get("resource_type", ""),
                    "old_name": op.get("name", ""), "new_name": "",
                    "score": "", "jaccard": "", "name_similarity": "",
                    "old_url": ou, "new_url": "", "second_best": "",
                    "confirmed": "",  # kein Kandidat -> vermutlich echtes Ende
                })

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    matched = sum(1 for r in rows if r["new_url"])
    print(f"{len(rows)} beendete Canonicals, {matched} mit Rename-Kandidat -> {OUT.relative_to(REPO)}")
    import collections
    for mod, n in collections.Counter(r["module"] for r in rows).most_common():
        m = sum(1 for r in rows if r["module"] == mod and r["new_url"])
        print(f"  {mod:<16} {n:>3} beendet, {m:>3} Kandidaten")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
