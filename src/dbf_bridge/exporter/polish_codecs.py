"""Compatibility re-export of the Polish codepage support.

The clean implementation (Mazovia table, codec registration, fallback chain,
and language-driver resolution) lives in :mod:`dbf_bridge.core.codecs`.
This module keeps the historical import path working:

    from dbf_bridge.exporter.polish_codecs import (
        POLISH_FALLBACK_ENCODINGS,
        register_polish_codecs,
    )
"""

from __future__ import annotations

from ..core.codecs import (
    EXTRA_DRIVER_ENCODINGS,
    POLISH_FALLBACK_ENCODINGS,
    TableCodec,
    decode_with_polish_fallback,
    driver_to_encoding,
    register_polish_codecs,
)

__all__ = [
    "EXTRA_DRIVER_ENCODINGS",
    "POLISH_FALLBACK_ENCODINGS",
    "TableCodec",
    "decode_with_polish_fallback",
    "driver_to_encoding",
    "register_polish_codecs",
]
