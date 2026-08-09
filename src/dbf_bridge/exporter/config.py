from __future__ import annotations

from pathlib import Path

from .models import (
    DecodeErrors,
    DeletedPolicy,
    ExportConfig,
    ExportFormat,
    MemoPolicy,
    MissingMemoPolicy,
)


class ConfigError(ValueError):
    """Raised when CLI configuration is unsafe or inconsistent."""


def make_config(
    *,
    source: Path,
    output: Path,
    export_format: ExportFormat = "jsonl",
    encoding: str = "auto",
    decode_errors: DecodeErrors = "strict",
    deleted: DeletedPolicy = "skip",
    missing_memo: MissingMemoPolicy = "fail",
    memo: MemoPolicy = "inline",
    strip_spaces: bool = False,
    validate: bool = True,
    overwrite: bool = True,
) -> ExportConfig:
    encoding_override = None if encoding == "auto" else encoding
    config = ExportConfig(
        source=source,
        output=output,
        format=export_format,
        encoding=encoding_override,
        decode_errors=decode_errors,
        deleted=deleted,
        missing_memo=missing_memo,
        memo=memo,
        strip_spaces=strip_spaces,
        validate=validate,
        overwrite=overwrite,
    )
    validate_config(config)
    return config


def validate_config(config: ExportConfig) -> None:
    source = config.source.resolve(strict=True)
    if not source.is_dir() and not (source.is_file() and source.suffix.lower() == ".dbf"):
        raise ConfigError(f"Source path is not a directory or DBF file: {config.source}")

    output = config.output.resolve(strict=False)
    if output == source:
        raise ConfigError("Output directory must not be the same as source directory.")
    if source.is_dir() and _is_relative_to(output, source):
        raise ConfigError("Output directory must not be inside the source directory.")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
