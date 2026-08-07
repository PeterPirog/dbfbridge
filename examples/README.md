# examples/

Przykłady uruchamiania konwertera `dbfbridge` na rzeczywistych danych.

## Pliki

| Plik | Opis |
|------|------|
| `export_logis.py` | Eksport DBF → CSV/JSON/JSONL (domyślnie `K:\Logis` → `K:\Logis_out`) |
| `verify_logis.py` | Weryfikacja poprawności konwersji |

## Uruchamianie

### Z PyCharm

Otwórz plik `export_logis.py` w PyCharm i kliknij „Run" — skrypt użyje domyślnych ścieżek:
- source: `K:\Logis`
- output: `K:\Logis_out`
- formats: `csv,json,jsonl`

### Z linii poleceń

```powershell
# Eksport (domyślne ścieżki Logis)
python examples/export_logis.py

# Eksport z własnymi ścieżkami
python examples/export_logis.py --source "D:\MojeDBF" --output "D:\Wynik" --formats jsonl

# Weryfikacja
python examples/verify_logis.py
```

## Uwaga o danych

Pliki DBF/FPT/CDX oraz wynikowe CSV/JSON/JSONL są ignorowane przez git
(patrz `.gitignore`), aby uniknąć wycieku wrażliwych danych. Katalog `examples/`
zawiera tylko skrypty Python — dane źródłowe i wynikowe pozostają lokalnie.