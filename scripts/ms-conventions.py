#!/usr/bin/env python3
"""Must-Support-Konventionen der MII-KDS-Module untersuchen.

Zwei Auswertungen, beide auf den in der BOM gepinnten Ballot-Versionen:

A) ANGEFASST-OHNE-MS — Elemente, die ein abgeleitetes Profil in seinem
   Differential beruehrt (Invariante, Mapping, Binding, Kardinalitaet …),
   deren Must-Support aber nur aus dem Basisprofil stammt. Im Differential
   sieht ein Leser dort kein MS; im Snapshot gilt es trotzdem. Faellt das MS
   in der Basis spaeter weg, verschwindet es hier lautlos — obwohl das Modul
   das Element nachweislich im Blick hatte. Das ist die Menge, fuer die sich
   explizites Nachsetzen lohnt (im Gegensatz zu allen geerbten MS).

B) CODEABLECONCEPT-EBENEN — auf welcher Ebene die Module Must-Support setzen
   (CC selbst, .coding, Slice, .system/.code/.display/.version, .text) und wo
   sie display/version stattdessen per pattern fixieren. Ein gepatterntes
   display oder version ist strenger als Must-Support: es erzwingt einen
   exakten Wert, ohne als Pflichtfeld sichtbar zu sein — und bricht Instanzen,
   die das Feld nicht fuehren (in den Package-Examples tragen nur 41 % der
   Codings eine version, 71 % ein display).

    ./scripts/ms-conventions.py              # Zusammenfassung auf stdout
    ./scripts/ms-conventions.py --csv        # data/ms-conventions-*.csv schreiben
"""

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOM = REPO / ".." / "kerndatensatz-complete" / "package.json"
CACHE = os.path.expanduser("~/.fhir/packages")


def pinned():
    deps = json.load(open(BOM))["dependencies"]
    return {k: v for k, v in deps.items() if "kerndatensatz." in k}


def load_profiles():
    """url -> (modul, StructureDefinition) fuer alle gepinnten Ballot-Profile."""
    out = {}
    for pkg, ver in pinned().items():
        mod = pkg.split(".")[-1]
        d = os.path.join(CACHE, f"{pkg}#{ver}", "package")
        for f in glob.glob(d + "/StructureDefinition-*.json"):
            try:
                sd = json.load(open(f, encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if sd.get("derivation") == "constraint" and sd.get("kind") == "resource":
                out[sd["url"]] = (mod, sd)
    return out


def analyse_touched_without_ms(prof):
    """A) Im Differential beruehrt, MS nur geerbt."""
    rows = []
    for url, (mod, sd) in prof.items():
        base_url = sd.get("baseDefinition") or ""
        if base_url not in prof:
            continue
        _bmod, bsd = prof[base_url]
        snap = {e.get("id"): e for e in (sd.get("snapshot") or {}).get("element", [])}
        for e in (sd.get("differential") or {}).get("element", []):
            eid = e.get("id")
            if e.get("mustSupport"):
                continue                      # Modul setzt MS selbst — alles gut
            if not snap.get(eid, {}).get("mustSupport"):
                continue                      # gar kein MS im Spiel
            why = sorted(k for k in e
                         if k not in ("id", "path", "sliceName", "mustSupport"))
            if not why:
                continue                      # nur Slice-Deklaration, kein Eingriff
            rows.append({
                "modul": mod, "profil": sd.get("name", url.split("/")[-1]),
                "element": eid, "beruehrt_wegen": ",".join(why),
                "basis": bsd.get("name", base_url.split("/")[-1]), "url": url,
            })
    return rows


def analyse_cc_levels(prof):
    """B) MS-Ebenen und gepatternte display/version je CodeableConcept."""
    rows = []
    for url, (mod, sd) in prof.items():
        els = {e.get("id"): e for e in (sd.get("snapshot")
                                        or sd.get("differential") or {}).get("element", [])}
        roots = set()
        for eid in els:
            m = re.match(r"^(.*)\.coding(:[^.]+)?$", eid)
            if m:
                roots.add(m.group(1))
        for root in roots:
            def ms(i):
                return bool(els.get(i, {}).get("mustSupport"))

            def any_of(pat, key="mustSupport"):
                return any(bool(v.get(key)) for k, v in els.items() if re.match(pat, k))

            esc = re.escape(root)
            pat_disp = pat_ver = False
            for k, v in els.items():
                if not re.match(esc + r"\.coding(:[^.]+)?$", k):
                    continue
                p = v.get("patternCoding") or v.get("fixedCoding") or {}
                if isinstance(p, dict):
                    pat_disp |= "display" in p
                    pat_ver |= "version" in p
            rows.append({
                "modul": mod, "profil": sd.get("name", ""), "codeableconcept": root,
                "ms_cc": ms(root), "ms_text": ms(root + ".text"),
                "ms_coding": ms(root + ".coding"),
                "ms_slice": any_of(esc + r"\.coding:[^.]+$"),
                "ms_system": any_of(esc + r"\.coding(:[^.]+)?\.system$"),
                "ms_code": any_of(esc + r"\.coding(:[^.]+)?\.code$"),
                "ms_display": any_of(esc + r"\.coding(:[^.]+)?\.display$"),
                "ms_version": any_of(esc + r"\.coding(:[^.]+)?\.version$"),
                "pattern_display": pat_disp, "pattern_version": pat_ver,
            })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", action="store_true", help="Ergebnisse nach data/ schreiben")
    a = ap.parse_args()

    prof = load_profiles()
    touched = analyse_touched_without_ms(prof)
    cc = analyse_cc_levels(prof)

    print(f"A) Beruehrt, aber MS nur geerbt: {len(touched)} Elemente")
    per = collections.Counter(r["modul"] for r in touched)
    for m, n in per.most_common():
        print(f"     {m:<16} {n}")
    why = collections.Counter(w for r in touched for w in r["beruehrt_wegen"].split(","))
    print("   Grund des Eingriffs:", dict(why.most_common(6)))

    print(f"\nB) {len(cc)} CodeableConcept-Strukturen")
    keys = [("ms_cc", "CC selbst"), ("ms_coding", ".coding"), ("ms_slice", ".coding:<slice>"),
            ("ms_system", "..system"), ("ms_code", "..code"), ("ms_display", "..display"),
            ("ms_version", "..version"), ("ms_text", ".text")]
    for k, label in keys:
        n = sum(1 for r in cc if r[k])
        print(f"     MS auf {label:<18} {n:>5} ({100*n/max(len(cc),1):>5.1f} %)")
    pd_ = [r for r in cc if r["pattern_display"] and not r["ms_display"]]
    pv_ = [r for r in cc if r["pattern_version"] and not r["ms_version"]]
    print(f"\n   display per pattern fixiert, aber NICHT Must-Support: {len(pd_)}")
    for m, n in collections.Counter(r['modul'] for r in pd_).most_common(6):
        print(f"     {m:<16} {n}")
    print(f"   version per pattern fixiert, aber NICHT Must-Support: {len(pv_)}")
    for m, n in collections.Counter(r['modul'] for r in pv_).most_common(6):
        print(f"     {m:<16} {n}")

    if a.csv:
        for name, rows in (("ms-touched-without-ms", touched), ("ms-codeableconcept-levels", cc)):
            p = REPO / "data" / f"{name}.csv"
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print(f"\n{p.relative_to(REPO)}: {len(rows)} Zeilen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
