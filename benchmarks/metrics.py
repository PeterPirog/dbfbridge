"""Measurement helpers for the Phase 0 benchmark runner.

Benchmark-only infrastructure.  It is never imported by ``dbfbridge`` and it
adds no runtime dependencies to the library.  Every metric that cannot be
measured on the current platform is reported as ``None`` (rendered as
``NOT_AVAILABLE``), never estimated.

Root-cause note (Phase 0 diagnostic, see docs/architecture/phase-0-audit.md)
---------------------------------------------------------------------------
The original implementation sampled the working set with
``ctypes.windll.psapi.GetProcessMemoryInfo`` declared with only TWO arguments
(``HANDLE, POINTER(PROCESS_MEMORY_COUNTERS)``), omitting the mandatory third
``DWORD cb``.  On the x64 Windows calling convention that argument is passed in
``EDX``; left uninitialised it makes ``psapi`` compute an invalid output size
and overrun the heap, crashing the interpreter later with ``0xC0000005``
(reproduced with a standalone 20-line script).  A correctly declared 3-argument
call returns ``BOOL = 1`` and does not crash, so this is a ctypes FFI
declaration bug in this project's benchmark code, not a CPython defect.

The fix replaces the raw WinAPI probe with ``psutil``, which performs the same
queries through a safe implementation.  ``psutil`` is an optional dev/benchmark
dependency; when it is absent the memory/IO metrics are reported as
``NOT_AVAILABLE`` instead of crashing.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

STATUS_FAILED = "FAILED"
STATUS_MEASURED = "MEASURED"


def _psutil():
    """Return the ``psutil`` module or ``None`` when it is not installed."""

    try:
        import psutil  # benchmark-only dependency

        return psutil
    except ImportError:
        return None


def process_snapshot() -> dict[str, Any] | None:
    """Capture RSS/IO counters, or ``None`` when ``psutil`` is unavailable."""

    psutil = _psutil()
    if psutil is None:
        return None
    try:
        process = psutil.Process()
        memory = process.memory_info()
        io = process.io_counters()
        return {
            "rss_bytes": int(memory.rss),
            "virtual_bytes": int(memory.vms),
            "io_read_bytes": int(io.read_bytes),
            "io_write_bytes": int(io.write_bytes),
            "io_read_ops": int(io.read_count),
            "io_write_ops": int(io.write_count),
        }
    except Exception:
        return None


def directory_size_bytes(root: Path) -> int:
    """Total size of every regular file under *root* (0 when *root* is absent)."""

    if not root.is_dir():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def run(
    function: Callable[[], object],
    *,
    input_bytes: int | None,
    input_records: int | None,
    output_dir: Path | None = None,
) -> dict[str, object]:
    """Execute *function* once and collect honest, platform-supported metrics.

    Values that the platform or the code path cannot provide stay ``None``;
    they are never estimated or invented.  A raised exception is reported as
    ``FAILED`` with the error text (a process-level crash is additionally
    captured by the subprocess controller).
    """

    output_root = output_dir if output_dir is not None else Path(os.getcwd())
    size_before = directory_size_bytes(output_root)
    before = process_snapshot()
    cpu_before = time.process_time()
    wall_before = time.perf_counter()
    status = STATUS_MEASURED
    error: str | None = None
    try:
        function()
    except Exception as exc:  # a measured failure is a result, not a crash
        status = STATUS_FAILED
        error = f"{type(exc).__name__}: {exc}"
    wall = time.perf_counter() - wall_before
    cpu = time.process_time() - cpu_before
    size_after = directory_size_bytes(output_root)
    after = process_snapshot()

    def finite(value: float) -> float | None:
        return value if value < 1e12 else None

    result: dict[str, object] = {
        "status": status,
        "wall_seconds": round(wall, 6),
        "cpu_seconds": round(cpu, 6),
        "records_per_second": (
            finite(input_records / wall) if input_records is not None and wall > 0 else None
        ),
        "source_mib_per_second": (
            finite((float(input_bytes) / (1024 * 1024)) / wall)
            if input_bytes is not None and wall > 0
            else None
        ),
        "output_bytes_delta": size_after - size_before,
    }

    if before is not None and after is not None:
        result["rss_bytes_before"] = before["rss_bytes"]
        result["rss_bytes_after"] = after["rss_bytes"]
        result["peak_rss_sampled_bytes"] = max(before["rss_bytes"], after["rss_bytes"])
        result["read_bytes"] = after["io_read_bytes"] - before["io_read_bytes"]
        result["write_bytes"] = after["io_write_bytes"] - before["io_write_bytes"]
        result["read_ops"] = after["io_read_ops"] - before["io_read_ops"]
        result["write_ops"] = after["io_write_ops"] - before["io_write_ops"]
        if input_bytes and result.get("read_bytes"):
            result["read_amplification"] = round(int(result["read_bytes"]) / input_bytes, 4)
        if input_bytes and result.get("write_bytes"):
            result["write_amplification"] = round(int(result["write_bytes"]) / input_bytes, 4)
    else:
        for key in (
            "rss_bytes_before",
            "rss_bytes_after",
            "peak_rss_sampled_bytes",
            "read_bytes",
            "write_bytes",
            "read_ops",
            "write_ops",
        ):
            result[key] = None

    if error is not None:
        result["error"] = error
    return result
