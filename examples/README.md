# examples/

Przykłady uruchamiania konwertera `dbfbridge` na rzeczywistych danych.

## Pliki

| Plik | Opis |
|------|------|
| `export_dbf.py` | Eksport DBF → CSV/JSON/JSONL/XLSX |
| `export_from_file_to_dbf.py` | Rekonstrukcja DBF/FPT z jednego wybranego formatu i schematów |
| `check_conversion_quality.py` | Diagnostyczny round-trip DBF → JSONL → DBF |
| `verify_dbf.py` | Weryfikacja poprawności konwersji |

## Uruchamianie

### Z PyCharm

Otwórz plik `export_dbf.py` w PyCharm i kliknij „Run" — skrypt użyje domyślnych ścieżek:
- source: `K:\dbf_source`
- output: `K:\dbf_output`
- formats: `csv,json,jsonl`

### Z linii poleceń

```powershell
# Eksport (domyślne ścieżki)
python examples/export_dbf.py

# Eksport z własnymi ścieżkami
python examples/export_dbf.py --source "D:\MojeDBF" --output "D:\Wynik" --formats jsonl

# Kolejne uruchomienie: konwertuj tylko nowe lub zmienione tabele
python examples/export_dbf.py --source "D:\MojeDBF" --output "D:\Wynik" `
  --formats jsonl --incremental

# Weryfikacja
python examples/verify_dbf.py

# Rekonstrukcja drzewa DBF/FPT z JSONL
python examples/export_from_file_to_dbf.py --source "K:\dbf_output" `
  --output "K:\dbf_output_reconstructed" --formats jsonl `
  --memo inline --overwrite --progress

# Pełna kontrola jakości z raportem diagnostycznym
python examples/check_conversion_quality.py --source "K:\dbf_source" `
  --output "K:\dbf_quality" --overwrite --progress
```

Dla porównania surowej sumy DBF eksport źródłowy musi używać
`--deleted include`, aby zachować również fizyczną kolejność rekordów usuniętych.

## Uwaga o danych

Pliki DBF/FPT/CDX oraz wynikowe CSV/JSON/JSONL są ignorowane przez git
(patrz `.gitignore`), aby uniknąć wycieku wrażliwych danych. Katalog `examples/`
zawiera tylko skrypty Python — dane źródłowe i wynikowe pozostają lokalnie.
