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
(2019–2027), mit Fokus auf **strukturelle Kompatibilität** zwischen Versionen.
Die 2027er Ballot-Linie ist enthalten, gepinnt über die
[Complete-BOM](https://github.com/medizininformatik-initiative/kerndatensatz-complete).

**Live-Explorer:** https://medizininformatik-initiative.github.io/mii-kerndatensatz-versionhistory/

## 📣 Feedback erwünscht

Der Explorer ist so weit, dass er sich lohnt anzusehen — bevor die Inhalte
verbindlich werden, brauchen wir Fachurteile. Drei Stellen sind besonders
wertvoll:

1. **Sind die erkannten Umbenennungen richtig?**
   [`data/rename-candidates.csv`](data/rename-candidates.csv) — 343 beendete
   Canonicals, 303 mit automatisch vorgeschlagenem Nachfolger (Ähnlichkeit über
   Element-Fingerprints). Die Spalte `confirmed` (`yes`/`no`) ist die
   Kuratierung; bestätigte Zeilen werden zu durchgehenden Linien im Explorer und
   zu `equivalent`-Einträgen in den Migrations-ConceptMaps der BOM.
2. **Wo sind Profile modulübergreifend aufgegangen?**
   [`data/cross-module-candidates.csv`](data/cross-module-candidates.csv) — 63
   Kandidaten. Das ist Fachwissen, das kein Algorithmus aus den Dateien zieht
   (Beispiel: `MII_PR_Seltene_Studie` ging im Modul Studie auf, teilt mit dessen
   Profil aber fast keine Elemente).
3. **Stimmt die ICU↔ISiK-Governance-Zuordnung?**
   [`data/icu-isik-governance.csv`](data/icu-isik-governance.csv) — ISiK 6 bettet
   83 MII-benannte Profile ein: 29 in Doppel-Governance, 49 übergegangen, 4 direkt
   in ISiK ausspezifiziert. Die Zuordnung betrifft **nur URLs** und ist keine
   Konformitätsaussage.

Rückmeldungen gern als Issue, PR auf die CSVs oder direkt an das KDS-Team.

## Was der Explorer zeigt

- **Subway-Map je Modul**: eine Linie pro Profil, eine Station pro Version.
  Farbe der Strecke = Art der Änderung, dicke Umsteigepunkte = Breaking Changes.
- **Feld-genaue Diffs**: Klick auf eine Strecke zeigt, *was* sich geändert hat —
  Kardinalität, Must Support, Typ, Referenzziel, Binding, fixed/pattern, Slicing,
  jeweils alt → neu. Verschärfungen sind rot markiert: dort können bestehende
  Instanzen ungültig werden.
- **Umbenennungen als eine Linie**: Profile, die ihre Canonical gewechselt haben,
  erscheinen als durchgehende Lane mit `renamed`-Segment statt als zwei Torsos.
  Ein Rename ist strukturell harmlos, für Instanzen aber breaking (`meta.profile`
  zeigt ins Leere) — beides wird getrennt ausgewiesen.
- **Governance-Übergänge**: ICU-Profile, die in ISiK 6 weitergeführt werden,
  enden in einem „ISiK 6"-Terminal statt einfach abzubrechen.
- **Nachfolger-Hinweise**: verschwundene Profile zeigen, in welchem anderen Modul
  ihr Inhalt vermutlich aufgegangen ist.

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
| [`docs/field-diffs.json`](docs/field-diffs.json) | Feld-genaue Änderungen je Übergang (vom Explorer nachgeladen) |
| [`data/rename-candidates.csv`](data/rename-candidates.csv) | **Zur Kuratierung:** Canonical-Umbenennungen mit Ähnlichkeitsscore |
| [`data/cross-module-candidates.csv`](data/cross-module-candidates.csv) | **Zur Kuratierung:** modulübergreifende Nachfolger |
| [`data/icu-isik-governance.csv`](data/icu-isik-governance.csv) | **Zur Kuratierung:** ICU-Profile in ISiK-6-Governance |

## Pipeline neu bauen

```bash
BOM=../kerndatensatz-complete
python3 scripts/analyze.py --stable-only \
    --allow-versions "$BOM/package.json" \
    --ignore-versions "$BOM/ignored-versions.json"
cp output/version-comparison/*.json output/version-comparison/*.csv data/
python3 scripts/build-element-index.py \
    --allow-versions "$BOM/package.json" --ignore-versions "$BOM/ignored-versions.json"
cp output/version-comparison/profile-element-index.json data/
./scripts/propose-renames.py --since 0      # Umbenennungs-Kandidaten
./scripts/propose-cross-module.py           # modulübergreifende Nachfolger
./scripts/build-field-diffs.py              # Feld-genaue Diffs
python3 scripts/build-explorer-data.py      # docs/data.json
```

**Auswahlregel für Versionen:** stabile Releases und die in der BOM gepinnten
Stände; zusätzlich Alphas als publizierte Meilensteine, keine rc-Stände.
Bekannte Fehlpublikationen stehen in der `ignored-versions.json` der BOM
(aktuell `icu 2027.0.0`) und werden übersprungen.

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
