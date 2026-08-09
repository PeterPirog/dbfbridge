"""Public Python API for Visual FoxPro migration and reconstruction.

The preferred import after installing the ``dbfbridge`` distribution is::

    from dbfbridge import export_dbf, reconstruct_dbf

The historical ``dbf_bridge`` package name exports the same API.
"""

from __future__ import annotations

from .exporter.polish_codecs import register_polish_codecs

__version__ = "0.1.0"

register_polish_codecs()

from .api import (  # noqa: E402
    ProgressCallback,
    check_conversion_quality,
    export_dbf,
    reconstruct_dbf,
    verify_conversion,
)
from .api_models import (  # noqa: E402
    DBFBridgeRunError,
    ExportOptions,
    ExportRunResult,
    ProgressEvent,
    QualityRunResult,
    ReconstructionOptions,
    ReconstructionRunResult,
    VerificationRunResult,
)
from .exporter.models import (  # noqa: E402
    DecodeErrors,
    DeletedPolicy,
    MemoPolicy,
    MissingMemoPolicy,
    OutputFormat,
    TableResult,
    TableStatus,
)
from .importer.models import InputFormat, ReconstructionResult  # noqa: E402

__all__ = [
    "DBFBridgeRunError",
    "DecodeErrors",
    "DeletedPolicy",
    "ExportOptions",
    "ExportRunResult",
    "InputFormat",
    "MemoPolicy",
    "MissingMemoPolicy",
    "OutputFormat",
    "ProgressCallback",
    "ProgressEvent",
    "QualityRunResult",
    "ReconstructionOptions",
    "ReconstructionResult",
    "ReconstructionRunResult",
    "TableResult",
    "TableStatus",
    "VerificationRunResult",
    "__version__",
    "check_conversion_quality",
    "export_dbf",
    "reconstruct_dbf",
    "verify_conversion",
]
