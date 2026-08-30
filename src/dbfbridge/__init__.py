"""Recommended public import name for the ``dbfbridge`` distribution.

This namespace is a thin alias of the historical ``dbf_bridge`` package.
Importing it has no side effects (no codec registration, no CLI, no optional
heavy dependencies); public symbols are resolved lazily from ``dbf_bridge``.
"""

from __future__ import annotations

from typing import Any

import dbf_bridge

__version__ = dbf_bridge.__version__
__all__ = list(dbf_bridge.__all__)


def __getattr__(name: str) -> Any:
    return getattr(dbf_bridge, name)


def __dir__() -> list[str]:
    return sorted(set(dbf_bridge.__all__) | set(vars(dbf_bridge)))
