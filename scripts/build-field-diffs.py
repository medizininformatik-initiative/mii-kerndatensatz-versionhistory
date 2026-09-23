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
import difflib
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "data" / "profile-element-index.json"
MATRIX = REPO / "data" / "profile-version-matrix.json"
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
    ("constraints", "Invarianten", "constraint"),
    ("conditions", "Bedingungen", "constraint"),
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


# Positionsmarker: reine Stringaehnlichkeit verwechselt "lebensphase-bis"
# mit "Lebensphase-Beginn" (gemeinsames "b"). Anfang/Ende im Pfad sind das
# verlaesslichere Signal.
START_MARKERS = ("onset", "start", "beginn", "begin", "anfang", "von", "from")
END_MARKERS = ("abatement", "ende", "end", "bis", "schluss", "stop", "to")


def position_bias(path):
    """+1 = spricht fuer Anfang, -1 = fuer Ende, 0 = neutral.

    Ausgewertet wird nur das BLATTSEGMENT: der Pfadpraefix traegt regelmaessig
    Gegensignale (Condition.onset[x]:onsetPeriod.end… enthaelt zweimal 'onset'
    und würde 'end' neutralisieren).
    """
    leaf = re.split(r"[.:]", path)[-1]
    words = [w for w in re.split(r"[^a-z]+", leaf.lower()) if w]
    score = 0
    for w in words:
        if any(w.startswith(m) for m in START_MARKERS):
            score += 1
        if any(w.startswith(m) for m in END_MARKERS):
            score -= 1
    return (score > 0) - (score < 0)


def subtree_roots(ids):
    """Maximale Wurzeln einer ID-Menge: Elemente ohne Elternteil in der Menge."""
    idset = set(ids)
    return [i for i in ids
            if not any(i.startswith(o + ".") for o in idset if o != i)]


def rel_children(root, all_ids):
    """Relative Kindpfade unter einer Wurzel."""
    return {i[len(root):] for i in all_ids if i.startswith(root + ".")}


def detect_moves(removed, old_ids, new_ids, old_el, new_el):
    """Erkennt verschobene Teilbaeume: gleiche relative Kinderstruktur an
    anderer Stelle. Das Ziel kann neu sein oder schon existiert haben —
    beides ist ein Move, kein Wegfall."""
    moves, consumed = [], set()
    # Nicht nur maximale Wurzeln pruefen: ein Teilbaum kann aufgeteilt worden
    # sein (onsetPeriod.start -> onsetAge, onsetPeriod.end -> abatementAge),
    # dann passt nur ein tieferer Knoten auf ein Ziel. Grosse Teilbaeume
    # zuerst, damit die Zuordnung so weit oben wie moeglich greift.
    cand_roots = sorted(removed, key=lambda r: (-len(rel_children(r, old_ids)), r))
    for root in cand_roots:
        if root in consumed:
            continue
        kids = rel_children(root, old_ids)
        if not kids:
            continue  # Blätter ohne Teilbaum sind zu schwach als Move-Beleg
        leaf = root.split(".")[-1]
        cands = []
        for cand in new_ids:
            # Ziel muss anderswo liegen als die entfernte Wurzel; ob es schon
            # vorher existierte, ist egal — gerade dann ist es eine
            # Konsolidierung auf ein bestehendes Element.
            if cand == root or cand.startswith(root + "."):
                continue
            if rel_children(cand, new_ids) != kids:
                continue
            if not kids and cand.split(".")[-1] != leaf:
                continue
            sim = difflib.SequenceMatcher(None, leaf.lower(),
                                          cand.split(".")[-1].lower()).ratio()
            # Widersprechende Positionsmarker (Anfang vs. Ende) schliessen
            # einen Move aus — das ist verlaesslicher als Namensaehnlichkeit.
            pb_from, pb_to = position_bias(root), position_bias(cand)
            if pb_from * pb_to < 0:
                continue
            bonus = 0.25 if (pb_from and pb_to and pb_from * pb_to > 0) else 0.0
            cands.append((sim + bonus, cand))
        if not cands:
            continue
        cands.sort(reverse=True)
        sim, target = cands[0]
        if not kids and sim < 0.5:
            continue
        moves.append({
            "from": root, "to": target,
            "children": len(kids),
            "target_is_new": target not in old_ids,
            "leaf_similarity": round(sim, 2),
        })
        consumed.add(root)
        consumed.update(i for i in removed if i.startswith(root + "."))
    return moves, consumed


SKIP_PROPS = {"id", "path", "base_path"}


def describe(el):
    """Kurzbeschreibung der Eigenschaften eines Elements fuer added-Listen."""
    if not el:
        return []
    out = []
    for key, label, _kind in FIELDS:
        v = el.get(key)
        if v in (None, False, [], {}):
            continue
        out.append(f"{label}: {short(v, 60)}")
    return out


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
    # Hinzugefuegte/entfernte Element-IDs kommen aus der Matrix — sie ist die
    # Quelle der im Explorer angezeigten Zaehlungen, also muessen die Listen
    # dazu passen. Der Element-Index liefert nur die Feldaenderungen an
    # Elementen, die in beiden Versionen existieren.
    addrem = {}
    if MATRIX.exists():
        mtx = json.load(open(MATRIX, encoding="utf-8"))
        for _short, pkg in mtx.get("packages", {}).items():
            for url, prof in pkg.get("profiles", {}).items():
                for pw in prof.get("pairwise_comparisons", []):
                    add = pw.get("elements_added") or []
                    rem = pw.get("elements_removed") or []
                    if add or rem:
                        addrem.setdefault(url, {})[f"{pw['from']}|{pw['to']}"] = {
                            "added": add, "removed": rem}
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
                key = f"{v_old}|{v_new}"
                ar = dict(addrem.get(url, {}).get(key, {}))
                # Verschobene Teilbaeume aus add/remove herausrechnen
                if ar.get("removed"):
                    moves, consumed = detect_moves(
                        ar["removed"], set(o_el), set(n_el_map), o_el, n_el_map)
                    if moves:
                        ar["moved"] = moves
                        ar["removed"] = [i for i in ar["removed"] if i not in consumed]
                        tgt = {m["to"] for m in moves}
                        ar["added"] = [i for i in (ar.get("added") or [])
                                       if not any(i == t or i.startswith(t + ".") for t in tgt)]
                if not changed and not ar:
                    continue
                entry = {"changed": changed}
                for k in ("moved", "removed"):
                    if ar.get(k):
                        entry[k] = ar[k]
                if ar.get("added"):
                    # Element-IDs allein sagen wenig ("+1: DocumentReference") —
                    # die mitgebrachten Constraints machen es lesbar.
                    entry["added"] = [{"id": i, "props": describe(n_el_map.get(i))}
                                      for i in ar["added"]]
                result.setdefault(url, {})[key] = entry
                n_tx += 1
                n_el += len(changed)

    # Uebergaenge, die nur Elemente hinzugefuegt/entfernt haben und deshalb
    # oben nicht erfasst wurden (kein gemeinsames Element mit Feldaenderung)
    for url, txs in addrem.items():
        for key, ar in txs.items():
            if key in result.get(url, {}):
                continue
            entry = {"changed": []}
            if ar.get("added"):
                entry["added"] = ar["added"]
            if ar.get("removed"):
                entry["removed"] = ar["removed"]
            result.setdefault(url, {})[key] = entry
            n_tx += 1

    OUT.parent.mkdir(exist_ok=True)
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    size = OUT.stat().st_size / 1024
    print(f"{OUT.relative_to(REPO)}: {size:.0f} KB · {len(result)} Profile, "
          f"{n_tx} Übergänge mit Feldänderungen, {n_el} geänderte Elemente")
    return 0


if __name__ == "__main__":
    sys.exit(main())
