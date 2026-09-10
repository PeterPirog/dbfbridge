"""Single source of truth for Direct Write regression ratio semantics
(DBFB-PERF-006, stage F3B1).

Both the policy generator (``calibrate_direct_write_regression``) and the
offline comparator (``compare_direct_write_regression``) consume THIS module's
definitions — there is no second operational formula table.

The five canonical ratio candidates have TWO mathematical forms:

- **per_record_ratio** (all four WALL ratios): the numerator's wall time is
  normalized by its OWN record count, the denominator's wall time by its
  record count, and the two normalized figures are divided:

  ``(numerator.wall_seconds / numerator.record_count) /``
  ``(denominator.wall_seconds / denominator.record_count)``

- **raw_ratio** (the W3/W1 peak-RSS-delta): the raw RSS-delta bytes are
  divided directly, with NO per-record normalization:

  ``numerator.peak_rss_delta_bytes / denominator.peak_rss_delta_bytes``

  The RSS delta is a retained-memory ratio, not a throughput figure —
  normalizing it by record count would misstate the memory relationship.

This module is ALSO the single mathematical authority for the v1 policy
derivation (``derive_ratio_statistics``) and the single strict parser for
the versioned runtime recipe (``parse_runtime_recipe``) — the generator
derives with it and the comparator re-derives with it, so a finite but
manually tampered derived statistic can never masquerade as policy.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

POLICY_CONTRACT = "dbfbridge-direct-write-regression-policy-v1"
POLICY_VERSION = 1

BENCHMARK_CONTRACT = "dbfbridge-direct-write-v1"
BENCHMARK_CONTRACT_VERSION = 1

CALIBRATION_CONTRACT = "dbfbridge-direct-write-calibration-v1"
CALIBRATION_CONTRACT_VERSION = 1

NORMALIZATION_PER_RECORD = "per_record_ratio"
NORMALIZATION_RAW = "raw_ratio"

#: The EXACT v1 policy engineering parameters (single authority; the
#: generator and the comparator both consume these values).
POLICY_PARAMETER_VALUES: dict[str, float] = {
    "mad_multiplier": 3.0,
    "small_sample_guard_band": 1.15,
    "hard_gate_discrimination_bound": 1.5,
}


def derive_ratio_statistics(
    values: Sequence[float],
    parameters: Mapping[str, float],
) -> dict[str, Any]:
    """THE single mathematical authority for v1 ratio derivation.

    Given a ratio ``values`` array and the v1 policy parameters, derives —
    with the exact rounding rules the policy uses — the complete derived
    specification:

    - ``center`` = median(values)                          (full precision)
    - ``mad`` = median(abs(value - center))                (full precision)
    - ``max_observed_deviation`` = max(abs(v - center))    (full precision)
    - ``spread_based`` = center + max(mad_multiplier*mad, max_dev)   (9 dp)
    - ``tail_based`` = max(values) * small_sample_guard_band         (9 dp)
    - ``envelope_upper`` = max(spread_based, tail_based)             (9 dp)
    - ``relative_mad`` = round(mad / center, 6) or None    (center > 0)
    - ``envelope_upper_over_center`` = round(envelope/center, 6) or None
    - ``classification`` = hard_gate iff
      envelope_upper <= center * hard_gate_discrimination_bound

    Raises ``ValueError`` for missing/non-finite inputs — callers must
    treat that as a fail-closed rejection.
    """
    finite = [float(value) for value in values]
    if not finite or any(not math.isfinite(value) for value in finite):
        raise ValueError("ratio values must be non-empty and finite")
    try:
        mad_multiplier = float(parameters["mad_multiplier"])
        guard_band = float(parameters["small_sample_guard_band"])
        discrimination_bound = float(
            parameters["hard_gate_discrimination_bound"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid policy parameters") from error
    center = statistics.median(finite)
    mad = statistics.median(abs(value - center) for value in finite)
    max_observed_deviation = max(abs(value - center) for value in finite)
    spread_based = center + max(mad_multiplier * mad, max_observed_deviation)
    tail_based = max(finite) * guard_band
    envelope_upper = max(spread_based, tail_based)
    classification = (
        "hard_gate"
        if center > 0 and envelope_upper <= center * discrimination_bound
        else "advisory_only"
    )
    return {
        "center": center,
        "mad": mad,
        "relative_mad": round(mad / center, 6) if center else None,
        "max_observed_deviation": max_observed_deviation,
        "spread_based": round(spread_based, 9),
        "tail_based": round(tail_based, 9),
        "envelope_upper": round(envelope_upper, 9),
        "envelope_upper_over_center": (
            round(envelope_upper / center, 6) if center else None
        ),
        "classification": classification,
    }


class RuntimeRecipe(NamedTuple):
    """Structurally parsed v1 policy runtime recipe."""

    runner_os: str
    runner_arch: str
    python_major_minor: str
    install_recipe: str
    dependencies: dict[str, str]


#: The EXACT dependency identity sequence of the v1 runtime recipe grammar.
RUNTIME_RECIPE_DEPENDENCY_NAMES = ("dbf", "dbfread", "psutil")

_PYTHON_COMPONENT_PATTERN = re.compile(r"^python-(\d+\.\d+)$")


def parse_runtime_recipe(recipe: Any) -> tuple[RuntimeRecipe | None, list[str]]:
    """THE single strict parser for the v1 policy runtime recipe.

    Grammar (exactly seven ``|``-separated components, none empty):

    ``runner_os | runner_arch | python-<major.minor> | install_recipe |``
    ``dbf=<version> | dbfread=<version> | psutil=<version>``

    Dependency components are positional and name-checked (wrong names,
    duplicates, missing dependencies and malformed ``key=value`` parts are
    rejected).  The parser fails closed and never leaks IndexError or
    ValueError: it returns ``(None, problems)`` for ANY malformed recipe.
    """
    if not isinstance(recipe, str) or not recipe.strip():
        return None, ["runtime_recipe must be a non-empty string"]
    parts = recipe.split("|")
    if len(parts) != 7:
        return None, [
            "runtime_recipe must have exactly 7 '|'-separated components "
            f"(got {len(parts)})"
        ]
    problems: list[str] = []
    runner_os, runner_arch, python_component, install_recipe = parts[:4]
    if not runner_os:
        problems.append("runtime_recipe runner_os component is empty")
    if not runner_arch:
        problems.append("runtime_recipe runner_arch component is empty")
    if _PYTHON_COMPONENT_PATTERN.match(python_component) is None:
        problems.append(
            "runtime_recipe python component must be python-<major.minor> "
            f"(got {python_component!r})"
        )
    if not install_recipe:
        problems.append("runtime_recipe install_recipe component is empty")
    dependencies: dict[str, str] = {}
    for expected_name, part in zip(
        RUNTIME_RECIPE_DEPENDENCY_NAMES, parts[4:], strict=True
    ):
        name, separator, version = part.partition("=")
        if not separator:
            problems.append(
                f"runtime_recipe dependency component {part!r} is not "
                f"key=value"
            )
            continue
        if name != expected_name:
            problems.append(
                f"runtime_recipe dependency component {part!r} must start "
                f"with {expected_name!r}="
            )
            continue
        if not version or "=" in version:
            problems.append(
                f"runtime_recipe dependency {name!r} has an empty or "
                f"malformed version"
            )
            continue
        dependencies[name] = version
    if problems or len(dependencies) != len(RUNTIME_RECIPE_DEPENDENCY_NAMES):
        if not problems:
            problems.append("runtime_recipe dependency components incomplete")
        return None, problems
    return (
        RuntimeRecipe(
            runner_os=runner_os,
            runner_arch=runner_arch,
            python_major_minor=python_component.removeprefix("python-"),
            install_recipe=install_recipe,
            dependencies=dependencies,
        ),
        [],
    )


class RatioDefinition(NamedTuple):
    label: str
    numerator_scenario: str
    denominator_scenario: str
    metric: str
    normalization: str


WALL_RATIOS: tuple[RatioDefinition, ...] = (
    RatioDefinition(
        label="W3/W1 wall-seconds-per-record",
        numerator_scenario="direct_write_1m_flat",
        denominator_scenario="direct_write_190k_flat",
        metric="wall_seconds",
        normalization=NORMALIZATION_PER_RECORD,
    ),
    RatioDefinition(
        label="W2/W1 wall-seconds-per-record",
        numerator_scenario="direct_read_transform_write_190k",
        denominator_scenario="direct_write_190k_flat",
        metric="wall_seconds",
        normalization=NORMALIZATION_PER_RECORD,
    ),
    RatioDefinition(
        label="W5/W1 wall-seconds-per-record",
        numerator_scenario="direct_write_memo_heavy",
        denominator_scenario="direct_write_190k_flat",
        metric="wall_seconds",
        normalization=NORMALIZATION_PER_RECORD,
    ),
    RatioDefinition(
        label="W10/W1 wall-seconds-per-record",
        numerator_scenario="direct_write_varchar_nullflags",
        denominator_scenario="direct_write_190k_flat",
        metric="wall_seconds",
        normalization=NORMALIZATION_PER_RECORD,
    ),
)

RSS_RATIO = RatioDefinition(
    label="W3/W1 peak-RSS-delta ratio",
    numerator_scenario="direct_write_1m_flat",
    denominator_scenario="direct_write_190k_flat",
    metric="peak_rss_delta_bytes",
    normalization=NORMALIZATION_RAW,
)

#: The EXACT five canonical ratio candidates (no hidden sixth).
RATIO_DEFINITIONS: tuple[RatioDefinition, ...] = WALL_RATIOS + (RSS_RATIO,)

RATIO_LABELS: tuple[str, ...] = tuple(item.label for item in RATIO_DEFINITIONS)

RATIO_DEFINITIONS_BY_LABEL: dict[str, RatioDefinition] = {
    item.label: item for item in RATIO_DEFINITIONS
}

#: Direct Write W1-W12 scenario identity order (contract of the benchmark).
SCENARIO_ORDER = (
    "direct_write_190k_flat",
    "direct_read_transform_write_190k",
    "direct_write_1m_flat",
    "direct_write_character_heavy",
    "direct_write_memo_heavy",
    "direct_write_deleted_include",
    "direct_write_cp1250",
    "direct_write_cp852",
    "direct_write_mazovia",
    "direct_write_varchar_nullflags",
    "overwrite_transaction_staging_cost",
    "cancellation_cleanup_smoke",
)

#: The W1-W11 MEASURED throughput scenarios (W12 is functional-only in both
#: modes and carries no per-record performance facts).
MEASURED_SCENARIO_ORDER = tuple(
    scenario for scenario in SCENARIO_ORDER if scenario != "cancellation_cleanup_smoke"
)

#: Canonical FULL-mode record counts (the benchmark contract's full workload
#: identity, DBFB-PERF-006).  These are the counts used to recompute the
#: per-record wall ratios from raw calibration wall-time facts; smoke runs
#: scale them down but never change their identity.
FULL_SCENARIO_RECORD_COUNTS = {
    "direct_write_190k_flat": 190_000,
    "direct_read_transform_write_190k": 190_000,
    "direct_write_1m_flat": 1_000_000,
    "direct_write_character_heavy": 100_000,
    "direct_write_memo_heavy": 100_000,
    "direct_write_deleted_include": 100_000,
    "direct_write_cp1250": 50_000,
    "direct_write_cp852": 50_000,
    "direct_write_mazovia": 50_000,
    "direct_write_varchar_nullflags": 100_000,
    "overwrite_transaction_staging_cost": 20_000,
}

#: Documented cross-check tolerance between stored 6-decimal serialized
#: calibration ratio facts and the mechanically recomputed full-precision
#: ratios.  The F3A collector serializes RSS delta ratios with
#: ``round(value, 6)``; a 6-decimal rounding can shift a value by up to
#: 5e-7, so 1e-6 covers exactly that serialization rounding — nothing else.
SERIALIZATION_ROUNDING_TOLERANCE = 1e-6


def evaluate_ratio_values(
    definition: RatioDefinition,
    numerator_rows: list[dict[str, Any]],
    denominator_rows: list[dict[str, Any]],
) -> list[float | None]:
    """Compute one ratio candidate's values from per-sample scenario rows.

    ``numerator_rows``/``denominator_rows`` are per-sample scenario dicts
    carrying the metric field and (for per-record normalization) the record
    count.  Returns ``None`` for samples whose facts are unavailable.
    """
    values: list[float | None] = []
    for numerator_row, denominator_row in zip(
        numerator_rows, denominator_rows, strict=True
    ):
        numerator_value = numerator_row.get(definition.metric)
        denominator_value = denominator_row.get(definition.metric)
        if (
            not isinstance(numerator_value, (int, float))
            or isinstance(numerator_value, bool)
            or not isinstance(denominator_value, (int, float))
            or isinstance(denominator_value, bool)
        ):
            values.append(None)
            continue
        numerator_number = float(numerator_value)
        denominator_number = float(denominator_value)
        if definition.normalization == NORMALIZATION_PER_RECORD:
            numerator_count = numerator_row.get("record_count")
            denominator_count = denominator_row.get("record_count")
            if (
                not isinstance(numerator_count, int)
                or isinstance(numerator_count, bool)
                or numerator_count <= 0
                or not isinstance(denominator_count, int)
                or isinstance(denominator_count, bool)
                or denominator_count <= 0
            ):
                values.append(None)
                continue
            values.append(
                (numerator_number / numerator_count)
                / (denominator_number / denominator_count)
            )
        else:  # raw_ratio
            if denominator_number <= 0:
                values.append(None)
                continue
            values.append(numerator_number / denominator_number)
    return values
