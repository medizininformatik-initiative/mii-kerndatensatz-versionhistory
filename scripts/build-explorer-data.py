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


def version_year(v: str) -> int:
    """Heuristic: map version string to approximate release year."""
    if v.startswith("0.0"):
        return 2019
    if v.startswith("0.9"):
        return 2020
    if v.startswith("1.0"):
        return 2021
    if v.startswith("2.0"):
        return 2022
    if v.startswith("2024"):
        return 2024
    if v.startswith("2025"):
        return 2025
    if v.startswith("2026"):
        return 2026
    if v.startswith("1."):
        return 2022
    return 2021


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
