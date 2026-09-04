# Publishing dbfbridge

This checklist is the release authority for the `dbfbridge` distribution. PyPI files and
version numbers are immutable, so do not publish until every step below succeeds.

## One-time PyPI configuration

Use PyPI Trusted Publishing instead of storing an API token in GitHub:

1. Sign in to PyPI and create a pending Trusted Publisher for a new project.
2. Enter these values exactly:

   | Setting | Value |
   |---|---|
   | PyPI project name | `dbfbridge` |
   | GitHub owner | `PeterPirog` |
   | GitHub repository | `dbfbridge` |
   | Workflow | `publish.yml` |
   | Environment | `pypi` |

3. In the GitHub repository, create an environment named exactly `pypi`. Manual approval
   is recommended when another trusted maintainer can act as reviewer. In a single-owner
   repository, do not add a reviewer rule that prevents the owner from approving their
   own deployment; the environment name is required for Trusted Publishing, but a
   reviewer rule is an independent GitHub protection setting.
4. Do not add a `PYPI_TOKEN`; `.github/workflows/publish.yml` requests a short-lived OIDC
   credential with the job-scoped `id-token: write` permission.

For an existing PyPI project, add the same publisher in that project's Publishing
settings instead of creating a pending publisher.

## Prepare a release (release-preparation PR)

1. Choose a PEP 440 version that has never been uploaded to PyPI.
2. Set the same version in `pyproject.toml` and `src/dbf_bridge/__init__.py`.
3. Fill the matching `CHANGELOG.md` section; while the release is only being
   prepared, its heading may carry the `Unreleased` placeholder.
4. Update README and examples for changed behavior, commands, dependencies, or APIs.

## Finalize the release commit (before creating the tag)

The tag must point at a commit that **already** contains the final release
state. Tagging first and modifying `CHANGELOG.md`/docs afterwards is never
allowed. One final release commit — created before the tag — must:

- keep `project.version` in `pyproject.toml` and `__version__` in
  `src/dbf_bridge/__init__.py` at the release version;
- replace the `Unreleased` CHANGELOG heading with the real publication date:
  `## [X.Y.Z] - YYYY-MM-DD`;
- remove the release-preparation status wording from `README.md`
  ("release candidate", "release is being prepared", "not yet published");
- replace the availability note in `docs/pypi-usage.md` with timeless
  released-distribution wording, for example:

  > This guide documents the dbfbridge X.Y.Z PyPI distribution.
  > Check PyPI for currently available releases.

  The final docs must not claim "currently available" for a specific
  version either — that ages immediately and cannot be verified from the
  repository;
- keep the migration guide and examples describing the final released
  contract.

Then run the deterministic release-state gate — it fails while any
release-preparation marker remains:

```bash
python scripts/check_release_state.py --tag vX.Y.Z
```

## Validate the exact release commit

From a clean checkout and virtual environment run:

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests benchmarks examples
python -m pytest
python -m build
python -m twine check dist/*
```

Then install the newly built wheel, not the source tree, and verify both import packages and
all four console entry points.

## Publish

1. Verify the PyPI Trusted Publisher configuration is ready (see the
   one-time configuration above).
2. Merge/finalize the exact final release commit on `main` and confirm that
   CI is green for that exact commit.
3. Create an annotated Git tag exactly matching `v<project.version>` —
   a release of version `X.Y.Z` is tagged `vX.Y.Z` — pointing at exactly
   that final release commit.
4. Create and publish a GitHub Release from that tag, using the matching
   changelog section as its notes.
5. Approve the protected `pypi` environment deployment.
6. The `Publish to PyPI` workflow checks out the tag, runs the
   release-state gate, builds **one wheel and one sdist**, runs
   `twine check`, verifies wheel metadata and sdist sanity, runs the two
   smoke suites (release wheel smoke + PyPI install-profile smoke) on the
   exact wheel, uploads the artifact once, and publishes those exact files
   through Trusted Publishing.

Never tag first and then modify `CHANGELOG.md` or docs. Never rebuild and
manually upload different files for the same version.

## Verify after publication

Create a fresh environment **outside the repository checkout** and set the
published version once — never edit this checklist per release.

POSIX (bash):

```bash
RELEASE_VERSION=X.Y.Z   # the version just published
python -m venv .venv-pypi-check
.venv-pypi-check/bin/python -m pip install --upgrade pip
.venv-pypi-check/bin/python -m pip install "dbfbridge==$RELEASE_VERSION"
```

Windows PowerShell:

```powershell
$ReleaseVersion = "X.Y.Z"   # the version just published
py -3.12 -m venv .venv-pypi-check
.\.venv-pypi-check\Scripts\python.exe -m pip install --upgrade pip
.\.venv-pypi-check\Scripts\python.exe -m pip install "dbfbridge==$ReleaseVersion"
```

Then verify, in order (POSIX paths shown; on Windows use
`.venv-pypi-check\Scripts\python.exe` and
`.venv-pypi-check\Scripts\<command>.exe`):

1. **Version is exact** and the import comes from the venv, never from a
   source tree:

   ```bash
   .venv-pypi-check/bin/python -c "import dbfbridge; print(dbfbridge.__version__, dbfbridge.__file__)"
   ```

   The version must equal `$RELEASE_VERSION` (PowerShell:
   `$ReleaseVersion`) and `__file__` must point inside
   `.venv-pypi-check` (`site-packages`).
2. **`pip show` metadata**: name, version, license, `Requires-Python`,
   `Requires-Dist` (base: `dbfread` only), project URLs.

   ```bash
   .venv-pypi-check/bin/python -m pip show dbfbridge
   ```
3. **All four console commands** respond:

   ```bash
   .venv-pypi-check/bin/dbf-bridge --help
   .venv-pypi-check/bin/dbf-bridge-import --help
   .venv-pypi-check/bin/dbf-bridge-verify --help
   .venv-pypi-check/bin/dbf-bridge-quality --help
   ```
4. **Base Direct Read smoke** on a synthetic DBF file: `inspect_table`,
   `read_schema`, `iter_records` (including `progress=`/`cancel_check=`),
   `read_records`, `iter_raw_records`.
5. **Base JSONL migration smoke**: `export_dbf(..., formats=("jsonl",))`
   produces `*.jsonl` + `<table>_schema.json` + `migration_report.jsonl`.
6. **`[write]` smoke**: install `"dbfbridge[write]"` and reconstruct the
   JSONL export back to DBF/FPT with `reconstruct_dbf`.
7. **Optional extras as appropriate**: `[xlsx]` export, `[write,xlsx]`
   XLSX → DBF reconstruction, `[fast]` accelerators, `[all]` complete
   profile (or rely on the CI/Publish `pypi_install_smoke.py` run of the
   exact artifact, which covers every profile, including the `[import]`
   compatibility alias).
8. **No source-tree import**: the checks above run from a directory that is
   not the repository checkout and with no `PYTHONPATH` set.
9. **PyPI project page**: description, license, Python requirement,
   dependency list, project URLs, and both a wheel and an sdist are visible
   for the release.
10. **Release provenance/attestations**: the GitHub Release points at the
    tag, the publish workflow run built the exact artifacts, and the PyPI
    upload provenance/attestation metadata (Trusted Publishing) is present
    on the release files.

If a serious defect is discovered, publish a new patch
version; PyPI does not permit replacing an existing file.

## If publication fails

Do not reuse the version number if PyPI accepted either distribution. First determine
whether the failure occurred before or after upload:

1. Open the failed `Publish to PyPI` run and inspect the first failing step.
2. If the build or tests failed, fix the repository, increment the patch version, and
   publish a new tag and GitHub Release.
3. If Trusted Publishing failed before upload, verify the PyPI publisher owner,
   repository, workflow filename, environment name, and exact release tag. Correct the
   configuration and use GitHub's **Re-run failed jobs** action.
4. Check the PyPI project page before retrying. Once a filename/version exists there,
   prepare a new patch release instead of attempting to replace it.
