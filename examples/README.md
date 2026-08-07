# examples/

Przykłady uruchamiania konwertera `dbfbridge` na rzeczywistych danych.

## Pliki

| Plik | Opis |
|------|------|
| `export_dbf.py` | Eksport DBF → CSV/JSON/JSONL (domyślnie `K:\dbf_source` → `K:\dbf_output`) |
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

# Weryfikacja
python examples/verify_dbf.py
```

## Uwaga o danych

Pliki DBF/FPT/CDX oraz wynikowe CSV/JSON/JSONL są ignorowane przez git
(patrz `.gitignore`), aby uniknąć wycieku wrażliwych danych. Katalog `examples/`
zawiera tylko skrypty Python — dane źródłowe i wynikowe pozostają lokalnie.