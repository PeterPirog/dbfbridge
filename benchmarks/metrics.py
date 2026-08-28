"""Measurement helpers for the Phase 0 benchmark runner.

Benchmark-only infrastructure.  It is never imported by ``dbfbridge`` and it
adds no runtime dependencies to the library.  Every metric that cannot be
measured on the current platform is reported as ``None`` (rendered as
``NOT_AVAILABLE``), never estimated.

Measurement model
-----------------
- Wall time is taken with ``time.perf_counter()`` and CPU time with
  ``time.process_time()`` around the measured callable.
- Peak RSS is measured by **sampling** the process RSS with ``psutil`` from a
  background thread while the measured callable runs, and reporting the maximum
  sample together with the sampling rate.  The sampler is always stopped and
  joined in a ``finally`` block, including when the callable raises.  If
  ``psutil`` is unavailable, peak RSS is ``None`` (NOT_AVAILABLE).
- Physical memory for the environment report uses ``psutil.virtual_memory()``;
  without ``psutil`` it is ``None`` (NOT_AVAILABLE).
- There is **no** direct Windows API (ctypes) usage anywhere in this module or
  the rest of ``benchmarks/``.
"""

from __future__ import annotations

import contextlib
import threading
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


def physical_memory_bytes() -> int | None:
    """Total physical memory via ``psutil``, or ``None`` when unavailable."""

    psutil = _psutil()
    if psutil is None:
        return None
    try:
        return int(psutil.virtual_memory().total)
    except Exception:
        return None


class RssSampler:
    """Sample the process RSS on a background thread while a block runs.

    The sampler records every sample and is guaranteed to be stopped and joined
    in ``finally``, so no thread leaks even when the measured block raises.
    The reported peak is the maximum of the samples (a true observed peak, not
    a single before/after delta).
    """

    def __init__(self, interval: float = 0.005) -> None:
        self.interval = interval
        self._samples: list[int] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def peak_bytes(self) -> int | None:
        return max(self._samples) if self._samples else None

    def _loop(self) -> None:
        psutil = _psutil()
        if psutil is None:
            return
        process = psutil.Process()
        while not self._stop.is_set():
            try:
                rss = int(process.memory_info().rss)
            except Exception:
                rss = None
            if rss is not None:
                self._samples.append(rss)
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None


def directory_size_bytes(root: Path) -> int:
    """Total size of every regular file under *root* (0 when *root* is absent)."""

    if not root.is_dir():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def temporary_bytes_in(root: Path) -> int:
    """Best-effort total size of ``*.partial*`` files under *root*.

    Used only to *report* temporary artefacts left behind by a run.  It is not
    a reliable per-operation temporary-bytes counter, so callers should treat
    it as informational and the true ``temporary_bytes`` metric is reported as
    NOT_AVAILABLE unless a scenario can measure it directly.
    """

    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and ".partial" in path.name:
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total


def run(
    function: Callable[[], object],
    *,
    input_bytes: int | None,
    input_records: int | None,
    output_dir: Path,
    warmup: bool = False,
) -> dict[str, object]:
    """Execute *function* once and collect honest, platform-supported metrics.

    ``output_dir`` is the scenario/rep-specific directory the operation writes
    into; ``output_bytes`` is its final size, so re-running an overwritten
    scenario still reports the real output size (never a zero from a
    before/after diff on a shared directory).

    Wall and CPU times are captured **immediately** after the measured call
    returns (or raises), *before* the RSS sampler is stopped and joined — so
    the reported times cover the measured call plus the small overhead of the
    active sampler thread, but NOT the cost of stopping/joining it.  The
    sampler is always stopped and joined in ``finally``.

    Values that the platform or the code path cannot provide stay ``None``;
    they are never estimated or invented.  A raised exception is reported as
    ``FAILED`` with the error text.
    """

    output_bytes = 0
    if output_dir.is_dir():
        output_bytes = directory_size_bytes(output_dir)

    before = process_snapshot()
    sampler = RssSampler()
    sampler.start()
    cpu_before = time.process_time()
    wall_before = time.perf_counter()
    status = STATUS_MEASURED
    error: str | None = None
    try:
        try:
            function()
        except Exception as exc:  # a measured failure is a result, not a crash
            status = STATUS_FAILED
            error = f"{type(exc).__name__}: {exc}"
        # Wall and CPU are captured IMMEDIATELY after the measured call ends
        # (or raises), before the sampler is stopped and joined: the reported
        # times include the tiny overhead of the active sampler thread but not
        # the cost of stopping/joining it.
        wall = time.perf_counter() - wall_before
        cpu = time.process_time() - cpu_before
    finally:
        # The sampler is always stopped and joined, even when the call raised.
        sampler.stop()

    # Re-measure the scenario's own output directory (authoritative size).
    if output_dir.is_dir():
        output_bytes = directory_size_bytes(output_dir)

    after = process_snapshot()

    def finite(value: float) -> float | None:
        return value if value < 1e12 else None

    result: dict[str, object] = {
        "status": status,
        "warmup": warmup,
        "input_bytes": input_bytes,
        "input_records": input_records,
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
        # Authoritative output size for this scenario/rep.
        "output_bytes": output_bytes,
        # temporary_bytes is NOT reliably measurable from the outside for the
        # library's atomic writes; report None (NOT_AVAILABLE), never a guess.
        "temporary_bytes": None,
    }

    # Peak RSS: a true sampled maximum (None -> NOT_AVAILABLE without psutil).
    peak = sampler.peak_bytes
    if peak is not None:
        result["peak_rss_bytes"] = peak
        result["rss_samples"] = sampler.sample_count
        result["rss_sample_interval_seconds"] = sampler.interval
    else:
        result["peak_rss_bytes"] = None
        result["rss_samples"] = None
        result["rss_sample_interval_seconds"] = None

    if before is not None and after is not None:
        result["io_read_bytes_delta"] = after["io_read_bytes"] - before["io_read_bytes"]
        result["io_write_bytes_delta"] = after["io_write_bytes"] - before["io_write_bytes"]
        result["io_read_ops_delta"] = after["io_read_ops"] - before["io_read_ops"]
        result["io_write_ops_delta"] = after["io_write_ops"] - before["io_write_ops"]
    else:
        for key in (
            "io_read_bytes_delta",
            "io_write_bytes_delta",
            "io_read_ops_delta",
            "io_write_ops_delta",
        ):
            result[key] = None

    if error is not None:
        result["error"] = error
    return result
