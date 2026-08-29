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
  ``psutil`` or the current process is unavailable, peak RSS is ``None`` with
  a diagnostic reason (NOT_AVAILABLE), never an unhandled thread exception.
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
        self.unavailable_reason: str | None = None

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def peak_bytes(self) -> int | None:
        return max(self._samples) if self._samples else None

    def _loop(self) -> None:
        psutil = _psutil()
        if psutil is None:
            self.unavailable_reason = "psutil is not installed"
            return
        try:
            process = psutil.Process()
        except Exception as exc:
            # Process discovery can fail in short-lived subprocesses and
            # restricted containers.  Missing RSS is an explicit
            # NOT_AVAILABLE metric, never an unhandled sampler-thread error.
            self.unavailable_reason = f"could not open current process: {type(exc).__name__}: {exc}"
            return
        while not self._stop.is_set():
            try:
                rss = int(process.memory_info().rss)
            except Exception as exc:
                self.unavailable_reason = (
                    f"could not sample current process: {type(exc).__name__}: {exc}"
                )
                return
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
    """Best-effort total size of ``*.partial*`` files left under *root* (info only).

    This is NOT the ``temporary_bytes_written`` metric — it merely reports
    temporary artefacts left behind by a run (there should be none).
    """

    total = 0
    for path in temporary_files_in(root):
        with contextlib.suppress(OSError):
            total += path.stat().st_size
    return total


def temporary_files_in(root: Path) -> list[Path]:
    """Return atomic-write temporary artifacts still present under *root*."""

    if not root.is_dir():
        return []
    return [
        path for path in root.rglob("*") if path.is_file() and "partial" in path.name.split(".")
    ]


class AtomicPublishTracker:
    """Benchmark-only interception of the atomic ``.partial -> final`` publish.

    dbfbridge publishes every output file by writing a sibling temporary file,
    flushing/fsyncing, then calling ``os.replace(partial, final)``.  The
    production conventions are:

    - ``name.partial``                 (JSONL/CSV/JSON reports);
    - ``.name.partial.dbf``            (reconstructed DBF);
    - ``.name.partial.fpt``            (reconstructed FPT);
    - ``.name.raw-layout.partial``     (raw-layout restoration).

    So ``.partial`` is treated as a **name segment** (any of the above), not
    just a suffix.  This tracker temporarily replaces ``os.replace`` (in this
    worker subprocess only) and, for every publish whose *source* lies inside
    the scenario's ``output_dir``, records the **logical size of the temporary
    file**.  The sum over all successful publishes is the
    ``temporary_bytes_written`` metric.

    Rules:
    - Only publishes inside ``output_dir`` count (a stray ``.partial`` elsewhere
      is ignored).
    - There is **no** deduplication: every successful ``os.replace`` is a
      separate record, even if the same path is published again.
    - The size is read **before** the replace, but added to the total and the
      count only **after** the replace succeeds — a failed replace is not a
      publish.
    - The original ``os.replace`` is always restored in ``finally``.
    - No production code is modified; this is NOT an
      ``io_write_bytes - output_bytes`` guess.
    - A real 0 when no temporary file was published; ``NOT_AVAILABLE`` (with a
      reason) only if the platform forbids reading the temporary file.
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir.resolve()
        self.total_bytes = 0
        self.publish_count = 0
        self.unavailable_reason: str | None = None
        self._real_replace: Callable[..., Any] | None = None
        self._active = False

    @property
    def measured(self) -> bool:
        """True when no publish hit the NOT_AVAILABLE path (reason is ``None``)."""

        return self.unavailable_reason is None

    @staticmethod
    def _is_partial(name: str) -> bool:
        """True when the *name* (not the path) is one of dbfbridge's temporary
        publish conventions.

        ``.partial`` is treated as a **dot-delimited name segment**: the file
        is a temporary publish file iff ``partial`` is a token of the
        dot-split name AND the token is *not* the only token (a file literally
        named ``partial`` is final output, not a temporary).  This matches all
        production conventions and nothing else:

        - ``name.partial``                (JSONL/CSV/JSON reports);
        - ``.name.partial.dbf``           (reconstructed DBF);
        - ``.name.partial.fpt``           (reconstructed FPT);
        - ``.name.raw-layout.partial``    (raw-layout restoration).
        """

        tokens = name.split(".")
        return "partial" in tokens and len(tokens) > 1

    def __enter__(self) -> AtomicPublishTracker:
        import os

        self._real_replace = os.replace

        def _tracked_replace(src, dst, *args, **kwargs):
            src_path = Path(src)
            inside = False
            if self._is_partial(src_path.name):
                with contextlib.suppress(OSError, ValueError):
                    inside = src_path.resolve().is_relative_to(self.output_dir)
            size: int | None = None
            if inside:
                try:
                    size = src_path.stat().st_size
                except OSError as exc:
                    if self.unavailable_reason is None:
                        self.unavailable_reason = f"could not stat {src_path.name}: {exc}"
            assert self._real_replace is not None
            result = self._real_replace(src, dst, *args, **kwargs)
            if inside and size is not None:
                self.total_bytes += size
                self.publish_count += 1
            return result

        os.replace = _tracked_replace  # type: ignore[assignment]
        self._active = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        import os

        if self._real_replace is not None:
            os.replace = self._real_replace  # type: ignore[assignment]
            self._real_replace = None
        self._active = False
        return False


def read_amplification(io_read_delta: int | None, input_bytes: int | None) -> float | None:
    """``process I/O read bytes / input bytes``.

    Computed from the **measured** psutil process I/O counter delta and the
    source bytes.  These are OS-level byte counters (cache/page-cache aware,
    platform dependent); the ratio is therefore a *measured system ratio*, not
    a logical read-count.  ``None`` when either term is unavailable or the
    denominator is zero.
    """

    if io_read_delta is None or not input_bytes:
        return None
    value = io_read_delta / input_bytes
    return round(value, 4) if value < 1e12 else None


def write_amplification(io_write_delta: int | None, output_bytes: int | None) -> float | None:
    """``process I/O write bytes / output bytes`` (same semantics as read)."""

    if io_write_delta is None or not output_bytes:
        return None
    value = io_write_delta / output_bytes
    return round(value, 4) if value < 1e12 else None


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
    tracker = AtomicPublishTracker(output_dir)
    tracker.__enter__()
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
        # The sampler is always stopped and joined, and the os.replace hook is
        # always restored, even when the call raised.
        sampler.stop()
        tracker.__exit__(None, None, None)

    # Re-measure the scenario's own output directory (authoritative size).
    if output_dir.is_dir():
        output_bytes = directory_size_bytes(output_dir)

    # Atomic-publish integrity is checked after the measured window. A
    # successful operation may not leave any .partial name segment behind.
    temporary_files_left = temporary_files_in(output_dir)
    temporary_bytes_left = temporary_bytes_in(output_dir)
    if temporary_files_left:
        status = STATUS_FAILED
        leftover_error = (
            f"atomic publish left {len(temporary_files_left)} temporary file(s) "
            f"({temporary_bytes_left} bytes)"
        )
        error = f"{error}; {leftover_error}" if error else leftover_error

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
        "temporary_files_left": len(temporary_files_left),
        "temporary_bytes_left": temporary_bytes_left,
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
        result["peak_rss_unavailable_reason"] = sampler.unavailable_reason

    if before is not None and after is not None:
        io_read_delta = after["io_read_bytes"] - before["io_read_bytes"]
        io_write_delta = after["io_write_bytes"] - before["io_write_bytes"]
        result["io_read_bytes_delta"] = io_read_delta
        result["io_write_bytes_delta"] = io_write_delta
        result["io_read_ops_delta"] = after["io_read_ops"] - before["io_read_ops"]
        result["io_write_ops_delta"] = after["io_write_ops"] - before["io_write_ops"]
    else:
        io_read_delta = None
        io_write_delta = None
        for key in (
            "io_read_bytes_delta",
            "io_write_bytes_delta",
            "io_read_ops_delta",
            "io_write_ops_delta",
        ):
            result[key] = None

    # Read/write amplification: measured process I/O counter deltas divided by
    # the logical input/output bytes.  OS-level counters (page-cache aware,
    # platform dependent); a measured ratio, not a logical read/write count.
    result["read_amplification"] = read_amplification(io_read_delta, input_bytes)
    result["write_amplification"] = write_amplification(io_write_delta, output_bytes)

    # temporary_bytes_written: logical size of the atomic .partial files at
    # publish time (measured by intercepting os.replace in this worker).
    if tracker.unavailable_reason is not None:
        result["temporary_bytes_written"] = None
        result["temporary_bytes_written_reason"] = tracker.unavailable_reason
    else:
        result["temporary_bytes_written"] = tracker.total_bytes
        result["temporary_publish_count"] = tracker.publish_count

    if error is not None:
        result["error"] = error
    return result
