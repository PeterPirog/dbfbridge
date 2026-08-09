# AGENTS.md — dbfbridge project context

> Ten plik zawiera pełny kontekst prac dla nowej sesji w PyCharm.
> Przeczytaj go w całości przed kontynuowaniem prac.

## Projekt

**dbfbridge** — dwukierunkowy konwerter plików DBF (Visual FoxPro) ↔ CSV/JSON/JSONL/XLSX z automatycznym fallbackiem polskich stron kodowych (cp1250/cp852/Mazovia).

- **Repozytorium**: https://github.com/PeterPirog/dbfbridge
- **Autor**: Peter Pirog <pirog.peter@gmail.com>
- **Licencja**: MIT
- **Status**: 0.1.0 (alpha — eksport DBF → CSV/JSON/JSONL/XLSX)
- **Cel publikacji**: PyPI

## Historia powstania

Projekt wywodzi się z wewnętrznego zestawu skryptów do migracji danych z systemu opartego na Visual FoxPro 9.0 SP2 (pliki DBF/FPT/CDX z polskimi znakami) do nowoczesnych formatów, a docelowo do bazy Neo4j. Skrypty te zostały uogólnione i wydzielone do samodzielnego pakietu `dbfbridge`.

### Co zostało zrobione w pierwotnym projekcie:

1. **`exporter/`** — streamingowy, atomowy pakiet eksportu DBF → JSONL/CSV
   - `models.py` — dataclassy: `ExportConfig`, `FieldMetadata`, `TableMetadata`, `TableResult`, `StreamStats`
   - `config.py` — `make_config()` walidacja konfiguracji
   - `discovery.py` — `discover_tables()` rekurencyjne znajdowanie DBF + FPT
   - `reader.py` — `LosslessFieldParser` z automatycznym fallback kodowania (cp1250 → cp852 → Mazovia)
   - `serialization.py` — `serialize_record()` z polityką memo (skip/inline/null) i strip_spaces
   - `writer.py` — `export_table()` streamingowy zapis z atomowym rename, walidacją SHA-256
   - `validation.py` — `validate_output()` round-trip parse + statystyki
   - `reporting.py` — `write_reports()` migration_report.jsonl + .csv
   - `polish_codecs.py` — rejestracja Mazovia/PIAST w `codecs` Pythona

2. **`05_dbf_tree_to_csv.py`** — fasada CLI wywołująca `exporter/`, z domyślnymi argumentami dla PyCharm
   - Formaty: csv, json, jsonl (parametr `--formats`)
   - Polityka memo per-format: csv=skip, json/jsonl=inline
   - Domyślne: source/output = synthetic_data/, overwrite=True

3. **`06_verify_conversion.py`** — weryfikacja konwersji
   - Sprawdza: kompletność plików, liczbę rekordów, SHA-256, schema, składnię, FPT/CDX
   - Zapisuje `verification_report.json`

4. **`synthetic_data/generate_sample_dbf.py`** — generator syntetycznych DBF (3 tabele: klienci, zamowienia, archiwum) z polskimi znakami w cp1250

5. **Testy na danych rzeczywistych**: 93 pliki DBF z `K:\dbf_source` — wszystkie 93 OK po dodaniu fallback Mazovia (wcześniej 6 failed na `UnicodeDecodeError` z cp1250)

### Kluczowe decyzje architektoniczne:

- **`dbfread` (nie `dbf`)** do odczytu — streaming, brak ładowania całej tabeli do RAM
- **`dbf` (Ethan Furman)** do zapisu DBF (do użycia w round-trip)
- **CSV bez memo** — pola memo zawierają `\n`, `,`, `"`, `;` które zaburzają proste parsery CSV
- **JSONL z memo inline** — 1 linia = 1 rekord, newline w memo escapowany jako `\n` w JSON
- **Atomowy zapis** — `AtomicTextWriter` pisze do `.partial`, potem `os.replace()` — bezpieczne dla dużych plików
- **Fallback kodowania per-rekord** — `LosslessFieldParser.decode_text()` przechwytuje `UnicodeDecodeError` i próbuje cp1250 → cp852 → mazovia

## Struktura repozytorium dbfbridge

```
dbfbridge/
├── pyproject.toml              # metadane pakietu, zależności, punkty wejścia
├── README.md                   # dokumentacja użytkowa
├── CHANGELOG.md                 # historia zmian
├── AGENTS.md                    # TEN PLIK — kontekst dla nowej sesji
├── LICENSE                      # MIT
├── .gitignore                   # ignoruje *.dbf/*.fpt/*.cdx/*.csv/*.json/*.jsonl
├── src/
│   └── dbf_bridge/
│       ├── __init__.py          # wersja 0.1.0.dev0
│       ├── cli.py               # dbf-bridge (eksport) — z 05_dbf_tree_to_csv.py
│       ├── verifier.py          # dbf-bridge-verify — z 06_verify_conversion.py
│       └── exporter/           # eksporter DBF
│           ├── __init__.py
│           ├── config.py        # make_config()
│           ├── discovery.py     # discover_tables()
│           ├── models.py        # ExportConfig, FieldMetadata, TableMetadata, TableResult
│           ├── polish_codecs.py # Mazovia/PIAST codec registration
│           ├── reader.py        # LosslessFieldParser z fallback kodowania
│           ├── reporting.py     # write_reports()
│           ├── serialization.py # serialize_record() z memo_policy
│           ├── validation.py    # validate_output()
│           └── writer.py        # export_table() streaming atomowy
├── examples/
│   ├── README.md                # instrukcje uruchamiania przykładów
│   ├── export_dbf.py            # eksport K:\dbf_source -> K:\dbf_output (domyślne)
│   └── verify_dbf.py            # weryfikacja konwersji
├── tests/
│   └── fixtures/
│       ├── generate_sample_dbf.py  # generator syntetycznych DBF (3 tabele)
│       ├── input/                  # (generowane lokalnie, gitignored)
│       └── output/                 # (generowane lokalnie, gitignored)
└── docs/                        # (do utworzenia w przyszłości)
```

## Co trzeba zrobić (kolejne kroki)

### Krok 1 — Oczyszczenie i dostosowanie importów (najpilniejsze) — UKOŃCZONE
- [x] Usunąć `src/dbf_bridge/exporter/cli.py` (dubluje `dbf_bridge.cli`)
- [x] Poprawić import w `polish_codecs.py` (`from exporter.` → `from dbf_bridge.exporter.`)
- [x] Utworzyć `.venv` w `dbfbridge/` i zainstalować `pip install -e ".[dev]"`
- [x] Przetestować: `python -m dbf_bridge.cli --help` i `dbf-bridge --help`
- [x] Poprawić domyślne ścieżki w cli.py i verifier.py (`synthetic_data/` → `tests/fixtures/`)
- [x] Przetestować pełny round-trip: generate_sample_dbf → cli → verifier — 3/3 OK

### Krok 2 — Testy na danych syntetycznych — UKOŃCZONE
- [x] Uruchomić `tests/fixtures/generate_sample_dbf.py` (wymaga `pip install dbf`)
- [x] Uruchomić `dbf-bridge --source tests/fixtures/input --output tests/fixtures/output`
- [x] Uruchomić `dbf-bridge-verify` na wyniku — 3/3 OK, 80 rekordów, 0 błędów

### Krok 3 — Implementacja round-trip X → DBF (nowa funkcjonalność)
- [ ] Utworzyć `src/dbf_bridge/importer/` subpackage
- [ ] `dbf_writer.py` — tworzenie DBF (VFP) z CSV/JSON/JSONL + `.schema.jsonl`
- [ ] Użycie biblioteki `dbf` (Ethan Furman) do zapisu — `dbf.Table(path, field_specs, dbf_type="vfp", codepage=0xC8)`
- [ ] Obsługa FPT memo (tworzenie pliku memo)
- [ ] CLI: `dbf-bridge-import` (nowy punkt wejścia)
- [ ] Walidacja round-trip: DBF → X → DBF' — porównanie rekordów

### Krok 4 — Dodanie XLSX
- [ ] Nowy format `xlsx` w `exporter/writer.py` przez `openpyxl`
- [ ] Streaming: zapis arkusz-po-arkuszu
- [ ] Memo inline (jako tekst w komórce)

### Krok 5 — Testy pytest
- [ ] `tests/test_export.py` — DBF → CSV/JSON/JSONL
- [ ] `tests/test_polish_codecs.py` — Mazovia/cp852/cp1250
- [ ] `tests/test_roundtrip.py` — DBF → X → DBF
- [ ] `tests/test_cli.py` — CLI przez subprocess
- [ ] GitHub Actions CI (Python 3.10/3.11/3.12)

### Krok 6 — Publikacja na PyPI
- [ ] `python -m build`
- **`0.1.0`** — Stabilna wersja: eksport DBF → CSV/JSON/JSONL/XLSX (wymagany optional extra `xlsx`).
- **`0.2.0`** — Round‑trip import (CSV/JSON/JSONL → DBF) oraz wsparcie memo przy zapisie.
- **`0.3.0`** — Dodatkowe formaty i usprawnienia (np. lepsza walidacja, CI).

## Zależności

### Podstawowe (zawsze):
- `dbfread>=2.0.7` — odczyt DBF (streaming, natywna obsługa codepage)
  - **Uwaga**: ostatni release 2016-11-25, biblioteka stabilna ale nieaktywnie rozwijana
    (40 otwartych issues na GitHub, ostatni push 2024). Pozostaje najlepszym wyborem
    do streamingowego odczytu DBF (niskie zużycie pamięci, obsługa FPT, codepage).
    Warstwa `dbf_bridge/exporter/reader.py` izoluje tę zależność — w razie potrzeby
    można ją zastąpić alternatywą (np. `dbf` lub własny parser) bez zmiany API.

### Opcjonalne:
- `dbf>=0.99.11` — zapis DBF (round-trip import, generowanie fixture)
  - Aktywnie rozwijana przez Ethan Furman (release 2025-09-02, Python 3.10-3.13)
  - `pip install "dbfbridge[import]"` lub `pip install "dbfbridge[dev]"`
- `openpyxl>=3.1.5` — XLSX (`pip install "dbfbridge[xlsx]"`)

### Dev:
- `pytest>=8.0`, `pytest-cov>=5.0`, `build>=1.2`, `twine>=5.1`
- `pip install -e ".[dev]"` instaluje wszystkie powyższe + `dbf`

### Wersja Pythona:
- Minimum: 3.10 (używa `X | None` syntax, `argparse.BooleanOptionalAction`)
- Testowane: 3.10, 3.11, 3.12, 3.13
- `pyproject.toml`: `requires-python = ">=3.10"`, classifiers do 3.13

## Konwencje kodowe

- Python 3.10+ (używa `from __future__ import annotations` i `X | None`)
- Type hints wszędzie
- Docstringi w polski (komentarze) / angielski (docstringi modułów)
- Brak komentarzy w kodzie (zgodnie z preferencjami autora)
- `from __future__ import annotations` na początku każdego modułu
- Testy przez pytest

## Uruchamianie

```powershell
# Setup
cd D:\PycharmProjects\dbfbridge
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"  # instaluje dbfread + dbf + pytest + build + twine

# Generuj dane syntetyczne (3 tabele: klienci, zamowienia, archiwum)
python tests/fixtures/generate_sample_dbf.py
# -> tests/fixtures/input/*.dbf + *.fpt

# Eksport (domyślnie: tests/fixtures/input -> tests/fixtures/output, 3 formaty)
dbf-bridge
# lub z własnymi ścieżkami:
dbf-bridge --source "K:\dbf_source" --output "K:\dbf_output"

# Weryfikacja
dbf-bridge-verify
# lub:
dbf-bridge-verify --source "K:\dbf_source" --output "K:\dbf_output"

# Przykłady (z domyślnymi ścieżkami)
python examples/export_dbf.py
python examples/verify_dbf.py

# Testy
pytest
```

## Weryfikacja projektu (stan na ostatni commit)

- `pip install -e ".[dev]"` — instalacja pakietu w trybie edytowalnym ✓
- `python -m dbf_bridge.cli --help` — CLI działa ✓
- `dbf-bridge` — eksport 3/3 tabel OK, 0 błędów ✓
- `dbf-bridge-verify` — weryfikacja 3/3 OK, 80 rekordów round-trip ✓
- `mazovia` codec — `bytes([0x81,0x83,0x88]).decode('mazovia')` = `ąęź` ✓
- `.gitignore` — `*.dbf`, `*.fpt`, `*.cdx`, `*.csv`, `*.json`, `*.jsonl` ignorowane ✓

## Kontakt / issues

- GitHub Issues: https://github.com/PeterPirog/dbfbridge/issues
- Autor: Peter Pirog