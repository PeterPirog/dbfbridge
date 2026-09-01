# dbfbridge — docelowa architektura, roadmapa i kontrakt integracyjny

**Dokument bazowy do dalszych prac w osobnym chacie / sesji architektonicznej.**

Repozytorium źródłowe: `https://github.com/PeterPirog/dbfbridge`

Docelowy konsument strategiczny: `https://github.com/PeterPirog/mcp-vfp9sp2-toolchain`

Stan odniesienia przy sporządzeniu dokumentu: `dbfbridge 0.1.0`, commit `addbadb9281914661bf742924f45039e46a895cd` (aktualny `main` w chwili analizy). Przed implementacją należy ponownie sprawdzić aktualny `main`, otwarte PR-y i changelog; ten dokument jest kontraktem architektonicznym, nie zamrożeniem repozytorium na zawsze.

---

## 1. Rola biblioteki

`dbfbridge` ma pozostać **osobną, samodzielną biblioteką Python**, wartościową również bez projektu MCP. Nie należy kopiować jej kodu do `mcp-vfp9sp2-toolchain` ani utrzymywać dwóch równoległych implementacji parsera DBF/FPT.

Docelowo `dbfbridge` powinien być wyspecjalizowanym, loss-aware silnikiem do danych dBase/FoxPro/Visual FoxPro, ze szczególnym naciskiem na Visual FoxPro 9:

```text
DBF/FPT
  ├── inspect / schema
  ├── direct streaming READ
  ├── raw physical metadata
  ├── migration/export
  ├── reconstruction
  └── verification / round-trip diagnostics
```

Biblioteka ma służyć dwóm klasom użytkowników:

1. **użytkownik PyPI / aplikacja Python** — potrzebuje prostego, stabilnego API do odczytu, migracji i rekonstrukcji DBF/FPT;
2. **mcp-vfp9sp2-toolchain** — potrzebuje szybkiego, bezpiecznego, transport-neutral backendu `PURE_READ` działającego bez zainstalowanego VFP oraz opcjonalnych funkcji write/reconstruction na kopiach.

---

## 2. Co jest dziś wartościowe i powinno zostać zachowane

Obecna wersja ma kilka elementów, których nie należy wyrzucać podczas optymalizacji:

- streaming DBF/FPT przez `dbfread`;
- zachowanie szczegółowych metadanych DBF/VFP i FPT;
- obsługa polskich kodowań, w tym cp1250, cp852 i Mazovia;
- rozróżnienie rekordów aktywnych i deleted;
- polityki memo;
- atomic output (`.partial` + `os.replace`);
- JSONL jako bezpieczny format migracyjny;
- raw-record metadata umożliwiające bardzo dokładny round-trip;
- SHA256, statystyki, walidacja i diagnostyka różnic;
- schema-driven reconstruction DBF/FPT;
- typed public API i structured result objects;
- progress callbacks niezależne od CLI;
- incremental export;
- benchmarki konwersji JSONL;
- rozdzielenie CLI i API;
- fakt, że CDX nie jest fałszywie „rekonstruowany” z samego DBF — brak definicji tagów jest jawny.

To są aktywa projektu. Optymalizacja ma redukować koszt i poprawiać API, a nie poświęcać poprawność round-trip.

---

## 3. Główny problem architektoniczny obecnej wersji

`dbfbridge 0.1.x` jest przede wszystkim **silnikiem migracyjnym**. Publiczne API skupia się na:

```python
export_dbf()
reconstruct_dbf()
verify_conversion()
check_conversion_quality()
```

Dla przyszłego MCP to za mało. Zwykłe pytanie typu „pokaż schemat tabeli” albo „odczytaj 100 rekordów z kolumn A, B, C” nie powinno wymagać:

```text
DBF -> JSONL -> schema.json -> migration report -> ponowna walidacja
```

Potrzebny jest **direct read core**, który nie tworzy żadnych plików tymczasowych i nie uruchamia migracyjnej warstwy raportowej.

---

## 4. Docelowe warstwy pakietu

Rekomendowana struktura logiczna:

```text
dbfbridge/
  core/
    table.py
    schema.py
    records.py
    memo.py
    codecs.py
    physical.py
    backends.py
  migration/
    export.py
    jsonl.py
    csv.py
    json.py
    xlsx.py
  reconstruction/
    writer.py
    raw_layout.py
    verify.py
  diagnostics/
    checksums.py
    roundtrip.py
    diff.py
  api.py
  models.py
  cli.py
```

Nazwy plików są propozycją. Ważniejszy jest podział odpowiedzialności:

### `core`

- zero CLI;
- zero raportów migracyjnych;
- zero tworzenia outputów przy READ;
- minimalny zestaw zależności;
- direct schema/record/memo/raw access;
- używany przez MCP, migrację i anonymizer.

### `migration`

- DBF/FPT -> JSONL/CSV/JSON/XLSX;
- raporty, manifests, incremental mode;
- może mieć cięższe optional dependencies.

### `reconstruction`

- schema + records -> DBF/FPT;
- zachowanie VFP metadata;
- raw-layout repair;
- atomic output.

### `diagnostics`

- canonical checksums;
- raw hashes;
- round-trip verification;
- bounded field-level diff.

---

## 5. Docelowe publiczne API — direct READ

Minimalny target dla `0.2.x` powinien obejmować stabilne API podobne do poniższego:

```python
from dbfbridge import (
    inspect_table,
    read_schema,
    iter_records,
    read_records,
    iter_raw_records,
)
```

### `inspect_table(path)`

Zwraca bezpieczny, JSON-serializable opis bez czytania całej tabeli:

```python
TableInfo(
    path=...,
    record_count=...,
    header_length=...,
    record_length=...,
    language_driver=...,
    encoding=...,
    has_memo=True,
    has_structural_cdx=True,
    dbc_bound=False,
    fields=(...),
    warnings=(...),
)
```

### `read_schema(path)`

Zwraca pełny opis pól i fizycznych właściwości potrzebnych do analizy VFP, bez tworzenia `_schema.json`.

### `iter_records(path, ...)`

Docelowy kontrakt:

```python
iter_records(
    path,
    *,
    fields=None,
    include_deleted=False,
    memo="lazy",          # lazy | inline | null | skip
    raw=False,
    encoding="auto",
    decode_errors="strict",
)
```

Wymagania:

- streaming, O(1) / O(batch) memory;
- field projection — nie dekodować niepotrzebnych pól, jeśli backend na to pozwala;
- memo lazy — nie czytać dużych FPT, jeśli klient nie potrzebuje memo;
- deleted policy bez drugiego pełnego przebiegu;
- brak output files;
- brak print/sys.exit;
- iterator może być przerwany bez utraty zasobów.

### `read_records(path, offset=0, limit=100, fields=None, ...)`

Wygodna bounded-memory funkcja do MCP/UI. `limit` powinien być jawny; API nie powinno przypadkiem materializować milionów rekordów.

### `iter_raw_records(path)`

Dla diagnostyki/forensics/round-trip. Nie powinien być domyślnym path zwykłego odczytu.

---

## 6. Profile kosztu zamiast jednej „najbezpieczniejszej” ścieżki

Biblioteka powinna jawnie rozróżniać trzy rodzaje pracy:

### `READ_FAST`

Cel: analiza/MCP.

- brak output files;
- brak raw Base64;
- brak ponownego parsowania wyniku;
- memo lazy/skip;
- projekcja pól;
- bounded `limit`;
- minimalne zależności.

### `MIGRATION_SAFE`

Cel: eksport/migracja.

- schema artifact;
- checksums;
- atomic output;
- walidacja syntaktyczna i liczników;
- JSONL jako preferowany loss-aware format;
- opcjonalny incremental manifest.

### `FORENSIC_ROUNDTRIP`

Cel: maksymalna możliwość rekonstrukcji fizycznego układu.

- raw physical record image;
- FPT metadata/pointers;
- deleted physical order;
- raw SHA256;
- bounded diagnostics różnic.

Nie należy płacić kosztu `FORENSIC_ROUNDTRIP` podczas zwykłego READ.

---

## 7. Raw-record metadata — zmienić z domyślnego kosztu w opcjonalną cechę

Obecna ścieżka migracyjna zapisuje Base64 surowego rekordu DBF w JSONL. Jest to bardzo użyteczne dla byte-accurate reconstruction, ale kosztowne:

- Base64 zwiększa rozmiar reprezentacji;
- wartości logiczne występują równolegle z raw record image;
- rośnie disk I/O;
- rośnie parse/serialization CPU;
- rosną temporary artifacts w anonymizerze.

Docelowo API migracji powinno mieć jawny poziom zachowania raw danych, np.:

```python
raw_mode="none" | "metadata" | "full-record"
```

Domyślny tryb może pozostać zgodny wstecznie, ale MCP/direct read ma używać `none`.

---

## 8. Walidacja: zachować, ale nie wykonywać zawsze

Obecny eksport potrafi zapisać plik, następnie policzyć jego SHA256 i ponownie go sparsować w celu potwierdzenia liczników/statystyk. Jest to właściwe dla migracji, lecz nie dla odczytu online.

Docelowo:

```text
READ_FAST          -> no output validation
MIGRATION_SAFE     -> standard validation
FORENSIC_ROUNDTRIP -> full validation + raw/canonical comparison
```

Walidacja musi być jawna i mierzalna; raport benchmarku powinien osobno pokazywać koszt `validate=False` vs `validate=True`.

---

## 9. `dbfread`: nie usuwać bez danych, ale odizolować backend

W obecnej wersji `dbfread` jest podstawowym readerem i `dbfbridge` korzysta także z elementów, które nie są idealną długoterminową publiczną granicą (np. prywatne metody/klasy lub głębsze podmoduły).

Nie należy przepisywać parsera DBF/FPT „na wyczucie”. Najpierw należy wprowadzić backend abstraction:

```python
class DBFReadBackend(Protocol):
    def inspect(...): ...
    def iter_records(...): ...
    def iter_raw_records(...): ...
```

Backend `dbfread` pozostaje reference/current implementation.

Dopiero benchmark i test matrix mogą uzasadnić drugi backend, np. `native_vfp_reader`, który czyta nagłówki, field descriptors, record bytes i FPT bezpośrednio.

Warunek zastąpienia `dbfread`:

- realna przewaga wydajności lub funkcjonalności;
- pełne pokrycie typów VFP;
- brak regresji encoding/memo/null/deleted;
- reference equivalence tests.

---

## 10. Writer/reconstruction — ostrożniejsza polityka zmian

Obecny writer i raw-layout repair obsługują trudne przypadki VFP: typy pól, NULL, memo, binary memo, metadata, deleted order i physical reconstruction.

Nie należy przepisywać tej części tylko po to, aby „usunąć dependency `dbf`”.

Najpierw trzeba zmierzyć:

- records/s;
- FPT MB/s;
- peak RSS;
- temp bytes;
- udział writer CPU w całym pipeline;
- koszt raw-layout restore;
- koszt canonical verify.

Własny writer ma sens dopiero wtedy, gdy writer jest udowodnionym bottleneckiem lub zewnętrzny pakiet blokuje poprawność ważnego przypadku VFP.

---

## 11. Zależności i PyPI

`dbfbridge` powinien być normalnym pakietem PyPI bez dependency typu `git+https://...`.

Rekomendowany podział extras:

```text
pip install dbfbridge
    -> direct READ + podstawowe JSONL/migration core

pip install dbfbridge[write]
    -> reconstruction dependencies

pip install dbfbridge[xlsx]
    -> openpyxl/xlsxwriter

pip install dbfbridge[fast]
    -> orjson/polars, jeśli rzeczywiście przyspieszają wybrane ścieżki

pip install dbfbridge[all]
    -> pełny zestaw
```

Dokładny zestaw zależności trzeba ustalić po audycie importów i benchmarkach. Zasada: brak opcjonalnej dependency degraduje tylko konkretną operację, nie cały package import ani `PURE_READ`.

### Nazwa pakietu

**Rekomendacja: zachować `dbfbridge`.**

Zalety:

- obecna nazwa jest krótka;
- publiczne API już używa `import dbfbridge`;
- zmiana nazwy generuje koszt migracyjny bez oczywistego zysku technicznego.

Jeżeli konieczna jest silniejsza identyfikacja z VFP, alternatywy do sprawdzenia pod kątem dostępności na PyPI:

- `vfpdbf`
- `vfp-dbf-toolkit`
- `foxpro-dbf`

Nie należy zmieniać nazwy przed sprawdzeniem dostępności i przygotowaniem okresu kompatybilności.

---

## 12. Wymagania jakości pakietu PyPI

Przed oznaczeniem biblioteki jako stabilnej:

- semver;
- `pyproject.toml` z pełnymi metadata;
- SPDX license expression i LICENSE;
- Trusted Publishing do PyPI;
- wheels/sdist;
- Python 3.10–3.14 test matrix;
- Windows jako obowiązkowa platforma CI;
- Linux dla pure-Python core, jeśli działa;
- changelog;
- migration guide;
- typed API / `py.typed` jeśli typowanie jest publicznym kontraktem;
- brak import-time side effects;
- brak runtime network;
- przykłady API, nie tylko CLI;
- jasne oznaczenie gwarancji i ograniczeń VFP/CDX.

Status `1.0.0` ma oznaczać stabilne API i dobrze określone gwarancje, nie tylko „dużo funkcji”.

---

## 13. Benchmark suite — obowiązkowy przed optymalizacją

Przed zmianą krytycznych ścieżek trzeba stworzyć baseline i zachować wyniki w repo.

Minimalne scenariusze:

```text
A. inspect/schema: 1 / 100 / 1000 tabel
B. DBF read: 190k rekordów
C. DBF read: 1M rekordów
D. DBF+FPT memo-heavy
E. deleted records include/skip
F. cp1250
G. cp852
H. Mazovia
I. DBF -> JSONL
J. JSONL -> DBF/FPT reconstruction
K. validate OFF vs ON
L. raw_mode none vs full-record
M. selected fields vs all fields
N. memo skip/lazy/inline
O. local SSD vs LAN-like path, jeśli dostępne
```

Mierzyć co najmniej:

```text
wall time
CPU time
records/s
source MB/s
peak RSS
temporary bytes written
output bytes
read amplification
write amplification
```

Wyniki mają być oznaczone jako measured, z wersją Pythona, OS, CPU, storage i rozmiarem fixture.

Nie wolno deklarować wzrostu wydajności bez BEFORE/AFTER.

---

## 14. Performance acceptance policy

Każda optymalizacja musi spełnić wszystkie warunki:

1. ten sam wymagany wynik logiczny;
2. brak regresji w testach VFP types/codepages/memo/deleted/null;
3. brak pogorszenia bezpieczeństwa atomic writes;
4. brak utraty round-trip diagnostics;
5. zmierzona poprawa w scenariuszu, dla którego zmiana została wprowadzona;
6. brak istotnej regresji w innych głównych scenariuszach albo jawne uzasadnienie trade-off.

Najważniejsze optymalizacje oczekiwane:

- direct read API;
- ograniczenie niepotrzebnych wielokrotnych otwarć/przebiegów;
- memo lazy;
- field projection;
- raw record jako opcja;
- brak temporary artifacts dla READ;
- bounded memory;
- możliwość anulowania długiego streamingu.

---

## 15. Kontrakt dla `mcp-vfp9sp2-toolchain`

`mcp-vfp9sp2-toolchain` nie powinien importować prywatnych modułów `dbfbridge`.

Dozwolona granica:

```python
import dbfbridge
```

lub stabilne publiczne submodule oznaczone jako API.

Toolchain powinien używać `dbfbridge` do:

```text
PURE_READ:
  inspect table
  schema
  bounded records
  stream records
  FPT/memo metadata
  physical DBF metadata

PURE_WRITE_COPY:
  export
  reconstruct on output/workspace only
  verify copied artifacts

PRIVACY:
  backend for DBF_Anonymizer
```

Core API nie może wymagać:

- VFP9;
- FoxBin2Prg;
- COM;
- sieci;
- OpenCode/MCP transportu.

MCP adapter ma widzieć jedynie stabilne, JSON-serializable result objects.

---

## 16. MCP-specific read semantics

Dla przyszłego MCP biblioteka musi dobrze obsługiwać bounded requests:

```text
limit
fields projection
offset lub cursor/token
includeDeleted
memo policy
encoding policy
```

Długie memo nie powinny być automatycznie zwracane w tysiącach rekordów. Preferowane są:

- `memo="lazy"`;
- metadata z długością/hash;
- osobny odczyt konkretnego memo, jeśli potrzebny.

To chroni zarówno wydajność, jak i limit odpowiedzi MCP/LLM.

---

## 17. Error model

Biblioteka powinna mieć typed exceptions i/lub structured errors rozróżniające co najmniej:

```text
DBF_FORMAT_UNSUPPORTED
DBF_HEADER_INVALID
DBF_TRUNCATED
FPT_REQUIRED_MISSING
FPT_INVALID
ENCODING_UNKNOWN
TEXT_DECODE_ERROR
FIELD_TYPE_UNSUPPORTED
OPTIONAL_DEPENDENCY_MISSING
OUTPUT_EXISTS
RECONSTRUCTION_FAILED
ROUNDTRIP_MISMATCH
```

Nie należy wrzucać wszystkich problemów do ogólnego `RuntimeError`.

Toolchain mapuje te kody na własny `OperationResult` bez parsowania tekstu błędu.

---

## 18. Bezpieczeństwo

Direct READ musi być rzeczywiście read-only:

- source SHA256 przed/po w testach;
- brak `touch`, `mkdir`, `.partial` obok source;
- brak implicit index rebuild;
- brak PACK/REINDEX/ZAP;
- brak tworzenia lock files przy source;
- output zawsze jawnie oddzielony od source.

Dla reconstruction/write:

- output only;
- atomic publish;
- brak source overwrite bez jawnego opt-in i najlepiej brak takiej opcji w high-level API używanym przez MCP.

---

## 19. CDX i IDX

`dbfbridge` nie powinien udawać pełnego CDX engine, dopóki nie ma wiarygodnego parsera definicji tagów.

Core może dostarczać:

```text
has_structural_cdx
companion_cdx_exists
raw header flags
heuristic/basic metadata if implemented
```

Authoritative tag expression/order/candidate/primary metadata w systemie VFP9 powinno pozostać odpowiedzialnością runtime VFP adaptera w `mcp-vfp9sp2-toolchain` lub przyszłego osobnego, udowodnionego CDX parsera.

---

## 20. Proposed release roadmap

### `0.2.0` — Direct Read Core

- `inspect_table`;
- `read_schema`;
- `iter_records`;
- `read_records`;
- memo lazy;
- field projection;
- raw-mode split;
- stable result models;
- benchmark baseline;
- no regressions in existing migration/reconstruction.

### `0.3.0` — Performance + backend abstraction

- backend interface for current `dbfread`;
- optimized physical iterator if benchmark justifies it;
- reduced multi-pass metadata reading;
- cancellation/progress for direct readers;
- performance regression CI.

### `0.4.x` — Reconstruction hardening

- only if benchmarks/bugs justify changes;
- stronger VFP type fixtures;
- FPT edge cases;
- improved writer throughput without losing round-trip guarantees.

### `1.0.0`

- stable direct-read API;
- stable migration/reconstruction API;
- documented compatibility matrix;
- benchmark suite;
- robust PyPI packaging;
- no known correctness gaps in supported VFP DBF/FPT cases.

---

## 21. Definition of Done dla współpracy z MCP

`dbfbridge` jest gotowy jako backend przyszłego MCP, jeśli:

- `import dbfbridge` nie ma efektów ubocznych;
- direct schema/read działa bez VFP9;
- direct read nie tworzy output files;
- field projection działa;
- memo może być lazy/skip;
- bounded `read_records(limit=N)` nie materializuje całej tabeli;
- source pozostaje byte-identical;
- API zwraca structured results/exceptions;
- brak runtime network;
- benchmarki są zapisane i powtarzalne;
- anonimizator może użyć direct record stream bez obowiązkowego JSONL;
- `mcp-vfp9sp2-toolchain` nie musi znać wewnętrznej struktury package.

---

## 22. Czego NIE robić

- nie kopiować kodu `dbfbridge` do `mcp-vfp9sp2-toolchain`;
- nie optymalizować bez benchmarku;
- nie usuwać JSONL/round-trip tylko dlatego, że direct read jest szybszy;
- nie przepisywać writera od zera bez udowodnionego bottlenecku;
- nie uzależniać importu core od Polars/XLSX;
- nie mieszać MCP/transportu z biblioteką danych;
- nie wymagać Internetu w runtime;
- nie dodawać VFP COM do core biblioteki.

---

## 23. Startowy prompt do nowego chatu

Można rozpocząć osobny chat od wklejenia tego dokumentu i polecenia:

> Jesteś architektem i reviewerem repozytorium `PeterPirog/dbfbridge`. Ten dokument definiuje stan docelowy. Najpierw sprawdź aktualny `main`, testy, benchmarki i różnice względem opisanego baseline. Nie przepisuj kodu od razu. Przygotuj plan Phase 0: benchmark baseline oraz Phase 1: Direct Read Core. Każda optymalizacja musi mieć BEFORE/AFTER, zachować poprawność VFP DBF/FPT i być zapisana w logicznych commitach Git. `dbfbridge` ma pozostać samodzielną biblioteką PyPI i stabilnym backendem dla `mcp-vfp9sp2-toolchain`.

---

## 24. Najważniejsza decyzja architektoniczna

**`dbfbridge` nie jest komponentem do skopiowania — jest komponentem do dojrzenia.**

Powinien ewoluować z „narzędzia migracyjnego DBF” w:

> **wysokiej jakości bibliotekę DBF/FPT dla Python, posiadającą szybki direct-read core oraz osobne, bardziej kosztowne warstwy migracji, rekonstrukcji i forensics.**

Tak rozwinięty `dbfbridge` będzie jednocześnie wartościowym projektem PyPI i właściwym data backendem przyszłego MCP dla Visual FoxPro 9 SP2.
