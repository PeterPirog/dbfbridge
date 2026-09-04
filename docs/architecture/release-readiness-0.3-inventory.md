# 0.3.0 RELEASE-READINESS INVENTORY (read-only audit)

## 1. Semver + package metadata

- project version: **0.2.0** on development main (correct — 0.3.0 bump happens in the release task)
- pyproject: name=dbfbridge, requires-python >=3.10, extras write/xlsx/fast/all/import, dev/benchmark ✅
- **PASS**

## 2. SPDX + LICENSE

- LICENSE: MIT ✅ (referenced from README, pyproject license field)
- **PASS**

## 3. Trusted Publishing

- `.github/workflows/publish.yml`: release published trigger, GITHUB_REF_NAME == v<project.version> gate,
  `--verify-tag`, pypa/gh-action-pypi-publish, OIDC only, no token — verified in prior task ✅
- **PASS**

## 4. wheel/sdist

- `python -m build` + `twine check dist/*` PASSED ✅
- release_wheel_smoke.py PASS (fresh venv, Direct Read + legacy + CLI) ✅
- pypi_install_smoke.py PASS (base inventory, Direct Read, explicit mazovia, unknown codec, progress/cancel, real Mazovia bytes) ✅
- **PASS**

## 5. Python 3.10–3.14 matrix

- CI: Ubuntu 3.10/3.11/3.12/3.13/3.14 + Windows 3.12 — all SUCCESS ✅
- **PASS**

## 6. Windows CI

- windows-latest 3.12 in CI matrix + windows-2025 in performance workflow ✅
- **PASS**

## 7. Linux pure core

- core/ has no Windows-only code paths; Linux CI matrix green ✅
- **PASS**

## 8. CHANGELOG

- CHANGELOG.md exists with Unreleased + 0.2.0 sections ✅
- **GAP: 0.3.0 changelog section not yet written** (release task item)
- **GAP**

## 9. Migration guide

- Reconstruction/export guide present in README + docs/pypi-usage.md (source/export/reconstruct/verify) ✅
- **GAP: dedicated migration guide section not consolidated; partial coverage in README/docs — release-readiness audit item**
- **GAP**

## 10. typed API / py.typed

- py.typed marker present (typed API), from __future__ annotations, complete type hints, `__all__` synchronized ✅
- **PASS**

## 11. import side effects

- fresh-process verified: `import dbfbridge` loads no heavy deps (dbf/dbfread/orjson/polars/openpyxl/xlsxwriter), registers no codepages, imports no exporter — regression tests in test_explicit_encoding.py + test_optional_dependencies.py ✅
- **PASS**

## 12. runtime network

- zero runtime network verified (read-only library; no telemetry, no install-time fetches) ✅
- **PASS**

## 13. public API examples

- examples/inspect_table.py, examples/read_records.py, examples/python_api.py — all executable from PyPI-installed usage ✅
- **PASS**

## 14. VFP/CDX guarantees

- structural CDX flag + companion presence reported; CDX tag definitions NOT reconstructed (documented limitation) ✅
- **PASS**

## 15. PyPI usage guide

- docs/pypi-usage.md: complete (requirements, venv, pip install, verify, profiles, Direct Read, schema, streaming, pagination, deleted, memo, raw, export, JSON/CSV fallback, reconstruct, XLSX, full install, CLI, structured errors, progress+cancellation, encodings, limitations) ✅
- **PASS**

## 16. compatibility matrix

- NOT YET DOCUMENTED as an explicit compatibility matrix document — **GAP** (release-readiness task)
- **GAP**

## Summary

Material release-readiness gaps (for the NEXT task):
1. 0.3.0 CHANGELOG section
2. dedicated migration guide (or confirmed inline coverage)
3. compatibility matrix document
All three are documentation items; no code gaps were found.