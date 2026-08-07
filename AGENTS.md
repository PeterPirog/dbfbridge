# AGENTS.md — dbfbridge project context

> Ten plik zawiera pełny kontekst prac dla nowej sesji w PyCharm.
> Przeczytaj go w całości przed kontynuowaniem prac.

## Projekt

**dbfbridge** — dwukierunkowy konwerter plików DBF (Visual FoxPro) ↔ CSV/JSON/JSONL/XLSX z automatycznym fallbackiem polskich stron kodowych (cp1250/cp852/Mazovia).

- **Repozytorium**: https://github.com/PeterPirog/dbfbridge
- **Autor**: Peter Pirog <pirog.peter@gmail.com>
- **Licencja**: MIT
- **Status**: 0.1.0.dev0 (alpha — tylko eksport DBF → X, brak round-trip i XLSX)
- **Cel publikacji**: PyPI

## Historia powstania

Projekt wywodzi się z `Logis-converters` (D:\PycharmProjects\Logis-converters) — zestawu skryptów do migracji danych systemu Logis (Visual FoxPro 9.0 SP2, pliki DBF/FPT/CDX z polskimi znakami) do nowoczesnych formatów, a docelowo do bazy Neo4j.

### Co zostało zrobione w Logis-converters:

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

5. **Testy na danych rzeczywistych**: 93 pliki DBF z `K:\Logis` — wszystkie 93 OK po dodaniu fallback Mazovia (wcześniej 6 failed na `UnicodeDecodeError` z cp1250)

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
├── .gitignore
├── src/
│   └── dbf_bridge/
│       ├── __init__.py          # wersja, public API
│       ├── cli.py               # dbf-bridge (eksport) — z 05_dbf_tree_to_csv.py
│       ├── verifier.py          # dbf-bridge-verify — z 06_verify_conversion.py
│       └── exporter/           # z Logis-converters/exporter/
│           ├── __init__.py
│           ├── cli.py           # (do usunięcia — dubluje dbf_bridge.cli)
│           ├── config.py        # make_config()
│           ├── discovery.py     # discover_tables()
│           ├── models.py        # ExportConfig, FieldMetadata, TableMetadata, TableResult
│           ├── polish_codecs.py # Mazovia/PIAST codec registration
│           ├── reader.py        # LosslessFieldParser z fallback kodowania
│           ├── reporting.py     # write_reports()
│           ├── serialization.py # serialize_record() z memo_policy
│           ├── validation.py    # validate_output()
│           └── writer.py        # export_table() streaming atomowy
├── tests/
│   └── fixtures/
│       └── generate_sample_dbf.py  # generator syntetycznych DBF
└── docs/                        # (do utworzenia)
```

## Co trzeba zrobić (kolejne kroki)

### Krok 1 — Oczyszczenie i dostosowanie importów (najpilniejsze)
- [ ] Usunąć `src/dbf_bridge/exporter/cli.py` (dubluje `dbf_bridge.cli`)
- [ ] Sprawdzić i poprawić importy w `exporter/` — wszystkie `from .` muszą działać w nowej strukturze
- [ ] Usunąć `from exporter.` → `from dbf_bridge.exporter.` w `cli.py` i `verifier.py` (już zrobione)
- [ ] Utworzyć `.venv` w `dbfbridge/` i zainstalować `pip install -e .[dev]`
- [ ] Przetestować: `python -m dbf_bridge.cli --help` i `dbf-bridge --help`

### Krok 2 — Testy na danych syntetycznych
- [ ] Uruchomić `tests/fixtures/generate_sample_dbf.py` (wymaga `pip install dbf`)
- [ ] Uruchomić `dbf-bridge --source tests/fixtures/synthetic_data/input --output tests/fixtures/synthetic_data/output`
- [ ] Uruchomić `dbf-bridge-verify` na wyniku
- [ ] Naprawić ewentualne błędy importów

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
- [ ] `twine upload --repository testpypi dist/*` (walidacja)
- [ ] `twine upload dist/*` (produkcyjne PyPI)
- [ ] Wersja `0.1.0` (alpha — tylko eksport), `0.2.0` (round-trip), `0.3.0` (XLSX)

## Zależności

### Podstawowe (zawsze):
- `dbfread>=2.0.7` — odczyt DBF (streaming, natywna obsługa codepage)

### Opcjonalne:
- `openpyxl>=3.1.5` — XLSX (`pip install dbfbridge[xlsx]`)
- `dbf>=0.99.11` — zapis DBF (do round-trip, dodana gdy implementujemy importer)

### Dev:
- `pytest>=8.0`, `pytest-cov>=5.0`, `build>=1.2`, `twine>=5.1`

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
pip install -e ".[dev]"

# Generuj dane syntetyczne
python tests/fixtures/generate_sample_dbf.py

# Eksport
dbf-bridge --source tests/fixtures/synthetic_data/input --output tests/fixtures/synthetic_data/output

# Weryfikacja
dbf-bridge-verify --source tests/fixtures/synthetic_data/input --output tests/fixtures/synthetic_data/output

# Testy
pytest
```

## Kontakt / issues

- GitHub Issues: https://github.com/PeterPirog/dbfbridge/issues
- Autor: Peter Pirog