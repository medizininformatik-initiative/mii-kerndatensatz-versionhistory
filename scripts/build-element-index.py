#!/usr/bin/env python3
"""Build a structural element index from all cached MII FHIR packages.

For each profile version, extracts a normalized fingerprint of all
differential elements with the information needed for compatibility
inference: cardinalities, fixed/pattern values, types, slicing
discriminators.

Output: output/version-comparison/profile-element-index.json
"""
from __future__ import annotations

import json
import re
import sys
import tarfile
from pathlib import Path
from collections import defaultdict

CACHE = Path(".cache/fhir-packages/tarballs")

# Optionale Steuerdateien: --allow-versions <BOM package.json>, --ignore-versions <ignored-versions.json>
import argparse as _ap
_p = _ap.ArgumentParser()
_p.add_argument("--allow-versions")
_p.add_argument("--ignore-versions")
_args, _ = _p.parse_known_args()
ALLOW, IGNORE = {}, {}
if _args.allow_versions:
    _d = json.load(open(_args.allow_versions, encoding="utf-8"))
    ALLOW = _d.get("dependencies", _d)
if _args.ignore_versions:
    _d = json.load(open(_args.ignore_versions, encoding="utf-8"))
    IGNORE = {k: set(v.keys() if isinstance(v, dict) else v)
              for k, v in _d.items() if not k.startswith("_")}
MII_PREFIX = "de.medizininformatikinitiative.kerndatensatz."
OUTPUT = Path("output/version-comparison/profile-element-index.json")


def parse_semver_key(v: str) -> tuple:
    parts = re.split(r'[.\-]', v)
    result = []
    for p in parts:
        try:
            result.append((0, int(p)))
        except ValueError:
            result.append((1, p))
    return tuple(result)


def is_stable(v: str) -> bool:
    return not any(t in v.lower() for t in ("alpha", "ballot", "rc", "beta", "draft", "snapshot"))


def base_path(elem_id: str) -> str:
    """Strip slice names: Observation.category.coding:loinc → Observation.category.coding"""
    parts = []
    for p in elem_id.split("."):
        if ":" in p:
            parts.append(p.split(":")[0])
        else:
            parts.append(p)
    return ".".join(parts)


def extract_fingerprint(elem: dict) -> dict:
    """Extract a comparison-ready fingerprint of one element definition."""
    fp = {
        "id": elem.get("id", ""),
        "path": elem.get("path", ""),
        "base_path": base_path(elem.get("id", "")),
        "min": elem.get("min"),
        "max": elem.get("max"),
        "must_support": elem.get("mustSupport", False),
    }

    # Slice info
    if "sliceName" in elem:
        fp["slice_name"] = elem["sliceName"]
    if "slicing" in elem:
        slicing = elem["slicing"]
        fp["is_slicing_root"] = True
        fp["slicing_discriminators"] = [
            {"type": d.get("type"), "path": d.get("path")}
            for d in slicing.get("discriminator", [])
        ]
        fp["slicing_rules"] = slicing.get("rules")

    # Types
    types = []
    type_profiles = []
    target_profiles = []
    for t in elem.get("type", []):
        code = t.get("code")
        if code:
            types.append(code)
        for p in t.get("profile", []):
            type_profiles.append(p)
        for p in t.get("targetProfile", []):
            target_profiles.append(p)
    if types:
        fp["types"] = sorted(types)
    if type_profiles:
        fp["type_profiles"] = sorted(type_profiles)
    if target_profiles:
        fp["target_profiles"] = sorted(target_profiles)

    # Bindings
    binding = elem.get("binding", {})
    if binding:
        fp["binding_strength"] = binding.get("strength")
        fp["binding_value_set"] = binding.get("valueSet")

    # Fixed/pattern values — these are the discriminator values
    for key, val in elem.items():
        if key.startswith("fixed") and key != "fixed":
            fp["fixed_kind"] = key
            fp["fixed_value"] = val
        elif key.startswith("pattern") and key != "pattern":
            fp["pattern_kind"] = key
            fp["pattern_value"] = val

    return fp


def extract_sds(tgz_path: Path) -> list[dict]:
    sds = []
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for m in tar.getmembers():
                if not m.name.endswith(".json"):
                    continue
                if "/examples/" in m.name or "/example/" in m.name:
                    continue
                if not m.name.startswith("package/"):
                    continue
                bn = m.name.split("/")[-1]
                if bn in ("package.json", ".index.json", "ImplementationGuide.json"):
                    continue
                skip_prefixes = ("CodeSystem", "ValueSet", "SearchParameter",
                                 "NamingSystem", "CapabilityStatement",
                                 "ConceptMap", "OperationDefinition")
                if any(bn.startswith(p) for p in skip_prefixes):
                    continue
                f = tar.extractfile(m)
                if f:
                    try:
                        sd = json.loads(f.read())
                        if (isinstance(sd, dict)
                                and sd.get("resourceType") == "StructureDefinition"
                                and sd.get("kind") in ("resource", "complex-type")
                                and sd.get("derivation") == "constraint"
                                and sd.get("type") != "Extension"):
                            sds.append(sd)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
    except (tarfile.TarError, EOFError):
        pass
    return sds


def main():
    if not CACHE.exists():
        print(f"Cache not found: {CACHE}", file=sys.stderr)
        print("Run scripts/compare-profile-versions.py first to populate cache.", file=sys.stderr)
        sys.exit(1)

    index = {}

    pkg_dirs = sorted(d for d in CACHE.iterdir()
                      if d.is_dir() and d.name.startswith(MII_PREFIX))

    for pkg_dir in pkg_dirs:
        package_id = pkg_dir.name
        short = package_id[len(MII_PREFIX):]

        versions = sorted(
            [f.stem for f in pkg_dir.glob("*.tgz") if f.stat().st_size > 0],
            key=parse_semver_key,
        )
        pool = [v for v in versions if v not in IGNORE.get(pkg_dir.name, set())]
        stable = [v for v in pool
                  if is_stable(v) or "alpha" in v.lower() or ALLOW.get(pkg_dir.name) == v]
        nonrc = [v for v in pool if "rc" not in v.lower()]
        if len(stable) < 2 <= len(nonrc):
            stable = nonrc
        if not stable:
            continue

        index[short] = {
            "package_id": package_id,
            "versions": {},
        }

        for v in stable:
            tgz = pkg_dir / f"{v}.tgz"
            sds = extract_sds(tgz)

            profiles = {}
            for sd in sds:
                url = sd.get("url", "")
                if not url:
                    continue

                diff_elements = sd.get("differential", {}).get("element", [])
                fingerprints = [extract_fingerprint(e) for e in diff_elements]

                profiles[url] = {
                    "id": sd.get("id"),
                    "name": sd.get("name"),
                    "resource_type": sd.get("type"),
                    "base_definition": sd.get("baseDefinition"),
                    "elements": fingerprints,
                }

            if profiles:
                index[short]["versions"][v] = profiles

        n_versions = len(index[short]["versions"])
        n_profiles = sum(len(v) for v in index[short]["versions"].values())
        print(f"  {short:20s}  {n_versions:3d} versions, {n_profiles:5d} profile-version entries")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(index, f, ensure_ascii=False)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    n_modules = len(index)
    n_total = sum(
        len(profs)
        for mod in index.values()
        for profs in mod["versions"].values()
    )
    print()
    print(f"Wrote index: {OUTPUT}")
    print(f"  {size_mb:.1f} MB, {n_modules} modules, {n_total} profile-version entries")


if __name__ == "__main__":
    main()
