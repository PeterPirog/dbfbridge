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

3. In the GitHub repository, create the `pypi` environment and require manual approval.
4. Do not add a `PYPI_TOKEN`; `.github/workflows/publish.yml` requests a short-lived OIDC
   credential with the job-scoped `id-token: write` permission.

For an existing PyPI project, add the same publisher in that project's Publishing
settings instead of creating a pending publisher.

## Prepare a release

1. Choose a PEP 440 version that has never been uploaded to PyPI.
2. Set the same version in `pyproject.toml` and `src/dbf_bridge/__init__.py`.
3. Move user-visible changes from `Unreleased` to a dated section in `CHANGELOG.md`.
4. Update README and examples for changed behavior, commands, dependencies, or APIs.
5. From a clean checkout and virtual environment run:

   ```bash
   python -m pip install -e ".[dev]"
   python -m ruff check src tests benchmarks examples
   python -m pytest
   python -m build
   python -m twine check dist/*
   ```

6. Install the newly built wheel, not the source tree, and verify both import packages and
   all four console entry points.
7. Confirm that CI is green for the release commit.

## Publish

1. Create a Git tag exactly matching `v<project.version>`, for example `v0.1.0`.
2. Create and publish a GitHub Release from that tag, using the matching changelog section
   as its notes.
3. Approve the protected `pypi` environment deployment.
4. The `Publish to PyPI` workflow validates the tag, builds wheel and sdist once, runs
   `twine check`, and publishes those exact artifacts through Trusted Publishing.

Never rebuild and manually upload different files for the same version.

## Verify after publication

Create a new environment that cannot import the repository checkout:

```bash
python -m venv .venv-pypi-check
.venv-pypi-check/bin/python -m pip install --upgrade pip
.venv-pypi-check/bin/python -m pip install dbfbridge==0.1.0
.venv-pypi-check/bin/python -c "from dbfbridge import export_dbf; print(export_dbf)"
.venv-pypi-check/bin/dbf-bridge --help
```

On Windows, use `.venv-pypi-check\Scripts\python.exe` and
`.venv-pypi-check\Scripts\dbf-bridge.exe`. Confirm the project description, license,
Python requirement, dependency list, source links, wheel, sdist, and release provenance on
PyPI. If a serious defect is discovered, publish a new patch version; PyPI does not permit
replacing an existing file.
