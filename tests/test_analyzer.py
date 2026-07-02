"""
test_analyzer.py

Locks in the deterministic anomaly detection logic as regression tests,
specifically targeting the two documented, verified failure modes:

1. Tight-baseline stddev false positive (why MIN_ABSOLUTE_DEVIATION exists)
2. Near-zero-baseline noise (why DEFAULT_DOLLAR_FLOOR exists)

Plus the zero-stddev edge case, insufficient-history guard, and the
service-level (not account-level) baseline comparison.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

from analyzer import detect_anomalies, summarize_by_service


def test_no_anomaly_below_dollar_floor(report_factory):
    """A service that never exceeds DEFAULT_DOLLAR_FLOOR ($1.00) is never
    flagged, no matter how large the percentage swing - this is the
    near-zero-account noise case from the project's own README."""
    report = report_factory(
        "2026-06-01", "2026-06-08",
        [
            ("2026-06-01", "AWS Lambda", 0.0000000016),
            ("2026-06-02", "AWS Lambda", 0.0000000016),
            ("2026-06-03", "AWS Lambda", 0.0000000016),
            ("2026-06-04", "AWS Lambda", 0.0000000016),
            ("2026-06-05", "AWS Lambda", 0.01),  # ~625,000,000% increase, still under $1 floor
        ],
    )
    anomalies = detect_anomalies(report)
    assert anomalies == []


def test_tight_baseline_does_not_false_positive(report_factory):
    """A stable service with tiny real-world wobble in its baseline ($5.00,
    $5.10, $4.90 - non-identical, so this hits the stddev branch, not the
    zero-baseline branch) followed by a trivial $0.20 deviation must NOT be
    flagged, even though the stddev ratio alone would call it 'many stddevs
    above baseline'. This is the exact bug documented in the session notes
    (Bug #10) - MIN_ABSOLUTE_DEVIATION is the fix."""
    report = report_factory(
        "2026-06-01", "2026-06-08",
        [
            ("2026-06-01", "Amazon EC2", 5.00),
            ("2026-06-02", "Amazon EC2", 5.10),
            ("2026-06-03", "Amazon EC2", 4.90),
            ("2026-06-04", "Amazon EC2", 5.20),  # trivial wobble, high stddev ratio, low absolute deviation
        ],
    )
    anomalies = detect_anomalies(report)
    assert anomalies == []


def test_real_spike_is_flagged(report_factory):
    """A genuine spike - large in both stddev terms AND absolute dollars -
    must be flagged. This is the positive case the two guards must not
    accidentally suppress."""
    report = report_factory(
        "2026-06-01", "2026-06-08",
        [
            ("2026-06-01", "Amazon EC2", 5.00),
            ("2026-06-02", "Amazon EC2", 5.00),
            ("2026-06-03", "Amazon EC2", 5.00),
            ("2026-06-04", "Amazon EC2", 45.00),  # real spike: 9x baseline, $40 absolute jump
        ],
    )
    anomalies = detect_anomalies(report)
    assert len(anomalies) == 1
    assert anomalies[0].service == "Amazon EC2"
    assert anomalies[0].date == "2026-06-04"
    assert anomalies[0].amount == 45.00


def test_zero_baseline_flags_any_new_spend(report_factory):
    """A service with a perfectly flat $0 baseline (never appeared before)
    that suddenly shows real spend must be flagged - this is the
    baseline_stddev == 0 branch, which exists specifically to avoid a
    division-by-zero while still catching genuinely new spend."""
    report = report_factory(
        "2026-06-01", "2026-06-08",
        [
            ("2026-06-01", "Amazon EC2", 0.0),
            ("2026-06-02", "Amazon EC2", 0.0),
            ("2026-06-03", "Amazon EC2", 0.0),
            ("2026-06-04", "Amazon EC2", 12.00),  # new spend, no prior baseline
        ],
    )
    anomalies = detect_anomalies(report)
    assert len(anomalies) == 1
    assert anomalies[0].baseline_stddev == 0.0
    assert anomalies[0].stddevs_above_baseline == float("inf")


def test_insufficient_history_is_not_flagged(report_factory):
    """Fewer than MIN_DAYS_FOR_BASELINE (3) prior days means there isn't
    enough history to judge fairly - must not be flagged regardless of
    how large the swing looks."""
    report = report_factory(
        "2026-06-01", "2026-06-08",
        [
            ("2026-06-01", "Amazon EC2", 5.00),
            ("2026-06-02", "Amazon EC2", 50.00),  # only 1 prior day - not enough baseline
        ],
    )
    anomalies = detect_anomalies(report)
    assert anomalies == []


def test_baseline_is_per_service_not_account_wide(report_factory):
    """Two services with wildly different normal cost shapes must be
    compared against their own baselines, not each other's or the
    account total - comparing across services would be meaningless per
    the module's own design docstring."""
    report = report_factory(
        "2026-06-01", "2026-06-08",
        [
            ("2026-06-01", "Amazon S3", 2.00),
            ("2026-06-02", "Amazon S3", 2.10),
            ("2026-06-03", "Amazon S3", 1.90),
            ("2026-06-04", "Amazon S3", 2.15),  # trivial S3 wobble
            ("2026-06-01", "Amazon EC2", 100.00),
            ("2026-06-02", "Amazon EC2", 105.00),
            ("2026-06-03", "Amazon EC2", 95.00),
            ("2026-06-04", "Amazon EC2", 101.00),
        ],
    )
    anomalies = detect_anomalies(report)
    assert anomalies == []  # neither service's baseline is disturbed by the other's scale


def test_summarize_by_service_totals_and_sorts_descending(report_factory):
    report = report_factory(
        "2026-06-01", "2026-06-03",
        [
            ("2026-06-01", "Amazon EC2", 10.00),
            ("2026-06-02", "Amazon EC2", 15.00),
            ("2026-06-01", "Amazon S3", 40.00),
        ],
    )
    totals = summarize_by_service(report)
    assert totals == {"Amazon S3": 40.00, "Amazon EC2": 25.00}
    assert list(totals.keys())[0] == "Amazon S3"  # highest spend first
