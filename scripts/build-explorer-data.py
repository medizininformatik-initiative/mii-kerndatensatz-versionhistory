#!/usr/bin/env python3
"""Build a compact JSON data file for the MII KDS Version History Explorer.

Reads from:
  - data/profile-version-matrix.json
  - data/breaking-change-classification.json (optional)
  - data/maturity-model.json (optional)

Writes:
  - docs/data.json  (consumed by docs/index.html)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX = REPO_ROOT / "data" / "profile-version-matrix.json"
BREAKING = REPO_ROOT / "data" / "breaking-change-classification.json"
MATURITY = REPO_ROOT / "data" / "maturity-model.json"
OUT_FILE = REPO_ROOT / "docs" / "data.json"


def parse_semver_key(v: str) -> tuple:
    parts = re.split(r'[.\-]', v)
    result = []
    for p in parts:
        try:
            result.append((0, int(p)))
        except ValueError:
            result.append((1, p))
    return tuple(result)


# Pre-CalVer-Versionen (vor 2024) auf ihr Release-Jahr abgebildet;
# CalVer-Versionen tragen das Jahr selbst und werden generisch gelesen —
# eine feste Liste hier hat frueher jede neue Generation auf 2021 geworfen.
LEGACY_YEARS = (("0.0", 2019), ("0.9", 2020), ("1.0", 2021), ("2.0", 2022), ("1.", 2022))


def version_year(v: str) -> int:
    """Map a version string to its release year (CalVer-aware)."""
    m = re.match(r"(20\d\d)\.", v)
    if m:
        return int(m.group(1))
    for prefix, year in LEGACY_YEARS:
        if v.startswith(prefix):
            return year
    return 2023


def fractional_year(v: str) -> float:
    """Heuristic: ordinal position within the year, 0..1, based on patch number."""
    yr = version_year(v)
    parts = v.split(".")
    minor = patch = 0
    try:
        if len(parts) >= 2:
            minor = int(parts[1])
        if len(parts) >= 3:
            # patch may include dashes/letters
            p = re.match(r"(\d+)", parts[2])
            if p:
                patch = int(p.group(1))
    except ValueError:
        pass
    # Compress minor*10 + patch into 0..1 within the year
    return yr + min(0.95, (minor * 0.2 + patch * 0.05))


def is_stable(v: str) -> bool:
    return not any(t in v.lower() for t in ("alpha", "ballot", "rc", "beta", "draft", "snapshot"))


def main():
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(MATRIX) as f:
        matrix = json.load(f)

    breaking_data = []
    if BREAKING.exists():
        with open(BREAKING) as f:
            breaking_data = json.load(f)

    maturity_data = {"modules": []}
    if MATURITY.exists():
        with open(MATURITY) as f:
            maturity_data = json.load(f)

    # Index breaking classifications: (module, profile_url, from, to) → entry
    breaking_idx = {}
    for b in breaking_data:
        key = (b["module"], b["profile_url"], b["from"], b["to"])
        breaking_idx[key] = b

    maturity_idx = {m["module"]: m for m in maturity_data.get("modules", [])}

    # ─ Modules ─────────────────────────────────────────────────────────
    modules = []
    for short, pkg_data in sorted(matrix["packages"].items()):
        versions = pkg_data.get("versions", [])
        # Count profiles per version
        version_meta = []
        for v in versions:
            n_profiles = sum(
                1
                for prof in pkg_data["profiles"].values()
                for grp in prof.get("compatibility_groups", [])
                if v in grp.get("versions", [])
            )
            version_meta.append({
                "v": v,
                "stable": is_stable(v),
                "year": version_year(v),
                "frac_year": round(fractional_year(v), 3),
                "n_profiles": n_profiles,
            })

        m = maturity_idx.get(short, {})
        modules.append({
            "short": short,
            "package_id": pkg_data.get("package_id", ""),
            "is_legacy": pkg_data.get("is_legacy", False),
            "n_versions": len(versions),
            "n_profiles": len(pkg_data.get("profiles", {})),
            "first_version": versions[0] if versions else None,
            "latest_version": versions[-1] if versions else None,
            "versions": version_meta,
            "maturity": {
                "level": m.get("level"),
                "avg": m.get("avg"),
                "d1": m.get("d1"),
                "d2": m.get("d2"),
                "d3": m.get("d3"),
                "d4": m.get("d4"),
                "d5": m.get("d5"),
                "d6": m.get("d6"),
            } if m else None,
        })

    # ─ Profiles ────────────────────────────────────────────────────────
    profiles = []
    for short, pkg_data in matrix["packages"].items():
        for url, prof_data in pkg_data["profiles"].items():
            transitions = []
            for pw in prof_data.get("pairwise_comparisons", []):
                key = (short, url, pw["from"], pw["to"])
                bc = breaking_idx.get(key, {})
                transitions.append({
                    "from": pw["from"],
                    "to": pw["to"],
                    "cat": pw["category"],
                    "breaking": pw["has_breaking"],
                    "n_add": len(pw["elements_added"]),
                    "n_rem": len(pw["elements_removed"]),
                    "n_mod": len(pw["elements_modified"]),
                    "severity": bc.get("severity"),
                    "reason": bc.get("reason"),
                })

            profiles.append({
                "url": url,
                "name": prof_data.get("name", ""),
                "module": short,
                "resource_type": prof_data.get("resource_type", ""),
                "first_seen": prof_data.get("first_seen"),
                "last_seen": prof_data.get("last_seen"),
                "n_versions": prof_data.get("version_count", 0),
                "groups": [
                    {
                        "idx": i + 1,
                        "category": g.get("category"),
                        "versions": g.get("versions", []),
                    }
                    for i, g in enumerate(prof_data.get("compatibility_groups", []))
                ],
                "transitions": transitions,
            })

    # ─ Renames & Governance (2027-Werkzeuge) ──────────────────────────
    # Inhaltsgleiche Canonical-Renames (Jaccard 1.0 bzw. kuratiert confirmed=yes)
    # werden zu EINER Lane verschmolzen; ICU-Profile, die nach ISiK 6
    # uebergegangen sind, bekommen ein governance-migration-Terminal.
    import csv as _csv, re as _re

    def _vkey(v):
        return [int(x) if x.isdigit() else x for x in _re.split(r"[.\-]", v)]

    def _norm_name(n):
        """Profilname ohne die wechselnden Namenspraefixe der MII-Module."""
        n = _re.sub(r"^(SD_MII_|MII_PR_|MII_|SD_)", "", n or "")
        return _re.sub(r"[_\-]", "", n).lower()

    ren_path = REPO_ROOT / "data" / "rename-candidates.csv"
    if ren_path.exists():
        links = []
        for r in _csv.DictReader(open(ren_path, encoding="utf-8")):
            conf = (r.get("confirmed") or "").strip().lower()
            if not r.get("new_url") or conf == "no":
                continue
            try:
                jac = float(r.get("jaccard") or 0)
            except ValueError:
                jac = 0.0
            same_name = _norm_name(r.get("old_name")) == _norm_name(r.get("new_name"))
            # Identitaet und Strukturaenderung sind orthogonal: derselbe
            # Profilname (auch nach Praefix-Wechsel SD_ -> MII_PR_) bedeutet
            # dieselbe Linie, egal wie stark sich die Struktur geaendert hat.
            # Bei abweichendem Namen braucht es Strukturgleichheit als Beleg.
            if conf == "yes" or same_name or jac == 1.0:
                links.append(r)
        parent = {}
        def find(u):
            parent.setdefault(u, u)
            while parent[u] != u:
                parent[u] = parent[parent[u]]
                u = parent[u]
            return u
        def union(a, b):
            parent[find(a)] = find(b)
        for r in links:
            union(r["old_url"], r["new_url"])
        by_url = {p_["url"]: p_ for p_ in profiles}
        groups_by_root = {}
        for u in list(parent):
            if u in by_url:
                groups_by_root.setdefault(find(u), []).append(u)
        for root, urls in groups_by_root.items():
            if len(urls) < 2:
                continue
            members = [by_url[u] for u in urls]
            primary = max(members, key=lambda m: _vkey(m["last_seen"] or "0"))
            rest = [m for m in members if m is not primary]
            primary["aka"] = sorted(m["url"] for m in rest)
            primary["first_seen"] = min(members, key=lambda m: _vkey(m["first_seen"] or "9"))["first_seen"]
            primary["n_versions"] = sum(m["n_versions"] for m in members)
            merged_groups = [g for m in members for g in m["groups"]]
            merged_groups.sort(key=lambda g: _vkey(g["versions"][0]) if g["versions"] else [0])
            for i, g in enumerate(merged_groups):
                g["idx"] = i + 1
            primary["groups"] = merged_groups
            seen_tx = {(t["from"], t["to"]) for t in primary["transitions"]}
            for m in rest:
                for t in m["transitions"]:
                    if (t["from"], t["to"]) not in seen_tx:
                        primary["transitions"].append(t)
                        seen_tx.add((t["from"], t["to"]))
            for r in links:
                if r["old_url"] in urls and (r["old_version"], r["new_version"]) not in seen_tx:
                    primary["transitions"].append({
                        "from": r["old_version"], "to": r["new_version"],
                        "cat": "renamed", "breaking": False,
                        "instance_breaking": True,
                        "jaccard": r.get("jaccard") or None,
                        "n_add": 0, "n_rem": 0, "n_mod": 0,
                        "severity": None,
                        "reason": (f"Canonical-Rename: {r['old_url']} -> {r['new_url']}"
                                   + (f" · Strukturähnlichkeit {r['jaccard']}"
                                      if r.get("jaccard") else "")),
                    })
                    seen_tx.add((r["old_version"], r["new_version"]))
            for m in rest:
                profiles.remove(m)

    gov_path = REPO_ROOT / "data" / "icu-isik-governance.csv"
    if gov_path.exists():
        by_url = {p_["url"]: p_ for p_ in profiles}
        aka_idx = {a: p_ for p_ in profiles for a in p_.get("aka", [])}
        for r in _csv.DictReader(open(gov_path, encoding="utf-8")):
            if not r.get("mii_url"):
                continue
            prof = by_url.get(r["mii_url"]) or aka_idx.get(r["mii_url"])
            if not prof:
                continue
            info = {"target_url": r["isik_url"], "jaccard": r["struktur_jaccard"],
                    "since": r["mii_letzte_version"]}
            if r["klasse"] == "nach-isik-uebergegangen":
                prof["governance"] = info
            elif r["klasse"] == "doppel-governance":
                prof["isik_twin"] = info

    # Modulübergreifende Nachfolger-Hinweise (Kandidaten, nicht kuratiert):
    # verschwundene Profile, deren Inhalt in einem anderen Modul aufgegangen
    # sein duerfte. Bewusst nur als Hinweis am Profil, keine Lane-Verschmelzung.
    cross_path = REPO_ROOT / "data" / "cross-module-candidates.csv"
    if cross_path.exists():
        by_url = {p_["url"]: p_ for p_ in profiles}
        aka_idx = {a: p_ for p_ in profiles for a in p_.get("aka", [])}
        for r in _csv.DictReader(open(cross_path, encoding="utf-8")):
            prof = by_url.get(r["old_url"]) or aka_idx.get(r["old_url"])
            if not prof:
                continue
            conf = (r.get("confirmed") or "").strip().lower()
            if conf == "no":
                continue
            try:
                score = float(r.get("score") or 0)
            except ValueError:
                score = 0.0
            try:
                nsim = float(r.get("name_similarity") or 0)
            except ValueError:
                nsim = 0.0
            # Struktur-Score allein traegt nicht: ohne Naehe im fachlichen
            # Namenskern ist ein Treffer meist Zufall (lange Namen, gemeinsame
            # Wortbestandteile). Deshalb beide Signale verlangen.
            if conf != "yes" and r["evidence"] != "inherited" and not (score >= 0.55 and nsim >= 0.6):
                continue
            prof["successor"] = {
                "module": r["target_module"], "name": r["target_name"],
                "url": r["target_url"], "evidence": r["evidence"],
                "score": r.get("score"), "name_similarity": r.get("name_similarity"),
                "confirmed": conf == "yes",
            }

    # ─ Lineage ─────────────────────────────────────────────────────────
    lineage = []
    for entry in matrix.get("lineage", []):
        lineage.append({
            "legacy_module": entry["legacy_package"],
            "profile_name": entry["profile_name"],
            "legacy_url": entry["legacy_url"],
            "base_url": entry["base_url"],
            "url_changed": entry["url_changed"],
            "transition_category": entry["transition"]["category"],
            "breaking": entry["transition"]["has_breaking"],
        })

    # ─ Output ──────────────────────────────────────────────────────────
    output = {
        "generated_at": matrix.get("generated_at"),
        "n_modules": len(modules),
        "n_profiles": len(profiles),
        "n_transitions": sum(len(p["transitions"]) for p in profiles),
        "modules": modules,
        "profiles": profiles,
        "lineage": lineage,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = OUT_FILE.stat().st_size / 1024
    print(f"Wrote {OUT_FILE} ({size_kb:.0f} KB)")
    print(f"  {output['n_modules']} modules")
    print(f"  {output['n_profiles']} profiles")
    print(f"  {output['n_transitions']} transitions")
    print(f"  {len(lineage)} lineage entries")


if __name__ == "__main__":
    main()
