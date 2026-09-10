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
"""

from __future__ import annotations

from typing import Any, NamedTuple

POLICY_CONTRACT = "dbfbridge-direct-write-regression-policy-v1"
POLICY_VERSION = 1

BENCHMARK_CONTRACT = "dbfbridge-direct-write-v1"
BENCHMARK_CONTRACT_VERSION = 1

CALIBRATION_CONTRACT = "dbfbridge-direct-write-calibration-v1"
CALIBRATION_CONTRACT_VERSION = 1

NORMALIZATION_PER_RECORD = "per_record_ratio"
NORMALIZATION_RAW = "raw_ratio"


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
