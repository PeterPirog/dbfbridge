# Przykłady

Skrypty w tym katalogu uruchamiają te same interfejsy, które po instalacji są dostępne
jako `dbf-bridge`, `dbf-bridge-verify`, `dbf-bridge-import` i
`dbf-bridge-quality`. Dodają lokalny katalog `src`, dlatego można ich użyć również przed
instalacją pakietu.

| Skrypt | Odpowiednik po instalacji | Zastosowanie |
|---|---|---|
| `export_dbf.py` | `dbf-bridge` | DBF → CSV/JSON/JSONL/XLSX |
| `verify_dbf.py` | `dbf-bridge-verify` | kontrola plików eksportu |
| `export_from_file_to_dbf.py` | `dbf-bridge-import` | rekonstrukcja DBF/FPT z jednego formatu |
| `check_conversion_quality.py` | `dbf-bridge-quality` | diagnostyczny DBF → JSONL → DBF |

## Uruchomienie w PowerShell

Eksport wymaga jawnego podania źródła i katalogu wynikowego:

```powershell
python examples/export_dbf.py --source "K:\dbf_source" `
  --output "K:\dbf_output" --overwrite --progress `
  --memo inline --formats csv,json,jsonl,xlsx
```

Przy kolejnym uruchomieniu opcja `--incremental` sprawdza
`conversion_checksums.json` i przelicza tylko nowe, zmienione, brakujące lub uszkodzone
tabele:

```powershell
python examples/export_dbf.py --source "K:\dbf_source" `
  --output "K:\dbf_output" --formats csv,json,jsonl,xlsx --incremental
```

Weryfikacja wskazanych formatów:

```powershell
python examples/verify_dbf.py --source "K:\dbf_source" `
  --output "K:\dbf_output" --formats csv,json,jsonl,xlsx
```

Rekonstrukcja drzewa z dokładnie jednego formatu:

```powershell
python examples/export_from_file_to_dbf.py --source "K:\dbf_output" `
  --output "K:\dbf_output_reconstructed" --formats jsonl `
  --memo inline --overwrite --progress
```

Pełny test jakości:

```powershell
python examples/check_conversion_quality.py --source "K:\dbf_source" `
  --output "K:\dbf_quality" --overwrite --progress
```

Każdy skrypt udostępnia pełną listę parametrów przez `--help`. Te same argumenty można
dodać w konfiguracji Run/Debug w PyCharm; skrypty nie zawierają ukrytych ścieżek do
danych użytkownika.

## Dane testowe

Po instalacji zależności deweloperskich można wygenerować bezpieczny zestaw testowy:

```powershell
python tests/fixtures/generate_sample_dbf.py
python examples/export_dbf.py --source "tests\fixtures\input" `
  --output "tests\fixtures\output" --formats csv,json,jsonl,xlsx
python examples/verify_dbf.py --source "tests\fixtures\input" `
  --output "tests\fixtures\output" --formats csv,json,jsonl,xlsx
```

Pliki DBF/FPT/CDX i wyniki konwersji są ignorowane przez Git, aby przypadkowo nie
opublikować danych produkcyjnych. Do porównania surowej sumy DBF użyj podczas eksportu
`--deleted include`, ponieważ zachowuje usunięte rekordy i ich fizyczną kolejność.
