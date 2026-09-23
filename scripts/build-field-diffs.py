#!/usr/bin/env python3
"""Feld-genaue Diffs je Versionsuebergang aus dem Element-Index.

Der Explorer zeigt bisher nur Zaehlungen (+n / -n / ~n). Dieses Skript
vergleicht die Element-Fingerprints beider Versionen Feld fuer Feld und
schreibt, WAS sich geaendert hat: Kardinalitaet, Must-Support, Typen,
Target-Profile, Binding (Staerke und ValueSet), fixed/pattern-Werte und
Slicing-Diskriminatoren.

Ausgabe: docs/field-diffs.json — vom Explorer bei Klick auf ein Segment
nachgeladen (nicht Teil der data.json, damit der Erststart schlank bleibt).

    ./scripts/build-field-diffs.py
    ./scripts/build-field-diffs.py --module icu   # nur ein Modul (Debug)
"""

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "data" / "profile-element-index.json"
OUT = REPO / "docs" / "field-diffs.json"

# Fingerprint-Feld -> Anzeigename und Art der Aenderung
# Feldnamen wie im Element-Index (snake_case, siehe build-element-index.py)
FIELDS = [
    ("min", "Kardinalität min", "cardinality"),
    ("max", "Kardinalität max", "cardinality"),
    ("must_support", "Must Support", "ms"),
    ("types", "Typ", "type"),
    ("type_profiles", "Typ-Profil", "type"),
    ("target_profiles", "Referenzziel", "type"),
    ("binding_strength", "Binding-Stärke", "binding"),
    ("binding_value_set", "Binding-ValueSet", "binding"),
    ("fixed_value", "fixed", "value"),
    ("fixed_kind", "fixed-Typ", "value"),
    ("pattern_value", "pattern", "value"),
    ("pattern_kind", "pattern-Typ", "value"),
    ("slice_name", "Slice-Name", "slicing"),
    ("slicing_discriminators", "Slicing-Diskriminator", "slicing"),
    ("slicing_rules", "Slicing-Regel", "slicing"),
]
# Aenderungen, die eine bestehende Instanz ungueltig machen koennen
TIGHTENING = {"cardinality", "binding", "value", "type"}


def norm(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return v


def short(v, limit=90):
    s = "—" if v is None else (json.dumps(v, ensure_ascii=False)
                               if isinstance(v, (dict, list)) else str(v))
    return s if len(s) <= limit else s[:limit - 1] + "…"


def diff_element(old, new):
    """Liste der geaenderten Felder eines Elements."""
    out = []
    for key, label, kind in FIELDS:
        o, n = old.get(key), new.get(key)
        if norm(o) == norm(n):
            continue
        change = {"field": label, "kind": kind, "from": short(o), "to": short(n)}
        # Verschaerfung markieren, wo sie mechanisch erkennbar ist
        if key == "min" and isinstance(n, int) and isinstance(o, int) and n > o:
            change["tighter"] = True
        if key == "max" and o == "*" and n not in ("*", None):
            change["tighter"] = True
        if key == "must_support" and n and not o:
            change["tighter"] = True
        if key == "binding_strength":
            order = {"example": 0, "preferred": 1, "extensible": 2, "required": 3}
            if order.get(str(n), -1) > order.get(str(o), -1):
                change["tighter"] = True
        if key in ("fixed_value", "pattern_value") and o is None and n is not None:
            change["tighter"] = True
        out.append(change)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", action="append")
    a = ap.parse_args()

    idx = json.load(open(INDEX, encoding="utf-8"))
    result = {}
    n_tx = n_el = 0
    for mod, data in sorted(idx.items()):
        if a.module and mod not in a.module:
            continue
        versions = list(data["versions"].keys())
        for i in range(len(versions) - 1):
            v_old, v_new = versions[i], versions[i + 1]
            old_v, new_v = data["versions"][v_old], data["versions"][v_new]
            for url in set(old_v) & set(new_v):
                op, np_ = old_v[url], new_v[url]
                o_el = {e["id"]: e for e in op.get("elements", [])}
                n_el_map = {e["id"]: e for e in np_.get("elements", [])}
                changed = []
                for eid in sorted(set(o_el) & set(n_el_map)):
                    fields = diff_element(o_el[eid], n_el_map[eid])
                    if fields:
                        changed.append({"id": eid, "fields": fields})
                if not changed:
                    continue
                result.setdefault(url, {})[f"{v_old}|{v_new}"] = changed
                n_tx += 1
                n_el += len(changed)

    OUT.parent.mkdir(exist_ok=True)
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    size = OUT.stat().st_size / 1024
    print(f"{OUT.relative_to(REPO)}: {size:.0f} KB · {len(result)} Profile, "
          f"{n_tx} Übergänge mit Feldänderungen, {n_el} geänderte Elemente")
    return 0


if __name__ == "__main__":
    sys.exit(main())
