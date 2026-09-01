"""Optional-dependency boundary for dbfbridge (stdlib-only module).

The base installation of ``dbfbridge`` needs exactly one runtime dependency
(``dbfread``) and covers ``import dbfbridge``, the whole Direct Read surface
and the DBF → JSONL/JSON/CSV migration paths.  Everything beyond that is an
explicit *extra*:

- ``[write]``  — ``dbf``: DBF/FPT reconstruction (``reconstruct_dbf``,
  ``check_conversion_quality``);
- ``[xlsx]``   — ``xlsxwriter`` (XLSX export) + ``openpyxl`` (XLSX input
  reading for reconstruction);
- ``[fast]``   — ``orjson``/``polars``: pure accelerators.  Missing fast
  dependencies NEVER raise; the stdlib/Python fallbacks keep every
  conversion working (the choice between fast and fallback engines never
  changes the logical result);
- ``[all]``    — the union of ``write``, ``xlsx`` and ``fast``;
- ``[import]`` — historical compatibility alias of ``[write]``.

Operations that genuinely need an optional dependency fail **before any side
effect** (no output directory, no ``.partial`` files, no DBF/FPT creation, no
report) with :class:`OptionalDependencyMissingError` — a typed, JSON-safe
exception carrying the machine code ``OPTIONAL_DEPENDENCY_MISSING`` and the
exact install command.  Missing dependencies are never installed, downloaded
or opened automatically: the error only tells the user what to run.
"""

from __future__ import annotations

import importlib

__all__ = ["OptionalDependencyMissingError", "require_optional"]

#: Stable machine code carried by every missing-optional-dependency failure.
CODE_OPTIONAL_DEPENDENCY_MISSING = "OPTIONAL_DEPENDENCY_MISSING"

_INSTALL_COMMAND = 'python -m pip install "dbfbridge[{extra}]"'


class OptionalDependencyMissingError(RuntimeError):
    """An operation requires an optional dependency that is not installed.

    The error is raised **before** the operation produces any side effect
    (no output directory, no temporary files, no partial artifacts).  It is
    never raised for the ``[fast]`` accelerators — those always fall back to
    the stdlib/Python engines.  The class carries a structured, JSON-safe
    payload (:meth:`to_dict`) so callers never parse the message text:

    - ``code``            — always ``"OPTIONAL_DEPENDENCY_MISSING"``;
    - ``dependency``      — the missing distribution name (e.g. ``"dbf"``);
    - ``extra``           — the pip extra that provides it (e.g. ``"write"``);
    - ``operation``       — the public operation that needs it;
    - ``install_command`` — the exact pip command to install the extra.
    """

    code = CODE_OPTIONAL_DEPENDENCY_MISSING

    def __init__(
        self,
        *,
        dependency: str,
        extra: str,
        operation: str,
        purpose: str | None = None,
    ) -> None:
        self.dependency = dependency
        self.extra = extra
        self.operation = operation
        self.purpose = purpose
        self.install_command = _INSTALL_COMMAND.format(extra=extra)
        message = (
            f"{operation} requires the optional dependency {dependency!r}, "
            f"which is provided by the {extra!r} extra.  Install it with: "
            f"{self.install_command}"
        )
        if purpose:
            message += f"  ({purpose})"
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        """JSON-safe structured payload (never parsed from the message)."""
        data = {
            "code": self.code,
            "dependency": self.dependency,
            "extra": self.extra,
            "operation": self.operation,
            "install_command": self.install_command,
        }
        if self.purpose:
            data["purpose"] = self.purpose
        return data


def require_optional(
    module_name: str,
    *,
    extra: str,
    operation: str,
    dependency: str | None = None,
    purpose: str | None = None,
) -> object:
    """Import *module_name* or raise :class:`OptionalDependencyMissingError`.

    The import is attempted normally (so an already-installed dependency is
    simply returned); an :class:`ImportError` — including a broken install —
    is converted into the typed public error.  No installation, download or
    network access is ever attempted.
    """
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise OptionalDependencyMissingError(
            dependency=dependency or module_name,
            extra=extra,
            operation=operation,
            purpose=purpose,
        ) from exc
