# VFP/FPT compatibility matrix (evidence-based)

This document records exactly what the current test suite **proves** about
Visual FoxPro physical field types and FPT memo behaviour.  It describes
only evidence, not intentions: a type is `SUPPORTED` only when a named test
exercises it end to end.  Where no honest fixture exists, the status is
`NOT_YET_VERIFIED` — never upgraded on the basis of source code alone.

Evidence names point at the suite on `main` (`tests/…`).  The deterministic
fixture builders live in `tests/vfp_fixture_factory.py`; the status legend:

| status | meaning |
|---|---|
| `SUPPORTED` | full evidence: schema + Direct Read + migration + reconstruction |
| `SUPPORTED_WITH_LIMITATION` | working with a documented, tested limitation |
| `RAW_ONLY` | physical bytes are preserved/forensically readable; decoded access is a typed error |
| `UNSUPPORTED` | typed refusal by design (schema reason + typed errors + UNSUPPORTED export status) |
| `SYSTEM_INTERNAL` | VFP system column; handled physically, not as user data |
| `NOT_YET_VERIFIED` | no honest fixture yet — behaviour unproven |

Legend for read levels: **schema** = `inspect_table`/`read_schema`, **read** =
Direct Read (`iter_records`/`read_records`), **migration** = `export_dbf`
JSONL, **recon** = `reconstruct_dbf`, **raw RT** = byte-identical DBF round
trip (canonical checksums are always checked first).

## Field types

| physical type | VFP meaning | schema | read | migration | recon | raw RT | nullable | memo/FPT | status | evidence | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `C` | Character | visible | decoded text | text | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_direct_read_records.py::test_supported_vfp_types_null_and_empty_values`, legacy suite | the workhorse type; NULLable columns add the hidden `_NullFlags` column (see type `0`) |
| `C` + flags 0x04 | NOCPTRANS/binary Character | visible, classified `nocptrans=True`, `supported=False` + reason | typed `FIELD_TYPE_UNSUPPORTED` | per-table `UNSUPPORTED` | n/a | yes (exact bytes) | n/a | no | `RAW_ONLY` | `tests/test_vfp_type_matrix.py::test_binary_character_nocptrans_is_raw_readable_but_not_decoded` | dedicated binary C/V parser is an explicit non-goal; bytes never lost |
| `V` | Varchar (0x32 tables) | visible, `dbversion_name` "Varchar/Varbinary enabled" | decoded text | text | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_vfp_varchar_direct_read_and_roundtrip` | fixture = legal C table patched to `V` with version 0x32 (identical physical layout) |
| `V` + flags 0x04 | binary Varchar | visible, `supported=False` | typed `FIELD_TYPE_UNSUPPORTED` | per-table `UNSUPPORTED` | n/a | yes | n/a | no | `RAW_ONLY` | same boundary as binary `C` (`classify_field` treats C/V identically) | binary parser non-goal; not separately fixtured |
| `N` | Numeric | visible | number | number | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_importer.py::test_jsonl_roundtrip_preserves_narrow_negative_numeric_and_complete_header`, `test_supported_vfp_types_null_and_empty_values` | includes scientific-storage restoration test |
| `F` | Float | visible | number | number | byte-identical | yes | yes | no | `SUPPORTED` | Direct Read: `test_supported_vfp_types_null_and_empty_values`; round trip: `tests/test_vfp_type_matrix.py::test_vfp_integer_float_datetime_roundtrip` | same ASCII storage as `N` |
| `I` | Integer | visible | int | number | byte-identical | yes | yes | no | `SUPPORTED` | `test_vfp_integer_float_datetime_roundtrip` (round trip) | 4-byte LE |
| `I` + mask 0x0C | VFP autoincrement Integer | `is_autoincrement=True`, next value/step exposed, `nocptrans=False` | int | flags + descriptor preserved | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_vfp_autoincrement_descriptor_survives_roundtrip` | mask 0x0C contains bit 0x04 — must never classify as NOCPTRANS/binary |
| `+` | dBase 7 autoincrement marker (outside VFP) | visible, never autoincrement evidence inside VFP | int with valid 4-byte layout | number | untested for round trip | yes | n/a | no | `SUPPORTED_WITH_LIMITATION` | schema: `tests/test_direct_read_schema.py::test_plus_type_is_never_vfp_autoincrement_evidence`; read: `test_vfp_type_matrix.py::test_vfp_plus_type_reads_as_integer_with_valid_layout` | a wrong-width `+` layout is a typed `DBF_RECORD_INVALID` (struct.error boundary); VFP tables should use `I` + 0x0C |
| `Y` | Currency | visible | `Decimal` with 4-digit scale | exact decimal string | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_vfp_currency_direct_read_and_roundtrip_preserves_decimal_value` | 8-byte scaled int ×10⁻⁴ |
| `B` (VFP, length 8) | Double (inline) | visible, `supported=True`, `is_memo=False` | float | number | byte-identical | yes | yes | no | `SUPPORTED` | `tests/test_vfp_type_matrix.py::test_vfp_double_table_without_fpt_exports_and_roundtrips` | a Double-only table needs **no FPT**; dbfread's own memo detection wrongly demands one, the export/import boundaries suppress that spurious requirement (see "correctness notes") |
| `B` (non-VFP) | binary memo pointer | memo classification | n/a for VFP focus | n/a | n/a | n/a | n/a | pointer | `NOT_YET_VERIFIED` (non-VFP tables out of this matrix's scope) | `dbfread` behaviour only | dBase III/IV `B` tables are outside the VFP matrix |
| `O` | Double (dBase 7 alias) | visible, supported | float (8-byte IEEE) | number | byte-identical | yes | yes | no | `SUPPORTED_WITH_LIMITATION` | `tests/test_vfp_type_matrix.py::test_vfp_double_alias_o_reads_like_a_double` | fixture = B patched to O (identical layout); `O` is not a native VFP type |
| `@` | dBase 7 Timestamp | visible, `dbf_type_name` "Timestamp" | `datetime` (same 8-byte layout as `T`) | ISO datetime | untested for round trip | yes | yes | no | `SUPPORTED_WITH_LIMITATION` | `tests/test_vfp_type_matrix.py::test_vfp_timestamp_alias_decodes_like_datetime` | fixture = T patched to `@`; `@` is not a native VFP type |
| `T` | DateTime | visible | `datetime` | ISO datetime | byte-identical | yes | yes | no | `SUPPORTED` | `test_vfp_integer_float_datetime_roundtrip` | |
| `D` | Date | visible | `date` | ISO date | byte-identical | yes | yes | no | `SUPPORTED` | existing suite (`test_supported_vfp_types_null_and_empty_values` + round trips) | |
| `L` | Logical | visible | bool / None | boolean-or-null | byte-identical | yes | yes | no | `SUPPORTED` | existing suite | |
| `M` | Memo (text) | visible, memo | lazy/inline/null/skip policies | text (+base64 for binary blocks) | byte-identical DBF; FPT content canonical | yes | yes | yes | `SUPPORTED` | existing memo suite + `test_vfp_type_matrix.py::test_memo_block_type_decides_text_vs_binary_decoding` | per-block type byte decides text vs binary decoding |
| `G` | General/OLE memo | visible, binary memo | inline payload bytes | base64 | DBF byte-identical; FPT content restored, raw FPT layout may differ (report `WARNING`) | partial (DBF yes) | yes | yes | `SUPPORTED_WITH_LIMITATION` | `tests/test_vfp_type_matrix.py::test_general_and_picture_memo_roundtrip_keeps_canonical_identity` | rebuilt FPT keeps payload + pointer but not byte-identical block layout — proven by `raw_fpt_match: false` + explicit warning in the reconstruction report |
| `P` | Picture memo | visible, binary memo | inline payload bytes | base64 | as `G` | partial (DBF yes) | yes | yes | `SUPPORTED_WITH_LIMITATION` | `test_general_and_picture_memo_roundtrip_keeps_canonical_identity[picture]` | same FPT-layout limitation |
| `Q` | Varbinary (inline) | visible, `supported=False` + reason | typed `FIELD_TYPE_UNSUPPORTED` | per-table `UNSUPPORTED` | n/a | yes | n/a | no | `UNSUPPORTED` | `tests/test_vfp_type_matrix.py::test_unsupported_varbinary_is_raw_readable_but_not_decoded`, projection exception: existing `test_unselected_unsupported_field_does_not_block_reading` | no auto-implementation; forensic raw access stays available |
| `W` | Blob (FPT pointer) | visible, `supported=False` + reason | typed `FIELD_TYPE_UNSUPPORTED` when selected; `memo="skip"` removes it | per-table `UNSUPPORTED` | n/a | yes | n/a | yes (pointer) | `UNSUPPORTED` | `tests/test_vfp_type_matrix.py::test_unsupported_blob_is_raw_readable_but_not_decoded`; skip contract: existing `test_skip_removes_unsupported_memo_field` | no auto-implementation |
| `0` | hidden `_NullFlags` system column | visible, `system=True`, binary | raw flag bytes | base64 | byte-identical | yes | n/a (it *is* the null bitmap) | no | `SYSTEM_INTERNAL` | `tests/test_vfp_type_matrix.py::test_nullflags_system_column_is_raw_readable_and_roundtrips` | appears automatically for NULLable columns; never presented as normal user data |
| `X` | (any unknown byte) | header-invalid / unknown type handling | typed errors | — | — | — | — | — | `NOT_YET_VERIFIED` | no fixture | no honest synthetic layout — deliberately left unproven rather than faked |

## FPT edge cases

| case | behaviour | evidence |
|---|---|---|
| missing FPT (inline) | typed `FPT_REQUIRED_MISSING` | existing `tests/test_direct_read_records.py::test_missing_fpt_inline_is_fpt_required_missing` |
| FPT vanishes before lazy load | typed `FPT_REQUIRED_MISSING` at `load()` | existing `test_lazy_load_missing_companion_is_typed` |
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

## Correctness notes discovered while building this matrix

1. **VFP `B` (Double) vs dbfread memo detection.** dbfread demands an FPT
   companion for any table containing a `B` field (`set('MGPB')` check),
   although its own field parser reads a VFP `B` as an inline double.  A
   Double-only table therefore failed the export preflight with
   "Memo fields require the memo companion" even though no memo field
   exists.  The export/import boundaries now consult the core header
   classification (which knows a VFP `B` is inline) and suppress only that
   spurious requirement — real memo tables keep the strict behaviour.
   Evidence: `test_vfp_double_table_without_fpt_exports_and_roundtrips`
   (failed before the fix), `tests/test_importer.py` suite unchanged.

## Out of scope / non-goals

- No Q/W implementation, no binary C/V parser, no CDX parsing, no native
  reader, no writer rewrite, no Direct Write.
- Non-VFP tables (dBase III/IV B-memo semantics) are outside this matrix.
- Any type without an honest fixture is recorded as `NOT_YET_VERIFIED`.