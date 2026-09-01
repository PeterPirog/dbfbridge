# Przykłady

## A. Użycie po instalacji z PyPI (normalny przypadek)

Po instalacji:

```bash
python -m pip install dbfbridge
```

nie potrzebujesz tego repozytorium. Korzystasz z zainstalowanych poleceń
(`dbf-bridge`, `dbf-bridge-verify`, `dbf-bridge-import`, `dbf-bridge-quality`)
oraz publicznego API `from dbfbridge import ...`. Kompletny przewodnik:
[docs/pypi-usage.md](../docs/pypi-usage.md). Poniższe skrypty są **przykładami
dla repozytorium** — zainstalowany pakiet działa bez nich i bez katalogu `src`.

## B. Przykłady repozytorium / development

Skrypty w tym katalogu uruchamiają te same interfejsy, które po instalacji są dostępne
jako `dbf-bridge`, `dbf-bridge-verify`, `dbf-bridge-import` i
`dbf-bridge-quality`. Dodają lokalny katalog `src`, dlatego można ich użyć również przed
instalacją pakietu — to wygodne wyłącznie przy pracy na kodzie repozytorium, a nie
normalny sposób instalacji dla użytkownika.

| Skrypt | Odpowiednik po instalacji | Zastosowanie |
|---|---|---|
| `export_dbf.py` | `dbf-bridge` | DBF → CSV/JSON/JSONL/XLSX |
| `verify_dbf.py` | `dbf-bridge-verify` | kontrola plików eksportu |
| `export_from_file_to_dbf.py` | `dbf-bridge-import` | rekonstrukcja DBF/FPT z jednego formatu |
| `check_conversion_quality.py` | `dbf-bridge-quality` | diagnostyczny DBF → JSONL → DBF |
| `python_api.py` | publiczne API | kompletny przepływ przez `from dbfbridge import ...` |
| `inspect_table.py` | publiczne API (Phase 1A) | tylko do odczytu inspekcja nagłówka i schematu |
| `read_records.py` | publiczne API (Phase 1B) | streaming odczyt rekordów (projekcja, memo policje, raw) |

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

Inspekcja jednej tabeli (tylko nagłówek, Phase 1A):

```powershell
python examples/inspect_table.py --dbf "K:\dbf_source\klienci.dbf" --json
```

Streaming odczyt rekordów (stronicowanie, projekcja pól, memo policje, raw; Phase 1B):

```powershell
python examples/read_records.py --dbf "K:\dbf_source\klienci.dbf" `
  --offset 0 --limit 20 --memo lazy --fields ID_KL,NAZWA,NOTATKA
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

## Użycie jako biblioteka

Po `pip install dbfbridge` nie trzeba uruchamiać podprocesów CLI. Te same operacje są
dostępne jako funkcje zwracające typowane wyniki:

```python
from dbfbridge import export_dbf, reconstruct_dbf, verify_conversion

export = export_dbf(
    r"K:\dbf_source",
    r"K:\dbf_output",
    formats=("csv", "json", "jsonl", "xlsx"),
    memo="inline",
)
export.raise_for_errors()

verification = verify_conversion(
    r"K:\dbf_source",
    r"K:\dbf_output",
    formats=("csv", "json", "jsonl", "xlsx"),
)

reconstruction = reconstruct_dbf(
    r"K:\dbf_output",
    r"K:\dbf_output_reconstructed",
    input_format="jsonl",
    overwrite=True,
)
```

Plik `python_api.py` pokazuje obsługę postępu, eksport przyrostowy, weryfikację i
rekonstrukcję w jednej aplikacji. Funkcje są domyślnie bezgłośne; do GUI lub logowania
można przekazać callback `progress` otrzymujący obiekty `ProgressEvent`.
