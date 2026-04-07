#!/usr/bin/env python3
"""Compare MII KDS FHIR profiles across all historical package versions.

Downloads all available package versions from the Simplifier registry,
extracts StructureDefinitions, and produces a version comparison matrix
showing which profiles are identical, have only canonical changes,
or have structural additions/removals.

Output is designed for FDPG to determine data aggregability across sites
running different package versions.

Usage:
    python3 scripts/compare-profile-versions.py [options]

    # All packages, stable versions only
    python3 scripts/compare-profile-versions.py --stable-only

    # Specific modules
    python3 scripts/compare-profile-versions.py --packages laborbefund,onkologie

    # Only recent versions
    python3 scripts/compare-profile-versions.py --since 2024.0.0 --stable-only

    # Use existing cache only (no downloads)
    python3 scripts/compare-profile-versions.py --cache-only
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────────

MII_PREFIX = "de.medizininformatikinitiative.kerndatensatz."
SIMPLIFIER_REGISTRY = "https://packages.simplifier.net/"

# All known MII KDS packages
MII_PACKAGES = {
    "meta": f"{MII_PREFIX}meta",
    "base": f"{MII_PREFIX}base",
    "person": f"{MII_PREFIX}person",
    "diagnose": f"{MII_PREFIX}diagnose",
    "fall": f"{MII_PREFIX}fall",
    "prozedur": f"{MII_PREFIX}prozedur",
    "laborbefund": f"{MII_PREFIX}laborbefund",
    "medikation": f"{MII_PREFIX}medikation",
    "biobank": f"{MII_PREFIX}biobank",
    "consent": f"{MII_PREFIX}consent",
    "icu": f"{MII_PREFIX}icu",
    "studie": f"{MII_PREFIX}studie",
    "onkologie": f"{MII_PREFIX}onkologie",
    "molgen": f"{MII_PREFIX}molgen",
    "patho": f"{MII_PREFIX}patho",
    "mikrobiologie": f"{MII_PREFIX}mikrobiologie",
    "bildgebung": f"{MII_PREFIX}bildgebung",
    "dokument": f"{MII_PREFIX}dokument",
    "pros": f"{MII_PREFIX}pros",
    "mtb": f"{MII_PREFIX}mtb",
    "seltene": f"{MII_PREFIX}seltene",
    "symptom": f"{MII_PREFIX}symptom",
    "kardiologie": f"{MII_PREFIX}kardiologie",
    "lungenfunktion": f"{MII_PREFIX}lungenfunktion",
}

# Legacy packages consolidated into base in 2026
LEGACY_CONSOLIDATED = {"person", "diagnose", "fall", "prozedur"}

# Lineage: maps (legacy_package, profile_url) → base profile_url for profiles
# that continued into the base package (same or renamed URL).
# Profiles with identical URLs across legacy→base are auto-detected;
# this map handles URL renames during the consolidation.
MII_CANONICAL_BASE = "https://www.medizininformatik-initiative.de/fhir/core"
LINEAGE_URL_RENAMES = {
    # old URL → new URL (within same logical profile, URL changed over time)
    f"{MII_CANONICAL_BASE}/StructureDefinition/Patient":
        f"{MII_CANONICAL_BASE}/modul-person/StructureDefinition/Patient",
    f"{MII_CANONICAL_BASE}/StructureDefinition/Vitalstatus":
        f"{MII_CANONICAL_BASE}/modul-person/StructureDefinition/Vitalstatus",
    f"{MII_CANONICAL_BASE}/StructureDefinition/Diagnose":
        f"{MII_CANONICAL_BASE}/modul-diagnose/StructureDefinition/Diagnose",
    f"{MII_CANONICAL_BASE}/StructureDefinition/Procedure":
        f"{MII_CANONICAL_BASE}/modul-prozedur/StructureDefinition/Procedure",
    # Fall had multiple old profiles consolidated into KontaktGesundheitseinrichtung
    f"{MII_CANONICAL_BASE}/StructureDefinition/Encounter/Versorgungsfall":
        f"{MII_CANONICAL_BASE}/modul-fall/StructureDefinition/KontaktGesundheitseinrichtung",
}

# Metadata fields to strip before structural comparison
METADATA_FIELDS = {
    "version", "date", "publisher", "contact", "description", "purpose",
    "title", "copyright", "text", "meta", "implicitRules", "language",
    "jurisdiction", "useContext", "keyword", "fhirVersion",
    "_title", "_description", "_purpose", "_copyright",
}

# Fields that count as "canonical-only" changes (not structural)
CANONICAL_FIELDS = {"url", "name", "status", "experimental", "id"} | METADATA_FIELDS


# ── Data Classes ───────────────────────────────────────────────────────────

@dataclass
class ElementFingerprint:
    """Structural fingerprint of a single element in a differential."""
    element_id: str
    path: str
    min: int | None = None
    max: str | None = None
    must_support: bool = False
    types: list[str] = field(default_factory=list)
    type_profiles: list[str] = field(default_factory=list)
    target_profiles: list[str] = field(default_factory=list)
    binding_strength: str | None = None
    binding_valueset: str | None = None
    fixed_value: Any = None
    pattern_value: Any = None
    slice_name: str | None = None
    slicing: dict | None = None

    def to_dict(self) -> dict:
        d = {
            "id": self.element_id,
            "path": self.path,
        }
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        if self.must_support:
            d["mustSupport"] = True
        if self.types:
            d["types"] = self.types
        if self.type_profiles:
            d["typeProfiles"] = self.type_profiles
        if self.target_profiles:
            d["targetProfiles"] = self.target_profiles
        if self.binding_strength:
            d["bindingStrength"] = self.binding_strength
        if self.binding_valueset:
            d["bindingValueSet"] = self.binding_valueset
        if self.fixed_value is not None:
            d["fixedValue"] = self.fixed_value
        if self.pattern_value is not None:
            d["patternValue"] = self.pattern_value
        if self.slice_name:
            d["sliceName"] = self.slice_name
        if self.slicing:
            d["slicing"] = self.slicing
        return d


@dataclass
class ProfileInfo:
    """Extracted profile information from a StructureDefinition."""
    sd_id: str
    url: str
    version: str
    name: str
    title: str
    status: str
    resource_type: str
    base_definition: str
    elements: list[ElementFingerprint] = field(default_factory=list)
    raw_differential: list[dict] = field(default_factory=list)


@dataclass
class ComparisonResult:
    """Result of comparing two profile versions."""
    category: str  # identical, canonical-only, elements-added, elements-removed, mixed, modified
    elements_added: list[str] = field(default_factory=list)
    elements_removed: list[str] = field(default_factory=list)
    elements_modified: list[dict] = field(default_factory=list)
    ms_added: list[str] = field(default_factory=list)
    ms_removed: list[str] = field(default_factory=list)
    cardinality_changes: list[dict] = field(default_factory=list)
    binding_changes: list[dict] = field(default_factory=list)
    has_breaking: bool = False
    detail: str = ""


# ── Registry Client ────────────────────────────────────────────────────────

def fetch_package_versions(package_id: str) -> dict:
    """Fetch all versions of a package from the Simplifier registry."""
    url = SIMPLIFIER_REGISTRY + package_id
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("versions", {})
    except Exception as e:
        print(f"  Warning: Could not fetch {package_id}: {e}", file=sys.stderr)
        return {}


def is_stable_version(version: str) -> bool:
    """Check if a version string represents a stable release."""
    unstable = ("alpha", "ballot", "rc", "beta", "draft", "snapshot", "dev")
    v_lower = version.lower()
    return not any(tag in v_lower for tag in unstable)


def parse_semver_key(version: str) -> tuple:
    """Parse a version string into a sortable tuple."""
    # Handle YYYY.X.Y format and classic X.Y.Z
    parts = re.split(r'[.\-]', version)
    result = []
    for p in parts:
        try:
            result.append((0, int(p)))
        except ValueError:
            result.append((1, p))
    return tuple(result)


# ── Package Download & Cache ───────────────────────────────────────────────

def download_package(package_id: str, version: str, cache_dir: Path,
                     max_retries: int = 3) -> Path | None:
    """Download a package tarball to the cache directory with retry on 429."""
    pkg_cache = cache_dir / "tarballs" / package_id
    pkg_cache.mkdir(parents=True, exist_ok=True)
    tgz_path = pkg_cache / f"{version}.tgz"

    if tgz_path.exists() and tgz_path.stat().st_size > 0:
        return tgz_path

    url = f"{SIMPLIFIER_REGISTRY}{package_id}/{version}"
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                tgz_path.write_bytes(data)
            return tgz_path
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                time.sleep(wait)
                continue
            print(f"  Warning: Download failed {package_id}@{version}: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"  Warning: Download failed {package_id}@{version}: {e}", file=sys.stderr)
            return None


def extract_structure_definitions(tgz_path: Path) -> list[dict]:
    """Extract StructureDefinition JSON files from a package tarball.

    Handles multiple naming conventions:
    - StructureDefinition-*.json (standard)
    - Profile_*.json (older consent, mikrobiologie packages)
    - MII_PR_*.json, MII-PR-*.json (other variants)
    - Any .json file in package/ that contains resourceType: StructureDefinition
    """
    sds = []
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                # Skip examples and non-JSON
                if not name.endswith(".json"):
                    continue
                if "/examples/" in name or "/example/" in name:
                    continue
                # Must be in package/ directory (top-level)
                if not name.startswith("package/"):
                    continue
                # Skip known non-SD files
                basename = name.split("/")[-1]
                if basename in ("package.json", ".index.json", "ImplementationGuide.json"):
                    continue
                # Skip CodeSystem, ValueSet, SearchParameter etc by name prefix
                skip_prefixes = ("CodeSystem", "ValueSet", "SearchParameter",
                                 "NamingSystem", "CapabilityStatement",
                                 "ConceptMap", "OperationDefinition")
                if any(basename.startswith(p) for p in skip_prefixes):
                    continue

                f = tar.extractfile(member)
                if f:
                    try:
                        sd = json.loads(f.read())
                        if isinstance(sd, dict) and sd.get("resourceType") == "StructureDefinition":
                            sds.append(sd)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
    except (tarfile.TarError, EOFError) as e:
        print(f"  Warning: Could not extract {tgz_path}: {e}", file=sys.stderr)
    return sds


# ── Profile Extraction ─────────────────────────────────────────────────────

def extract_element_fingerprint(elem: dict) -> ElementFingerprint:
    """Extract a structural fingerprint from an element definition."""
    types = []
    type_profiles = []
    target_profiles = []
    for t in elem.get("type", []):
        code = t.get("code", "")
        if code:
            types.append(code)
        for p in t.get("profile", []):
            type_profiles.append(p)
        for p in t.get("targetProfile", []):
            target_profiles.append(p)

    # Extract fixed[x] and pattern[x] values
    fixed_value = None
    pattern_value = None
    for key, val in elem.items():
        if key.startswith("fixed") and key != "fixed":
            fixed_value = val
        elif key.startswith("pattern") and key != "pattern":
            pattern_value = val

    slicing = None
    if "slicing" in elem:
        slicing = elem["slicing"]

    return ElementFingerprint(
        element_id=elem.get("id", ""),
        path=elem.get("path", ""),
        min=elem.get("min"),
        max=elem.get("max"),
        must_support=elem.get("mustSupport", False),
        types=sorted(types),
        type_profiles=sorted(type_profiles),
        target_profiles=sorted(target_profiles),
        binding_strength=elem.get("binding", {}).get("strength"),
        binding_valueset=elem.get("binding", {}).get("valueSet"),
        fixed_value=fixed_value,
        pattern_value=pattern_value,
        slice_name=elem.get("sliceName"),
        slicing=slicing,
    )


def extract_profile(sd: dict) -> ProfileInfo | None:
    """Extract profile information from a StructureDefinition."""
    # Only profiles (resource constraints), not extensions or logical models
    kind = sd.get("kind", "")
    derivation = sd.get("derivation", "")

    if kind not in ("resource", "complex-type"):
        return None
    # Include profiles (constraint) and extensions
    if derivation != "constraint":
        return None
    # Skip Extension definitions for now (focus on resource profiles)
    if sd.get("type") == "Extension":
        return None

    diff_elements = sd.get("differential", {}).get("element", [])
    elements = [extract_element_fingerprint(e) for e in diff_elements]

    return ProfileInfo(
        sd_id=sd.get("id", ""),
        url=sd.get("url", ""),
        version=sd.get("version", ""),
        name=sd.get("name", ""),
        title=sd.get("title", ""),
        status=sd.get("status", ""),
        resource_type=sd.get("type", ""),
        base_definition=sd.get("baseDefinition", ""),
        elements=elements,
        raw_differential=diff_elements,
    )


# ── Comparison Engine ──────────────────────────────────────────────────────

def normalize_for_structural_compare(elements: list[ElementFingerprint]) -> dict[str, dict]:
    """Create a normalized dict of elements keyed by element ID for comparison."""
    result = {}
    for elem in elements:
        result[elem.element_id] = elem.to_dict()
    return result


def compare_profiles(profile_a: ProfileInfo, profile_b: ProfileInfo) -> ComparisonResult:
    """Compare two versions of the same profile."""
    # Step 1: Check if differentials are completely identical
    if (json.dumps(profile_a.raw_differential, sort_keys=True)
            == json.dumps(profile_b.raw_differential, sort_keys=True)):
        # Check if metadata also identical
        meta_same = (
            profile_a.url == profile_b.url
            and profile_a.name == profile_b.name
            and profile_a.status == profile_b.status
        )
        if meta_same:
            return ComparisonResult(category="identical")
        return ComparisonResult(category="canonical-only",
                                detail="Only URL/name/status/version metadata changed")

    # Step 2: Normalize and compare elements
    elems_a = normalize_for_structural_compare(profile_a.elements)
    elems_b = normalize_for_structural_compare(profile_b.elements)

    ids_a = set(elems_a.keys())
    ids_b = set(elems_b.keys())

    added_ids = ids_b - ids_a
    removed_ids = ids_a - ids_b
    common_ids = ids_a & ids_b

    # Check modifications in common elements
    elements_modified = []
    ms_added = []
    ms_removed = []
    cardinality_changes = []
    binding_changes = []
    has_breaking = False

    for eid in sorted(common_ids):
        ea = elems_a[eid]
        eb = elems_b[eid]

        # Strip element id/path for comparison (they should be same)
        ca = {k: v for k, v in ea.items() if k not in ("id", "path")}
        cb = {k: v for k, v in eb.items() if k not in ("id", "path")}

        if json.dumps(ca, sort_keys=True) != json.dumps(cb, sort_keys=True):
            mod = {"element_id": eid, "changes": []}

            # mustSupport changes
            if ea.get("mustSupport") != eb.get("mustSupport"):
                if eb.get("mustSupport") and not ea.get("mustSupport"):
                    ms_added.append(eid)
                    mod["changes"].append("mustSupport added")
                elif ea.get("mustSupport") and not eb.get("mustSupport"):
                    ms_removed.append(eid)
                    mod["changes"].append("mustSupport removed")

            # Cardinality changes
            if ea.get("min") != eb.get("min") or ea.get("max") != eb.get("max"):
                change = {
                    "element": eid,
                    "from": f"{ea.get('min', '?')}..{ea.get('max', '?')}",
                    "to": f"{eb.get('min', '?')}..{eb.get('max', '?')}",
                }
                # Breaking: min increased or max decreased
                min_a = ea.get("min", 0) or 0
                min_b = eb.get("min", 0) or 0
                max_a = ea.get("max", "*")
                max_b = eb.get("max", "*")
                if min_b > min_a:
                    change["breaking"] = True
                    has_breaking = True
                if max_a == "*" and max_b != "*":
                    change["breaking"] = True
                    has_breaking = True
                elif max_a != "*" and max_b != "*":
                    try:
                        if int(max_b) < int(max_a):
                            change["breaking"] = True
                            has_breaking = True
                    except ValueError:
                        pass
                cardinality_changes.append(change)
                mod["changes"].append(f"cardinality {change['from']} → {change['to']}")

            # Binding changes
            bs_a = ea.get("bindingStrength")
            bs_b = eb.get("bindingStrength")
            if bs_a != bs_b:
                strength_order = {"example": 0, "preferred": 1, "extensible": 2, "required": 3}
                change = {
                    "element": eid,
                    "from_strength": bs_a,
                    "to_strength": bs_b,
                    "from_valueset": ea.get("bindingValueSet"),
                    "to_valueset": eb.get("bindingValueSet"),
                }
                if (strength_order.get(bs_b, -1) > strength_order.get(bs_a, -1)):
                    change["breaking"] = True
                    has_breaking = True
                binding_changes.append(change)
                mod["changes"].append(f"binding {bs_a} → {bs_b}")

            if ea.get("bindingValueSet") != eb.get("bindingValueSet") and bs_a == bs_b:
                binding_changes.append({
                    "element": eid,
                    "from_strength": bs_a,
                    "to_strength": bs_b,
                    "from_valueset": ea.get("bindingValueSet"),
                    "to_valueset": eb.get("bindingValueSet"),
                })

            # Type changes
            if ea.get("types") != eb.get("types"):
                mod["changes"].append(f"types changed")
                # Fewer types = breaking
                if set(ea.get("types", [])) - set(eb.get("types", [])):
                    has_breaking = True

            if mod["changes"]:
                elements_modified.append(mod)

    # Step 3: If no structural changes found, check if it's metadata-only
    if not added_ids and not removed_ids and not elements_modified:
        return ComparisonResult(category="canonical-only",
                                detail="Differential elements match but metadata differs")

    # Step 4: Classify
    elements_added = sorted(added_ids)
    elements_removed = sorted(removed_ids)

    if removed_ids:
        has_breaking = True

    if elements_removed and not elements_added and not elements_modified:
        category = "elements-removed"
    elif elements_added and not elements_removed and not elements_modified:
        category = "elements-added"
    elif elements_modified and not elements_added and not elements_removed:
        category = "modified"
    else:
        category = "mixed"

    return ComparisonResult(
        category=category,
        elements_added=elements_added,
        elements_removed=elements_removed,
        elements_modified=elements_modified,
        ms_added=ms_added,
        ms_removed=ms_removed,
        cardinality_changes=cardinality_changes,
        binding_changes=binding_changes,
        has_breaking=has_breaking,
    )


# ── Compatibility Grouping ─────────────────────────────────────────────────

def build_compatibility_groups(
    version_profiles: dict[str, ProfileInfo],
    sorted_versions: list[str],
) -> tuple[list[dict], list[dict]]:
    """Build compatibility groups and pairwise comparisons for a profile across versions.

    Returns (compatibility_groups, pairwise_comparisons).
    """
    if len(sorted_versions) < 2:
        if sorted_versions:
            return ([{"versions": sorted_versions, "category": "sole-version"}], [])
        return ([], [])

    pairwise = []
    for i in range(len(sorted_versions) - 1):
        v_from = sorted_versions[i]
        v_to = sorted_versions[i + 1]
        result = compare_profiles(version_profiles[v_from], version_profiles[v_to])
        pairwise.append({
            "from": v_from,
            "to": v_to,
            "category": result.category,
            "elements_added": result.elements_added,
            "elements_removed": result.elements_removed,
            "elements_modified": [m["element_id"] for m in result.elements_modified],
            "ms_added": result.ms_added,
            "ms_removed": result.ms_removed,
            "cardinality_changes": result.cardinality_changes,
            "binding_changes": result.binding_changes,
            "has_breaking": result.has_breaking,
        })

    # Build groups: consecutive versions that are identical or canonical-only
    groups = []
    current_group = [sorted_versions[0]]
    current_cat = "identical"

    for pw in pairwise:
        if pw["category"] in ("identical", "canonical-only"):
            current_group.append(pw["to"])
            # Group category is the "weakest" link
            if pw["category"] == "canonical-only":
                current_cat = "canonical-only"
        else:
            groups.append({
                "versions": current_group,
                "category": current_cat,
            })
            current_group = [pw["to"]]
            current_cat = "identical"

    groups.append({
        "versions": current_group,
        "category": current_cat,
    })

    return groups, pairwise


# ── Lineage Helpers ─────────────────────────────────────────────────────────

def _collect_legacy_profiles(
    legacy_pkg: str,
    package_profiles: dict,
    legacy_versions: list[str],
) -> list[tuple[str, dict[str, ProfileInfo]]]:
    """Collect all profile URLs from a legacy package with their per-version ProfileInfo.

    Returns list of (profile_url, {version: ProfileInfo}) tuples.
    """
    all_urls = set()
    for v in legacy_versions:
        for url in package_profiles[legacy_pkg].get(v, {}):
            all_urls.add(url)

    result = []
    for url in sorted(all_urls):
        profiles_by_ver = {}
        for v in legacy_versions:
            vdata = package_profiles[legacy_pkg].get(v, {})
            if url in vdata:
                profiles_by_ver[v] = vdata[url]
        if profiles_by_ver:
            result.append((url, profiles_by_ver))
    return result


# ── Main Pipeline ──────────────────────────────────────────────────────────

def run_pipeline(args: argparse.Namespace) -> dict:
    """Run the full comparison pipeline."""
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine which packages to process
    if args.packages == "all":
        packages = dict(MII_PACKAGES)
    else:
        selected = [p.strip() for p in args.packages.split(",")]
        packages = {k: v for k, v in MII_PACKAGES.items() if k in selected}

    if not packages:
        print("No packages selected!", file=sys.stderr)
        sys.exit(1)

    print(f"Processing {len(packages)} packages...")
    print()

    # Phase 1: Fetch version lists from registry
    print("Phase 1: Fetching version lists from Simplifier registry...")
    all_versions: dict[str, list[str]] = {}
    for short_name, package_id in sorted(packages.items()):
        if args.cache_only:
            # Discover versions from cached tarballs
            tgz_dir = cache_dir / "tarballs" / package_id
            if tgz_dir.exists():
                versions = [
                    f.stem for f in sorted(tgz_dir.glob("*.tgz"))
                    if f.stat().st_size > 0
                ]
            else:
                versions = []
        else:
            versions_meta = fetch_package_versions(package_id)
            versions = sorted(versions_meta.keys(), key=parse_semver_key)
            time.sleep(0.3)  # Rate limit

        # Apply filters
        if args.stable_only:
            versions = [v for v in versions if is_stable_version(v)]
        if args.since:
            since_key = parse_semver_key(args.since)
            versions = [v for v in versions if parse_semver_key(v) >= since_key]

        if versions:
            all_versions[short_name] = versions
            print(f"  {short_name}: {len(versions)} versions ({versions[0]} → {versions[-1]})")
        else:
            print(f"  {short_name}: no versions found")

    total_downloads = sum(len(v) for v in all_versions.values())
    print(f"\nTotal: {total_downloads} package versions to process")
    print()

    # Phase 2: Download packages
    print("Phase 2: Downloading packages...")
    download_count = 0
    download_errors = 0

    for short_name, versions in sorted(all_versions.items()):
        package_id = packages[short_name]
        cached = 0
        downloaded = 0
        failed = 0

        for version in versions:
            tgz_dir = cache_dir / "tarballs" / package_id
            tgz_path = tgz_dir / f"{version}.tgz"

            if tgz_path.exists() and tgz_path.stat().st_size > 0:
                cached += 1
                continue

            if args.cache_only:
                continue

            result = download_package(package_id, version, cache_dir)
            if result:
                downloaded += 1
                download_count += 1
            else:
                failed += 1
                download_errors += 1
            time.sleep(1.0)  # Rate limit — Simplifier returns 429 below ~1s

        status_parts = []
        if cached:
            status_parts.append(f"{cached} cached")
        if downloaded:
            status_parts.append(f"{downloaded} downloaded")
        if failed:
            status_parts.append(f"{failed} failed")
        print(f"  {short_name}: {', '.join(status_parts) or 'none'}")

    print(f"\nDownloaded {download_count} new packages ({download_errors} errors)")
    print()

    # Phase 3: Extract StructureDefinitions
    print("Phase 3: Extracting StructureDefinitions...")
    # package_profiles[short_name][version][profile_url] = ProfileInfo
    package_profiles: dict[str, dict[str, dict[str, ProfileInfo]]] = {}

    for short_name, versions in sorted(all_versions.items()):
        package_id = packages[short_name]
        package_profiles[short_name] = {}
        profile_count = 0

        for version in versions:
            tgz_path = cache_dir / "tarballs" / package_id / f"{version}.tgz"
            if not tgz_path.exists():
                continue

            sds = extract_structure_definitions(tgz_path)
            version_profiles = {}
            for sd in sds:
                profile = extract_profile(sd)
                if profile:
                    version_profiles[profile.url] = profile
                    profile_count += 1

            if version_profiles:
                package_profiles[short_name][version] = version_profiles

        print(f"  {short_name}: {len(package_profiles[short_name])} versions, {profile_count} profile extractions")

    print()

    # Phase 4: Compare profiles across versions
    print("Phase 4: Comparing profiles across versions...")
    results = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool_version": "1.0.0",
        "parameters": {
            "stable_only": args.stable_only,
            "since": args.since,
            "packages": list(sorted(packages.keys())),
        },
        "packages": {},
    }

    aggregability_rows = []  # For CSV output

    for short_name in sorted(package_profiles.keys()):
        version_data = package_profiles[short_name]
        if not version_data:
            continue

        sorted_versions = sorted(version_data.keys(), key=parse_semver_key)

        # Collect all profile URLs seen across any version
        all_profile_urls = set()
        for vp in version_data.values():
            all_profile_urls.update(vp.keys())

        package_result = {
            "package_id": packages[short_name],
            "versions": sorted_versions,
            "is_legacy": short_name in LEGACY_CONSOLIDATED,
            "profiles": {},
        }

        for profile_url in sorted(all_profile_urls):
            # Collect versions that have this profile
            profile_versions = {}
            for ver in sorted_versions:
                if profile_url in version_data.get(ver, {}):
                    profile_versions[ver] = version_data[ver][profile_url]

            present_versions = sorted(profile_versions.keys(), key=parse_semver_key)
            if not present_versions:
                continue

            first_profile = profile_versions[present_versions[0]]

            groups, pairwise = build_compatibility_groups(profile_versions, present_versions)

            profile_result = {
                "url": profile_url,
                "resource_type": first_profile.resource_type,
                "name": first_profile.name,
                "first_seen": present_versions[0],
                "last_seen": present_versions[-1],
                "version_count": len(present_versions),
                "compatibility_groups": groups,
                "pairwise_comparisons": pairwise,
            }

            package_result["profiles"][profile_url] = profile_result

            # Generate aggregability CSV rows
            for gi, group in enumerate(groups):
                is_latest = present_versions[-1] in group["versions"]
                breaking_from_prev = False
                if gi > 0:
                    # Check if transition into this group was breaking
                    prev_last = groups[gi - 1]["versions"][-1]
                    group_first = group["versions"][0]
                    for pw in pairwise:
                        if pw["from"] == prev_last and pw["to"] == group_first:
                            breaking_from_prev = pw["has_breaking"]
                            break

                aggregability_rows.append({
                    "package": packages[short_name],
                    "package_short": short_name,
                    "profile_url": profile_url,
                    "profile_name": first_profile.name,
                    "resource_type": first_profile.resource_type,
                    "compatibility_group": gi + 1,
                    "group_category": group["category"],
                    "versions": "|".join(group["versions"]),
                    "version_count": len(group["versions"]),
                    "is_latest_group": is_latest,
                    "breaking_from_previous": breaking_from_prev if gi > 0 else "",
                })

        results["packages"][short_name] = package_result
        n_profiles = len(package_result["profiles"])
        n_groups = sum(
            len(p["compatibility_groups"])
            for p in package_result["profiles"].values()
        )
        print(f"  {short_name}: {n_profiles} profiles, {n_groups} compatibility groups")

    print()

    # Phase 4b: Build lineage — merge legacy package profiles into base timeline
    print("Phase 4b: Building legacy→base lineage...")
    lineage_entries = []

    if "base" in package_profiles:
        base_profile_urls = set()
        if "base" in results["packages"]:
            base_profile_urls = set(results["packages"]["base"]["profiles"].keys())

        for legacy_pkg in LEGACY_CONSOLIDATED:
            if legacy_pkg not in package_profiles:
                continue
            legacy_versions = sorted(package_profiles[legacy_pkg].keys(), key=parse_semver_key)
            if not legacy_versions:
                continue

            for legacy_url, legacy_profiles_by_ver in _collect_legacy_profiles(
                legacy_pkg, package_profiles, legacy_versions
            ):
                # Determine continuation URL in base
                continuation_url = None
                if legacy_url in base_profile_urls:
                    continuation_url = legacy_url
                elif legacy_url in LINEAGE_URL_RENAMES:
                    renamed = LINEAGE_URL_RENAMES[legacy_url]
                    if renamed in base_profile_urls:
                        continuation_url = renamed

                if continuation_url is None:
                    continue  # Profile was dropped, not continued in base

                # Get base versions for this profile
                base_sorted = sorted(package_profiles["base"].keys(), key=parse_semver_key)
                base_profiles_by_ver = {}
                for bv in base_sorted:
                    if continuation_url in package_profiles["base"].get(bv, {}):
                        base_profiles_by_ver[bv] = package_profiles["base"][bv][continuation_url]

                if not base_profiles_by_ver:
                    continue

                # Build merged version list: legacy versions + base versions
                all_versions_merged = {}
                for v, p in legacy_profiles_by_ver.items():
                    all_versions_merged[f"{legacy_pkg}@{v}"] = p
                for v, p in base_profiles_by_ver.items():
                    all_versions_merged[f"base@{v}"] = p

                sorted_merged = list(legacy_profiles_by_ver.keys()) + list(base_profiles_by_ver.keys())

                # Compare last legacy version → first base version
                last_legacy_ver = list(legacy_profiles_by_ver.keys())[-1]
                first_base_ver = list(base_profiles_by_ver.keys())[0]
                transition = compare_profiles(
                    legacy_profiles_by_ver[last_legacy_ver],
                    base_profiles_by_ver[first_base_ver],
                )

                first_profile = list(legacy_profiles_by_ver.values())[0]
                lineage_entry = {
                    "profile_name": first_profile.name,
                    "resource_type": first_profile.resource_type,
                    "legacy_package": legacy_pkg,
                    "legacy_url": legacy_url,
                    "base_url": continuation_url,
                    "url_changed": legacy_url != continuation_url,
                    "legacy_versions": list(legacy_profiles_by_ver.keys()),
                    "base_versions": list(base_profiles_by_ver.keys()),
                    "transition": {
                        "from": f"{legacy_pkg}@{last_legacy_ver}",
                        "to": f"base@{first_base_ver}",
                        "category": transition.category,
                        "has_breaking": transition.has_breaking,
                        "elements_added": transition.elements_added,
                        "elements_removed": transition.elements_removed,
                        "elements_modified": [m["element_id"] for m in transition.elements_modified],
                    },
                }
                lineage_entries.append(lineage_entry)
                print(f"  {legacy_pkg}/{first_profile.name}: "
                      f"{len(legacy_profiles_by_ver)} legacy → {len(base_profiles_by_ver)} base "
                      f"({transition.category}{'*' if transition.has_breaking else ''})")

    results["lineage"] = lineage_entries
    if lineage_entries:
        print(f"  {len(lineage_entries)} profiles tracked across legacy→base consolidation")
    else:
        print("  No lineage mappings (base package not in scope or no legacy overlap)")
    print()

    # Phase 5: Write outputs
    print("Phase 5: Writing outputs...")

    # JSON output (full matrix)
    json_path = output_dir / "profile-version-matrix.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  {json_path}")

    # CSV: Aggregability summary (key FDPG deliverable)
    csv_path = output_dir / "profile-aggregability.csv"
    if aggregability_rows:
        fieldnames = [
            "package", "package_short", "profile_url", "profile_name",
            "resource_type", "compatibility_group", "group_category",
            "versions", "version_count", "is_latest_group",
            "breaking_from_previous",
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(aggregability_rows)
        print(f"  {csv_path}")

    # CSV: Pairwise changes (detailed)
    pairwise_rows = []
    for short_name, pkg_data in results["packages"].items():
        for profile_url, prof_data in pkg_data["profiles"].items():
            for pw in prof_data["pairwise_comparisons"]:
                pairwise_rows.append({
                    "package": pkg_data["package_id"],
                    "package_short": short_name,
                    "profile_url": profile_url,
                    "profile_name": prof_data["name"],
                    "resource_type": prof_data["resource_type"],
                    "from_version": pw["from"],
                    "to_version": pw["to"],
                    "category": pw["category"],
                    "elements_added_count": len(pw["elements_added"]),
                    "elements_removed_count": len(pw["elements_removed"]),
                    "elements_modified_count": len(pw["elements_modified"]),
                    "ms_added_count": len(pw["ms_added"]),
                    "ms_removed_count": len(pw["ms_removed"]),
                    "has_breaking": pw["has_breaking"],
                    "elements_added": "|".join(pw["elements_added"]),
                    "elements_removed": "|".join(pw["elements_removed"]),
                })

    pairwise_csv_path = output_dir / "profile-pairwise-changes.csv"
    if pairwise_rows:
        fieldnames = [
            "package", "package_short", "profile_url", "profile_name",
            "resource_type", "from_version", "to_version", "category",
            "elements_added_count", "elements_removed_count",
            "elements_modified_count", "ms_added_count", "ms_removed_count",
            "has_breaking", "elements_added", "elements_removed",
        ]
        with open(pairwise_csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(pairwise_rows)
        print(f"  {pairwise_csv_path}")

    # CSV: Lineage (legacy→base consolidation)
    if results.get("lineage"):
        lineage_csv_path = output_dir / "profile-lineage.csv"
        lineage_rows = []
        for entry in results["lineage"]:
            lineage_rows.append({
                "legacy_package": entry["legacy_package"],
                "profile_name": entry["profile_name"],
                "resource_type": entry["resource_type"],
                "legacy_url": entry["legacy_url"],
                "base_url": entry["base_url"],
                "url_changed": entry["url_changed"],
                "legacy_versions": "|".join(entry["legacy_versions"]),
                "base_versions": "|".join(entry["base_versions"]),
                "transition_category": entry["transition"]["category"],
                "transition_breaking": entry["transition"]["has_breaking"],
                "elements_added": "|".join(entry["transition"]["elements_added"]),
                "elements_removed": "|".join(entry["transition"]["elements_removed"]),
            })
        fieldnames = [
            "legacy_package", "profile_name", "resource_type",
            "legacy_url", "base_url", "url_changed",
            "legacy_versions", "base_versions",
            "transition_category", "transition_breaking",
            "elements_added", "elements_removed",
        ]
        with open(lineage_csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(lineage_rows)
        print(f"  {lineage_csv_path}")

    # Print summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total_profiles = 0
    total_groups = 0
    total_breaking = 0

    for short_name, pkg_data in sorted(results["packages"].items()):
        n_prof = len(pkg_data["profiles"])
        total_profiles += n_prof
        for prof_data in pkg_data["profiles"].values():
            groups = prof_data["compatibility_groups"]
            total_groups += len(groups)
            for pw in prof_data["pairwise_comparisons"]:
                if pw["has_breaking"]:
                    total_breaking += 1

        n_ver = len(pkg_data["versions"])
        legacy = " (LEGACY)" if pkg_data.get("is_legacy") else ""
        print(f"  {short_name:20s} {n_ver:3d} versions  {n_prof:3d} profiles{legacy}")

    print()
    print(f"  Total profiles tracked: {total_profiles}")
    print(f"  Total compatibility groups: {total_groups}")
    print(f"  Total breaking transitions: {total_breaking}")
    print()

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Compare MII KDS FHIR profiles across all historical package versions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--packages", default="all",
        help="Comma-separated short names (e.g., 'laborbefund,onkologie') or 'all' (default: all)",
    )
    parser.add_argument(
        "--stable-only", action="store_true",
        help="Skip alpha/ballot/rc/draft versions",
    )
    parser.add_argument(
        "--since",
        help="Only include versions >= this version (e.g., '2024.0.0')",
    )
    parser.add_argument(
        "--cache-dir", default=".cache/fhir-packages",
        help="Directory for downloaded package cache (default: .cache/fhir-packages)",
    )
    parser.add_argument(
        "--output-dir", default="output/version-comparison",
        help="Output directory (default: output/version-comparison)",
    )
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Only use already-cached packages, don't download",
    )
    args = parser.parse_args()
    run_pipeline(args)


if __name__ == "__main__":
    main()
