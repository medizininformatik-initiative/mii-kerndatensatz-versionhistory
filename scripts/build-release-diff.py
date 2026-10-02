#!/usr/bin/env python3
"""Gesamt-Diff des KDS zwischen zwei Staenden der Complete-BOM.

Die Subway-Map zeigt jeden Versionsschritt einzeln. Wer von der letzten
stabilen 2026er-BOM auf den aktuellen Stand migriert, braucht stattdessen
EINE Liste: je Modul die beiden gepinnten Versionen direkt gegeneinander,
ohne die Zwischenstaende. Dieses Skript vergleicht die Element-Fingerprints
der beiden BOM-Staende und schreibt

  docs/release-diff.json   (von docs/diff.html geladen)
  data/release-diff.csv    (dieselbe Liste flach, eine Zeile je Aenderung)

    BOM=../kerndatensatz-complete
    ./scripts/build-release-diff.py \\
        --baseline "$BOM/de.medizininformatikinitiative.kerndatensatz.complete-2026.2.0.tgz" \\
        --target "$BOM/package.json"

--baseline/--target nehmen eine package.json oder ein Package-Tarball.
"""

import argparse
import csv
import difflib
import importlib.util
import json
import re
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "data" / "profile-element-index.json"
EXPLORER = REPO / "docs" / "data.json"
OUT = REPO / "docs" / "release-diff.json"
OUT_CSV = REPO / "data" / "release-diff.csv"
CACHE = REPO / ".cache" / "fhir-packages"
MII_PREFIX = "de.medizininformatikinitiative.kerndatensatz."

# Feldvergleich und Move-Erkennung kommen aus build-field-diffs.py, damit
# beide Ansichten dieselben Regeln (Verschaerfung, verschobene Teilbaeume)
# anwenden. Der Dateiname hat einen Bindestrich, deshalb importlib.
def _load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses in analyze.py brauchen das
    spec.loader.exec_module(module)
    return module


fd = _load("build-field-diffs")
element_index = _load("build-element-index")
analyze = _load("analyze")


def read_bom(path):
    """Name, Version und Modul-Pins aus package.json oder Tarball."""
    p = Path(path)
    if p.suffix == ".json":
        d = json.load(open(p, encoding="utf-8"))
    else:
        with tarfile.open(p) as t:
            d = json.load(t.extractfile("package/package.json"))
    pins = {k[len(MII_PREFIX):]: v for k, v in d.get("dependencies", {}).items()
            if k.startswith(MII_PREFIX)}
    return {"name": d.get("name"), "version": d.get("version"), "pins": pins}


def vkey(v):
    return [(0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"[.\-]", v)]


def is_stable(v):
    return not any(t in v.lower() for t in ("alpha", "ballot", "rc", "beta", "draft", "snapshot"))


def load_pinned(mod, pin):
    """Profile einer gepinnten Version, die der Index nicht fuehrt (rc-Staende
    schliesst die Pipeline aus): Tarball aus dem Cache bzw. der Registry holen
    und mit denselben Fingerprints wie der Index aufbereiten."""
    tgz = analyze.download_package(MII_PREFIX + mod, pin, CACHE)
    if not tgz:
        return None
    profiles = {}
    for sd in element_index.extract_sds(tgz):
        if sd.get("url"):
            profiles[sd["url"]] = {
                "id": sd.get("id"), "name": sd.get("name"),
                "resource_type": sd.get("type"),
                "base_definition": sd.get("baseDefinition"),
                "elements": [element_index.extract_fingerprint(e)
                             for e in sd.get("differential", {}).get("element", [])],
            }
    return profiles or None


def resolve(mod, pin, indexed):
    """Gepinnte Version im Index suchen; fehlt sie dort, direkt aus dem
    Package lesen. Erst wenn auch das scheitert, die hoechste stabile Version
    davor nehmen und das ausweisen, statt das Modul still als 'neu' zu fuehren."""
    if pin is None:
        return None, None
    if pin in indexed:
        return pin, None
    profiles = load_pinned(mod, pin)
    if profiles:
        indexed[pin] = profiles
        return pin, f"{pin} nicht im Index, direkt aus dem Package gelesen"
    earlier = [v for v in indexed if is_stable(v) and vkey(v) < vkey(pin)]
    if not earlier:
        return None, f"BOM pinnt {pin}, nicht im Index"
    used = max(earlier, key=vkey)
    return used, f"BOM pinnt {pin} (nicht im Index), verglichen mit {used}"


def norm_name(n):
    """Profilname ohne wechselnde Praefixe und Trenner."""
    n = re.sub(r"^(SD_MII_|MII_PR_|MIIPR_|MII_|SD_)", "", n or "")
    return re.sub(r"[_\-]", "", n).lower()


def match_by_name(rest_old, rest_new, old, new, threshold=0.85):
    """Renames, die keine Lane abdeckt (z.B. ICU ..._X -> ..._VENT_X):
    gleiches Modul, gleicher Ressourcentyp, gegenseitig bester Namenstreffer.
    Das ist ein Vorschlag, keine Kuratierung — die Paare werden als
    unbestaetigt ausgewiesen."""
    def best(u, side, others, other_side):
        mod, prof = side[u]
        scored = sorted(
            ((difflib.SequenceMatcher(None, norm_name(prof.get("name")),
                                      norm_name(other_side[c][1].get("name"))).ratio(), c)
             for c in others
             if other_side[c][0] == mod
             and other_side[c][1].get("resource_type") == prof.get("resource_type")),
            reverse=True)
        return scored[0] if scored and scored[0][0] >= threshold else (0, None)

    out = []
    for u in sorted(rest_old):
        sim, cand = best(u, old, rest_new, new)
        if cand and best(cand, new, rest_old, old)[1] == u:
            out.append((u, cand, round(sim, 2)))
    return out


# Canonical-Felder: eine angehaengte |version (CRMI-Versionspinning beim
# Publizieren) ist keine inhaltliche Aenderung, solange das Ziel gleich bleibt.
CANONICAL_FIELDS = {label: key for key, label, _kind in fd.FIELDS
                    if key in ("type_profiles", "target_profiles", "binding_value_set")}


def unpinned(v):
    if isinstance(v, list):
        return sorted(unpinned(x) for x in v)
    return v.split("|")[0] if isinstance(v, str) else v


SLICING_KEYS = ("slicing_discriminators", "slicing_rules")
EXTENSION_SLICING = ([{"type": "value", "path": "url"}], "open")


def implicit_slicing(el):
    """Extensions sind in FHIR immer nach url gesliced. Ob ein Differential
    diesen Slicing-Kopf ausschreibt oder nicht, aendert nichts."""
    return (re.split(r"[.:]", el["id"])[-1] in ("extension", "modifierExtension")
            and (el.get("slicing_discriminators"), el.get("slicing_rules")) == EXTENSION_SLICING)


def substance(el):
    """Element ohne den impliziten Extension-Slicing-Kopf."""
    if implicit_slicing(el):
        return {k: v for k, v in el.items() if k not in SLICING_KEYS}
    return el


def diff_profile(old, new):
    o_el = {e["id"]: e for e in old.get("elements", [])}
    n_el = {e["id"]: e for e in new.get("elements", [])}
    changed = []
    for eid in sorted(set(o_el) & set(n_el)):
        fields = [
            f for f in fd.diff_element(substance(o_el[eid]), substance(n_el[eid]))
            if not (f["field"] in CANONICAL_FIELDS
                    and unpinned(o_el[eid].get(CANONICAL_FIELDS[f["field"]]))
                    == unpinned(n_el[eid].get(CANONICAL_FIELDS[f["field"]])))
        ]
        if fields:
            changed.append({"id": eid, "fields": fields})
    removed = sorted(set(o_el) - set(n_el))
    added = sorted(set(n_el) - set(o_el))
    moved = []
    if removed:
        moved, consumed = fd.detect_moves(removed, set(o_el), set(n_el), o_el, n_el)
        if moved:
            removed = [i for i in removed if i not in consumed]
            tgt = {m["to"] for m in moved}
            added = [i for i in added
                     if not any(i == t or i.startswith(t + ".") for t in tgt)]
    # Elemente ohne jede Einschraenkung (typisch: das nackte Wurzelelement oder
    # ein ausgeschriebener Extension-Slicing-Kopf im Differential) sind
    # kein struktureller Unterschied. Sie werden gezaehlt, aber nicht als
    # Aenderung gefuehrt — sonst gilt fast jedes Profil als geaendert.
    bare = [i for i in added if not fd.describe(substance(n_el[i]))] \
         + [i for i in removed if not fd.describe(substance(o_el[i]))]
    added = [i for i in added if fd.describe(substance(n_el[i]))]
    removed = [i for i in removed if fd.describe(substance(o_el[i]))]
    entry = {"changed": changed}
    if bare:
        entry["bare"] = bare
    if moved:
        entry["moved"] = moved
    if removed:
        # Wie bei den neuen Elementen: die blanke ID sagt nicht, welche
        # Einschraenkung wegfaellt.
        entry["removed"] = [{"id": i, "props": fd.describe(o_el[i])} for i in removed]
    if added:
        entry["added"] = [{"id": i, "props": fd.describe(n_el[i])} for i in added]
    return entry


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True,
                    help="BOM des Ausgangsstands (package.json oder .tgz)")
    ap.add_argument("--target", required=True,
                    help="BOM des Zielstands (package.json oder .tgz)")
    a = ap.parse_args()

    base_bom, tgt_bom = read_bom(a.baseline), read_bom(a.target)
    idx = json.load(open(INDEX, encoding="utf-8"))

    # Lanes aus dem Explorer: umbenannte Canonicals gehoeren zu EINEM Profil.
    # Ohne diese Zuordnung erschiene jeder Rename als entfallen + neu.
    lane, meta = {}, {}
    if EXPLORER.exists():
        for p in json.load(open(EXPLORER, encoding="utf-8"))["profiles"]:
            for u in [p["url"]] + p.get("aka", []):
                lane[u] = p["url"]
            meta[p["url"]] = {k: p[k] for k in ("governance", "isik_twin", "successor")
                              if p.get(k)}

    modules, sides = {}, {"old": {}, "new": {}}
    for mod in sorted(set(base_bom["pins"]) | set(tgt_bom["pins"])):
        indexed = idx.setdefault(mod, {}).setdefault("versions", {})
        b_pin, t_pin = base_bom["pins"].get(mod), tgt_bom["pins"].get(mod)
        b_ver, b_note = resolve(mod, b_pin, indexed)
        t_ver, t_note = resolve(mod, t_pin, indexed)
        modules[mod] = {
            "short": mod, "baseline_pin": b_pin, "target_pin": t_pin,
            "baseline": b_ver, "target": t_ver,
            "notes": [n for n in (b_note, t_note) if n], "profiles": [],
        }
        for side, ver in (("old", b_ver), ("new", t_ver)):
            for url, prof in (indexed.get(ver, {}) if ver else {}).items():
                sides[side][url] = (mod, prof)

    old, new = sides["old"], sides["new"]
    # 1. gleiche Canonical, 2. gleiche Lane (Rename), Rest ist entfallen/neu
    pairs = [(u, u) for u in old if u in new]
    evidence = {}
    rest_old = {u for u in old if u not in new}
    rest_new = {u for u in new if u not in old}
    new_by_lane = {}
    for u in rest_new:
        new_by_lane.setdefault(lane.get(u, u), []).append(u)
    for u in sorted(rest_old):
        cands = new_by_lane.get(lane.get(u, u), [])
        if len(cands) == 1 and cands[0] in rest_new:
            pairs.append((u, cands[0]))
            rest_new.discard(cands[0])
            rest_old.discard(u)
    for u, cand, sim in match_by_name(rest_old, rest_new, old, new):
        pairs.append((u, cand))
        evidence[u] = sim
        rest_old.discard(u)
        rest_new.discard(cand)

    def record(mod, prof, url, status, **extra):
        rec = {"name": prof.get("name", ""), "url": url,
               "resource_type": prof.get("resource_type", ""), "status": status}
        rec.update(extra)
        hints = meta.get(lane.get(url, url), {})
        if status == "removed":
            rec.update(hints)
        elif hints.get("isik_twin"):
            rec["isik_twin"] = hints["isik_twin"]
        modules[mod]["profiles"].append(rec)
        return rec

    for o_url, n_url in pairs:
        (o_mod, o_prof), (n_mod, n_prof) = old[o_url], new[n_url]
        diff = diff_profile(o_prof, n_prof)
        has_diff = any(diff.get(k) for k in ("changed", "moved", "removed", "added"))
        extra = {}
        if o_url != n_url:
            extra["old_url"] = o_url
            # Lane = kuratiert bzw. namens-/strukturgleich; sonst Namensvorschlag
            if o_url in evidence:
                extra["rename_unconfirmed"] = evidence[o_url]
            if o_prof.get("name") != n_prof.get("name"):
                extra["old_name"] = o_prof.get("name")
        if o_mod != n_mod:
            extra["old_module"] = o_mod
        if o_prof.get("base_definition") != n_prof.get("base_definition"):
            extra["base"] = {"from": o_prof.get("base_definition"),
                             "to": n_prof.get("base_definition")}
        status = ("renamed" if o_url != n_url
                  else "changed" if has_diff or "base" in extra else "unchanged")
        if has_diff:
            extra["diff"] = diff
        if diff.get("bare"):
            extra["bare"] = diff.pop("bare")
        extra["n"] = {
            "changed": len(diff["changed"]), "added": len(diff.get("added", [])),
            "removed": len(diff.get("removed", [])), "moved": len(diff.get("moved", [])),
            "tighter": sum(1 for el in diff["changed"] for f in el["fields"]
                           if f.get("tighter")),
        }
        record(n_mod, n_prof, n_url, status, **extra)
    for u in rest_old:
        record(old[u][0], old[u][1], u, "removed",
               n_elements=len(old[u][1].get("elements", [])))
    for u in rest_new:
        record(new[u][0], new[u][1], u, "new",
               n_elements=len(new[u][1].get("elements", [])))

    order = {"removed": 0, "renamed": 1, "changed": 2, "new": 3, "unchanged": 4}
    totals = dict.fromkeys(order, 0)
    totals["tighter"] = 0
    for m in modules.values():
        m["profiles"].sort(key=lambda r: (order[r["status"]], r["name"].lower()))
        m["counts"] = dict.fromkeys(order, 0)
        for r in m["profiles"]:
            m["counts"][r["status"]] += 1
            totals[r["status"]] += 1
        m["counts"]["tighter"] = sum(r.get("n", {}).get("tighter", 0) for r in m["profiles"])
        totals["tighter"] += m["counts"]["tighter"]
        if not m["target"]:
            m["state"] = "not-indexed" if m["target_pin"] else "dropped"
        elif not m["baseline"]:
            m["state"] = "new"
        else:
            m["state"] = "compared"

    result = {
        "baseline": {"name": base_bom["name"], "version": base_bom["version"]},
        "target": {"name": tgt_bom["name"], "version": tgt_bom["version"]},
        "totals": totals,
        "modules": list(modules.values()),
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["module", "baseline_version", "target_version", "profile",
                    "profile_status", "url", "old_url", "change", "element",
                    "field", "from", "to", "tighter"])
        for m in result["modules"]:
            for r in m["profiles"]:
                head = [m["short"], m["baseline"] or "", m["target"] or "", r["name"],
                        r["status"], r["url"], r.get("old_url", "")]
                if r["status"] in ("new", "removed", "renamed"):
                    w.writerow(head + [f"profile-{r['status']}", "", "", "", "", ""])
                if r.get("base"):
                    w.writerow(head + ["base-definition", "", "baseDefinition",
                                       r["base"]["from"], r["base"]["to"], ""])
                d = r.get("diff", {})
                for mv in d.get("moved", []):
                    w.writerow(head + ["element-moved", mv["from"], "", mv["from"], mv["to"], ""])
                for el in d.get("removed", []):
                    w.writerow(head + ["element-removed", el["id"], "",
                                       "; ".join(el["props"]), "", ""])
                for el in d.get("added", []):
                    w.writerow(head + ["element-added", el["id"], "", "",
                                       "; ".join(el["props"]), ""])
                for el in d.get("changed", []):
                    for fl in el["fields"]:
                        w.writerow(head + ["field-changed", el["id"], fl["field"],
                                           fl["from"], fl["to"],
                                           "yes" if fl.get("tighter") else ""])

    print(f"{base_bom['version']} -> {tgt_bom['version']}")
    for m in result["modules"]:
        c = m["counts"]
        print(f"  {m['short']:18} {str(m['baseline']):20} -> {str(m['target']):20} "
              f"-{c['removed']} ren{c['renamed']} ~{c['changed']} +{c['new']} ={c['unchanged']}"
              + (f"   [{'; '.join(m['notes'])}]" if m["notes"] else ""))
    print(f"{OUT.relative_to(REPO)}: {OUT.stat().st_size / 1024:.0f} KB · "
          f"{OUT_CSV.relative_to(REPO)}: {OUT_CSV.stat().st_size / 1024:.0f} KB")
    print("  " + " · ".join(f"{k} {v}" for k, v in totals.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
