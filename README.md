# MII Kerndatensatz Version History

> ⚠️ **WORK IN PROGRESS — Inhalte und Analysen noch nicht finalisiert**
>
> Dieses Repository ist in aktiver Entwicklung. Daten, Klassifikationen und
> Visualisierungen sind **noch nicht reviewed** und können Fehler enthalten.
> Insbesondere enthält die Analyse bekannte False-Positives durch Build-Pipeline-
> Artefakte (nicht-reproduzierbare Snapshots), die noch manuell kuratiert werden
> müssen. Nicht für produktive Entscheidungen verwenden, bevor die Inhalte
> validiert sind.

Retrospektive Analyse der publizierten FHIR-Profilversionen des MII Kerndatensatzes
(2019–2026), mit Fokus auf **strukturelle Kompatibilität** zwischen Versionen.

**Live-Explorer:** https://medizininformatik-initiative.github.io/mii-kerndatensatz-versionhistory/

## Was ist hier drin?

| Datei | Inhalt |
|---|---|
| [`docs/index.html`](docs/) | Interaktiver Explorer (Subway-Map-Style) |
| [`docs/data.json`](docs/data.json) | Kompakte Datenbasis für den Explorer |
| [`data/profile-version-matrix.json`](data/profile-version-matrix.json) | Vollständige Versionsmatrix aller Profile |
| [`data/profile-aggregability.csv`](data/profile-aggregability.csv) | Kompatibilitätsgruppen (für FDPG-Tooling) |
| [`data/profile-pairwise-changes.csv`](data/profile-pairwise-changes.csv) | Alle Versions-Übergänge im Detail |
| [`data/profile-lineage.csv`](data/profile-lineage.csv) | Legacy→Base Konsolidierung |
| [`data/breaking-change-classification.json`](data/breaking-change-classification.json) | Breaking Changes klassifiziert nach Ursache |
| [`data/maturity-model.json`](data/maturity-model.json) | KDS Specification Maturity Scores pro Modul |
| [`data/profile-element-index.json`](data/profile-element-index.json) | Element-Fingerprints pro Profil/Version |

## Zentrale Konzepte

### Kompatibilitätsgruppe

Aufeinanderfolgende Versionen eines Profils, deren strukturelle Definition
(Differential) **identisch** oder nur in **Metadaten** (URL, Version, Titel) verschieden ist.
Daten von Standorten innerhalb einer Gruppe sind direkt aggregierbar — ohne Mapping.

Beispiel: `ObservationLab` hat 14 publizierte Versionen, aber nur 6 echte strukturelle
Stände — die anderen 8 sind reine Versionsbumps.

### Breaking Change Klassifikation

| Kategorie | Bedeutung |
|---|---|
| `expected` | Major-Version-Bump — SemVer-konform |
| `transition` | Konsolidierung (z.B. person/diagnose/fall/prozedur → base) |
| `neutral` | Externe Dependency wurde gebumpt (DE-Basisprofil, ISiK, ...) |
| `concerning` | Minor-Version-Bump mit Breaking Change |
| `problematic` | Patch-Version-Bump mit Breaking Change — SemVer-Verletzung |

## Nachbauen

```bash
# Dependencies
pip install -r requirements.txt

# Packages von Simplifier ziehen + analysieren (dauert ~5 Min beim ersten Lauf)
python3 scripts/analyze.py --stable-only

# Element-Index bauen
python3 scripts/build-element-index.py

# Explorer-Daten bauen
python3 scripts/build-explorer-data.py

# Lokal betrachten
cd docs && python3 -m http.server 8765
# → http://localhost:8765/
```

## Datenquelle

Alle Daten stammen aus dem öffentlichen
[Simplifier FHIR Package Registry](https://packages.simplifier.net/).
Es werden keine nicht-öffentlichen Quellen verwendet.

## Use Cases

- **FDPG-Tooling**: `profile-aggregability.csv` liefert für jede Profilversion
  die Kompatibilitätsgruppe — damit kann entschieden werden welche Standort-Daten
  aggregierbar sind.
- **Standort-Integration**: Überblick welche Profil-Versionen ein DIZ gerade nutzt
  und welche Version-Sprünge Breaking Changes enthalten.
- **Release-Planung**: Welche Dependency-Updates haben in der Vergangenheit
  Kaskaden ausgelöst.
- **Kommunikation/Review**: Screenshots und teilbare Links für Folien, Reviews
  und Diskussionen.

## Lizenz

Apache License 2.0 — siehe [LICENSE](LICENSE)
