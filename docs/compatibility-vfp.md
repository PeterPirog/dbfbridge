# VFP/FPT compatibility matrix (evidence-based)

This document records exactly what the current test suite **proves** about
Visual FoxPro physical field types and FPT memo behaviour.  It describes
only evidence, not intentions: a type is `SUPPORTED` only when a named test
exercises it end to end against a physically valid fixture.  Where no honest
fixture exists, the status is `NOT_YET_VERIFIED` — never upgraded on the
basis of source code alone or type-byte similarity.

Evidence names point at the suite (`tests/…`).  Deterministic fixture
builders live in `tests/vfp_fixture_factory.py`; the status legend (each
status means exactly one thing):

| status | meaning |
|---|---|
| `SUPPORTED` | full evidence: schema + Direct Read + migration + reconstruction (canonical) on a physically valid fixture |
| `SUPPORTED_WITH_LIMITATION` | working with a documented, tested limitation |
| `RAW_ONLY` | physical bytes preserved/forensically readable; decoded access is a typed error |
| `UNSUPPORTED` | typed refusal by design (schema reason + typed errors + `UNSUPPORTED` export status) |
| `SYSTEM_INTERNAL` | VFP system column; handled physically, not as normal user data |
| `PARSER_COMPATIBILITY_ONLY` | same-width field-code parsing inside an already supported table frame only — NOT evidence of the real dialect's table/memo/header semantics |
| `NOT_YET_VERIFIED` | no honest fixture yet — behaviour unproven |

Legend for read levels: **schema** = `inspect_table`/`read_schema`, **read** =
Direct Read (`iter_records`/`read_records`), **migration** = `export_dbf`
JSONL, **recon** = `reconstruct_dbf`, **raw RT** = byte-identical DBF round
trip (canonical checksums are always checked first).

## Field types

| physical type | VFP meaning | schema | read | migration | recon | raw RT | nullable | memo/FPT | status | evidence | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `C` | Character | visible | decoded text | text | byte-identical | yes | yes (NULL bit) | no | `SUPPORTED` | `tests/test_public_api.py::test_public_api_runs_the_complete_workflow_silently` (3-table tree incl. `C` round trip) + `tests/test_direct_read_records.py::test_exporter_parity_klienci_memo` (Direct Read) + `tests/test_vfp_varchar.py::test_vfp_nullable_ordinary_fields_null_bit_reads_as_none` | NULLable columns carry the hidden `_NullFlags` column (see type `0`); genuinely NULL values read as `None` |
| `C` + flags 0x04 | NOCPTRANS/binary Character | visible, classified `nocptrans=True`, `supported=False` + reason | typed `FIELD_TYPE_UNSUPPORTED` | per-table `UNSUPPORTED` | n/a | yes (exact bytes) | n/a | no | `RAW_ONLY` | `tests/test_vfp_type_matrix.py::test_binary_character_nocptrans_is_raw_readable_but_not_decoded` | dedicated binary C/V parser is an explicit non-goal; bytes never lost |
| `V` | Varchar (0x32 dialect) | visible, `dbversion_name` "Varchar/Varbinary enabled" | exact value incl. significant trailing spaces; varlength + NULL bits honoured; text decoded by the configured parser policy | exact logical value in JSONL (incl. NULL distinction) | canonical match in every raw mode (schema-driven Varchar logical-layout repair; no raw record image required); raw byte identity is a tested gap (the writer cannot yet guarantee the byte-exact variable-length source layout without raw images) | no (tested gap, `raw_dbf_match` reported separately) | yes (NULL bit) | no | `SUPPORTED_WITH_LIMITATION` | `tests/test_vfp_varchar.py` (whole module; authentic 0x32 fixtures with real `_NullFlags`); schema-driven reconstruction per raw mode: `tests/test_raw_mode.py::test_canonical_reconstruction_nullable_varchar`, `test_varchar_value_matrix_reconstructs_logically_identical`, `test_varchar_polish_codepages_reconstruct` | fixture = authentic low-level build (header + descriptors + bitmap + length-byte payloads), **not** a `C` patch. Text-policy evidence: `test_vfp_varchar_keeps_significant_trailing_spaces` (parser policy, not a blanket rstrip), cp1250 `test_vfp_varchar_cp1250_polish_text_reads_exact_unicode`, cp852 `test_vfp_varchar_cp852_encoding_reads_exact_unicode`, Mazovia `test_vfp_varchar_mazovia_encoding_reads_exact_unicode`; the migration fallback path retains the original raw bytes under `__dbfbridge_raw_text_fields__` (`test_vfp_varchar_migration_fallback_keeps_raw_bytes`). Reconstruction evidence: `test_vfp_varchar_null_record_reconstructs_canonically` (NULL records included), `test_vfp_varchar_mixed_null_bitmap_reconstructs_canonically` |
| `V` + flags 0x04 | binary Varchar | visible, `supported=False` | typed `FIELD_TYPE_UNSUPPORTED` (by classifier) | — | — | — | — | — | `NOT_YET_VERIFIED` (unsupported by design) | no authentic physical fixture exists; similarity to binary `C` is not evidence | dedicated binary Varchar parser is an explicit non-goal |
| `N` | Numeric | visible | number | number | byte-identical | yes | yes (NULL bit) | no | `SUPPORTED` | `tests/test_importer.py::test_jsonl_roundtrip_preserves_narrow_negative_numeric_and_complete_header`, `test_vfp_nullable_ordinary_fields_null_bit_reads_as_none` | |
| `F` | Float | visible | number | number | byte-identical | yes | yes (NULL bit) | no | `SUPPORTED` | Direct Read + round trip: `tests/test_vfp_type_matrix.py::test_vfp_integer_float_datetime_roundtrip` | |
| `I` | Integer | visible | int | number | byte-identical | yes | yes (NULL bit) | no | `SUPPORTED` | `test_vfp_integer_float_datetime_roundtrip` | 4-byte LE |
| `I` + mask 0x0C | VFP autoincrement Integer | `is_autoincrement=True`, next value/step exposed, `nocptrans=False` | int | flags + descriptor preserved | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_vfp_autoincrement_descriptor_survives_roundtrip` | mask 0x0C contains bit 0x04 — must never classify as NOCPTRANS/binary |
| `+` | dBASE Level 7 autoincrement marker | visible, never autoincrement evidence inside VFP | int with valid 4-byte layout | number | untested | yes | n/a | no | `PARSER_COMPATIBILITY_ONLY` | schema: existing `test_plus_type_is_never_vfp_autoincrement_evidence`; read: `test_vfp_plus_type_reads_as_integer_with_valid_layout` | proven only as same-width field-code parsing inside a VFP-framed table; a real dBASE Level 7 table dialect is NOT supported (`SUPPORTED_VERSIONS` does not include it) |
| `Y` | Currency | visible | `Decimal` with 4-digit scale | exact decimal string | byte-identical | yes | yes (NULL bit) | no | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_vfp_currency_direct_read_and_roundtrip_preserves_decimal_value`; NULL: `test_vfp_nullable_ordinary_fields_null_bit_reads_as_none` | 8-byte scaled int ×10⁻⁴ |
| `B` (VFP, length 8) | Double (inline) | visible, `supported=True`, `is_memo=False` | float | number | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_vfp_double_table_without_fpt_exports_and_roundtrips`; strictness: `test_vfp_double_with_real_memo_still_requires_fpt` | a Double-only table needs **no FPT**; a table with a real memo still requires it (typed failure proven) |
| `B` (non-VFP) | binary memo pointer | memo classification | — | — | — | — | — | pointer | `NOT_YET_VERIFIED` | — | dBase III/IV `B` tables are outside this matrix's scope |
| `O` | Double (dBASE Level 7 alias) | visible, supported | float (8-byte IEEE) | number | byte-identical | yes | yes | no | `PARSER_COMPATIBILITY_ONLY` | `tests/test_vfp_type_matrix.py::test_vfp_double_alias_o_reads_like_a_double` | fixture = B patched to O (identical layout); `O` is not a native VFP type; no dBASE 7 dialect support |
| `@` | dBASE Level 7 Timestamp | visible, `dbf_type_name` "Timestamp" | `datetime` (same 8-byte layout as `T`) | ISO datetime | untested | yes | yes | no | `PARSER_COMPATIBILITY_ONLY` | `tests/test_vfp_type_matrix.py::test_vfp_timestamp_alias_decodes_like_datetime` | fixture = T patched to `@` (identical layout); no dBASE 7 dialect support |
| `T` | DateTime | visible | `datetime` | ISO datetime | byte-identical | yes | yes (NULL bit) | no | `SUPPORTED` | `test_vfp_integer_float_datetime_roundtrip` | |
| `D` | Date | visible | `date` | ISO date | byte-identical | yes | yes | no | `SUPPORTED` | Direct Read: `tests/test_direct_read_records.py::test_exporter_parity_zamowienia_types` (`DATA_ZAM` parity incl. ISO form); canonical round trip: `tests/test_public_api.py::test_public_api_runs_the_complete_workflow_silently` | |
| `L` | Logical | visible | bool / None | boolean-or-null | byte-identical | yes | yes | no | `SUPPORTED` | Direct Read (incl. NULL `VIP`): `tests/test_direct_read_records.py::test_exporter_parity_klienci_memo`; canonical round trip: `tests/test_public_api.py::test_public_api_runs_the_complete_workflow_silently` | |
| `M` | Memo (text) | visible, memo | lazy/inline/null/skip policies; NULL bit → `None` without FPT | text (+base64 for binary blocks) | byte-identical DBF; FPT content canonical | yes | yes (NULL bit) | yes | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_memo_block_type_decides_text_vs_binary_decoding`; lazy: `tests/test_direct_read_records.py::test_lazy_memo_values_never_open_fpt_during_iteration`; canonical FPT round trip: `tests/test_importer.py::test_jsonl_roundtrip_relocates_memo_to_original_pointer` | per-block type byte decides text vs binary decoding |
| `G` | General/OLE memo | visible, binary memo | inline payload bytes | base64 | DBF byte-identical; FPT content restored, raw FPT layout may differ (report `WARNING`) | partial (DBF yes) | yes | yes | `SUPPORTED_WITH_LIMITATION` | `tests/test_vfp_type_matrix.py::test_general_and_picture_memo_roundtrip_keeps_canonical_identity` | rebuilt FPT keeps payload + pointer but not byte-identical block layout — proven by `raw_fpt_match: false` + explicit warning |
| `P` | Picture memo | visible, binary memo | inline payload bytes | base64 | as `G` | partial (DBF yes) | yes | yes | `SUPPORTED_WITH_LIMITATION` | `test_general_and_picture_memo_roundtrip_keeps_canonical_identity[picture]` | same FPT-layout limitation |
| `Q` | Varbinary (inline) | visible, `supported=False` + reason | typed `FIELD_TYPE_UNSUPPORTED` | per-table `UNSUPPORTED` | n/a | yes | yes (varlength bit affects layout only) | no | `UNSUPPORTED` | authentic 0x32 fixtures: `tests/test_vfp_type_matrix.py::test_unsupported_varbinary_is_raw_readable_but_not_decoded`, `test_unsupported_table_export_reports_typed_unsupported_status`; projection exception: existing `test_unselected_unsupported_field_does_not_block_reading` | no Varbinary decoding implemented; the forensic raw stream keeps the exact physical bytes |
| `W` | Blob (FPT pointer) | visible, `supported=False` + reason | typed `FIELD_TYPE_UNSUPPORTED` when selected; `memo="skip"` removes it | per-table `UNSUPPORTED` | n/a | yes (pointer bytes) | n/a | yes (pointer) | `UNSUPPORTED` | `tests/test_vfp_type_matrix.py::test_unsupported_blob_is_raw_readable_but_not_decoded` (synthetic M→W pointer patch); skip contract: existing `test_skip_removes_unsupported_memo_field` | authentic W-in-FPT fixture NOT_YET_VERIFIED — the pointer-boundary evidence is parser-level |
| `0` | hidden `_NullFlags` system column | visible, `system=True`, binary | physically exposed raw bitmap bytes in `values` (classified `system=True`; not a normal application data field) | base64 | byte-identical | yes | n/a (it *is* the flag bitmap) | no | `SYSTEM_INTERNAL` | `tests/test_vfp_type_matrix.py::test_nullflags_system_column_is_raw_readable_and_roundtrips` | appears automatically for NULLable columns; its NULL/varlength bits drive the shared read semantics (`core/nullflags.py`) |
| `X` | (any unknown byte) | header-invalid / unknown type handling | typed errors | — | — | — | — | — | `NOT_YET_VERIFIED` | no fixture | no honest synthetic layout — deliberately left unproven rather than faked |

## FPT edge cases

| case | behaviour | evidence |
|---|---|---|
| missing FPT (inline) | typed `FPT_REQUIRED_MISSING` | existing `tests/test_direct_read_records.py::test_missing_fpt_inline_is_fpt_required_missing` |
| FPT vanishes before lazy load | typed `FPT_REQUIRED_MISSING` at `load()` | existing `test_lazy_load_missing_companion_is_typed` |
| B Double + real memo, FPT missing | typed `FPT_REQUIRED_MISSING` (read) and structured `FAILED` table (migration) — the Double never suppresses a real memo requirement | `tests/test_vfp_type_matrix.py::test_vfp_double_with_real_memo_still_requires_fpt` |
| block size 0 | typed `FPT_INVALID` | existing `test_broken_fpt_block_size_zero_is_fpt_invalid` |
| companion truncated (no block area) | typed `FPT_INVALID` | existing `test_truncated_memo_payload_is_fpt_invalid` |
| memo pointer == 0 | no value in every policy (`None`/absent) | `tests/test_fpt_edge_cases.py::test_fpt_pointer_zero_reads_as_empty_across_policies` |
| pointer beyond EOF | typed `FPT_INVALID` (inline; lazy at `load()` only) | `test_fpt_pointer_beyond_eof_is_typed_invalid`, `test_lazy_corrupt_memo_fails_only_on_load` |
| declared payload length beyond EOF | typed `FPT_INVALID` (inline; lazy at `load()`) | `test_fpt_payload_length_beyond_eof_is_typed_invalid` |
| block header cut by EOF | lazy-clean iteration; typed `FPT_INVALID` at `load()` | `test_fpt_truncated_block_header_is_typed_invalid_on_lazy_load` |
| empty payload block (length 0) | deterministic empty string (distinct from pointer-0 `None`) | `test_fpt_empty_payload_block_reads_as_empty_string` |
| non-default block size (1024) | payload read correctly; schema reports block size | `test_fpt_non_default_block_size_reads_payload_correctly` |
| multiple memo fields per record | independent inline/lazy reads | `test_multiple_memo_fields_are_read_independently` |
| deleted record containing a memo | payload readable with `include_deleted=True`; raw stream keeps the pointer without FPT | `test_deleted_record_memo_is_readable_when_included` |
| binary + text blocks in one logical field | per-block type byte decides decoding | `test_memo_block_type_decides_text_vs_binary_decoding` |
| separate blocks per record / relocated pointers | covered by the existing suite | existing `test_jsonl_roundtrip_relocates_memo_to_original_pointer` |
| case-insensitive companion discovery | covered by the existing suite | existing schema companion tests (`test_direct_read_schema.py`) |
| reconstruction failure atomicity | failed run publishes nothing; no `.partial`; sources byte-identical | `tests/test_fpt_edge_cases.py::test_reconstruction_failure_leaves_no_partial_outputs` |

## NULL semantics (VFP `_NullFlags` bitmap)

Verified against the reference writer's real NULL output (bit set = NULL,
bits allocated in field order; a `V`/`Q` column takes a varlength bit first,
then its NULL bit when nullable):

- genuinely NULL values of nullable `C`/`I`/`Y` fields resolve to `None`
  (previously blank storage decoded to `''`/`0`/`Decimal('0')`) —
  `tests/test_vfp_varchar.py::test_vfp_nullable_ordinary_fields_null_bit_reads_as_none`;
- NULL memos never touch the FPT (handled inside the single physical loop);
- bitmap bytes beyond the allocated bit count are template residue and are
  never interpreted;
- a table that needs a bitmap must carry **exactly one** type-`0` column and
  that column must carry the VFP system flag (0x01): zero candidates, more
  than one candidate, or a non-system bitmap column all fail typed
  (`DBF_HEADER_INVALID`) — `tests/test_vfp_varchar.py::test_nullflags_duplicate_bitmap_column_is_typed_invalid`,
  `test_nullflags_non_system_bitmap_column_is_typed_invalid`;
- a bitmap column shorter than the allocated bits fails typed as well —
  `test_nullflags_too_short_is_typed_header_invalid`;
- the layout builder materializes its input once (one-shot generators
  accepted) — `test_nullflags_accepts_one_shot_iterable`.

## Text-policy boundary (Varchar)

The single physical record loop owns the physical Varchar contract
(`_NullFlags` bits, the length byte, full-width vs variable-width forms) and
isolates the logical bytes; their TEXT decoding always goes through the
configured parser instance (`parser.decode_text`) — never a direct
`bytes.decode` in the core.  Dependency direction: core backend → configured
parser instance; the core never imports exporter policy
(`test_vfp_varchar_migration_fallback_keeps_raw_bytes` proves the exporter's
loss-aware Polish fallback still applies to Varchar: exact logical Unicode +
original raw bytes retained under `__dbfbridge_raw_text_fields__` in JSONL,
while the policy-neutral Direct Read keeps raising the typed
`TEXT_DECODE_ERROR` for undecodable bytes).

## Correctness notes discovered while building this matrix

1. **VFP `B` (Double) vs dbfread memo detection.** dbfread demands an FPT
   companion for any table containing a `B` field (`set('MGPB')` check),
   although its own field parser reads a VFP `B` as an inline double.  A
   Double-only table therefore failed the export preflight with
   "Memo fields require the memo companion" even though no memo field
   exists.  The export/import boundaries now consult the core header
   classification and suppress only that spurious requirement — real memo
   tables keep the strict behaviour (strictness regression:
   `test_vfp_double_with_real_memo_still_requires_fpt`).
2. **NULL values of ordinary nullable fields.** A genuinely NULL-marked
   record (bit set in `_NullFlags`) decoded blank storage into `''`/`0`/
   `Decimal('0')` instead of `None`.  The shared physical loop now resolves
   a set NULL bit to `None` before any decoding or memo I/O
   (`test_vfp_nullable_ordinary_fields_null_bit_reads_as_none` failed before
   the fix).
3. **Varchar significant trailing spaces + text policy.** dbfread's `parseV`
   aliases `parseC` (blank rstrip), destroying significant trailing spaces of
   variable-length values and misreading the length byte.  The shared
   physical loop now applies the varlength contract (length-byte form vs
   full-width form) and hands the isolated logical bytes to the configured
   parser policy for text decoding
   (`test_vfp_varchar_keeps_significant_trailing_spaces` failed before the
   fix).
4. **Reconstruction verification now shares the canonical `_NullFlags`
   engine.** The importer's checksum/diagnostic path used its own
   `enumerate(nullable)` bit allocation (ignoring the varlength bits of
   preceding `V`/`Q` fields) and read the rebuilt table through dbfread's
   own record loop.  Both the writer-side NULL detection and the
   verification re-read now consume `core.nullflags` (one allocation
   engine, one physical record loop, one configured loss-aware parser), so
   NULL Varchar records and mixed nullable bitmaps reconstruct with
   canonical equality
   (`test_vfp_varchar_null_record_reconstructs_canonically`,
   `test_vfp_varchar_mixed_null_bitmap_reconstructs_canonically`,
   `test_vfp_ordinary_nullable_fields_reconstruct_canonically`,
   `test_vfp_interleaved_deleted_records_reconstruct_canonically`).

## Out of scope / non-goals

- No Q/W decoding implementation, no binary C/V parser, no CDX parsing, no
  native reader, no writer rewrite, no Direct Write, no dBASE Level 7 table
  dialect support (the `+`/`O`/`@` rows are parser-level compatibility only).
- Non-VFP tables (dBase III/IV `B`-memo semantics) are outside this matrix.
- Any type without an honest fixture is recorded as `NOT_YET_VERIFIED`.
