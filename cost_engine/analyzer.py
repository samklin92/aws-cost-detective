"""
analyzer.py

Deterministic anomaly detection over a CostReport. Two layers, applied in
order:

1. Minimum dollar floor - a service's cost on a given day must exceed a
   configurable absolute threshold before it's even considered for
   statistical analysis. This exists because percentage-based or pure
   standard-deviation comparisons break down at near-zero baselines: a
   jump from $0.0000000016 to $0.01 is mathematically a massive percentage
   increase but a financially meaningless event. Verified against this
   account's real data - most services sit at exactly $0 or fractions of
   a cent most days, and would constantly "anomaly" under a naive
   statistical-only approach.

2. Statistical deviation - for services that clear the dollar floor, flag
   a day where that service's cost is more than N standard deviations
   above the trailing average for that same service (not the account's
   total - different services have wildly different normal cost shapes,
   so comparing across services would be meaningless).

This logic is intentionally deterministic and rule-based, not delegated
to Claude - same separation-of-concerns principle as the drift detector's
differ.py. If an anomaly is wrongly flagged or missed, that's a code bug
to fix directly; explaining WHY a flagged anomaly might have happened is
the Claude layer's job (triage.py), not this one's.

Verified design issue, found via unit testing (not live data): a pure
stddev-based check is sensitive to artificially tight baselines. If a
service happens to be extremely stable for a few days (e.g. $5.0, $5.1,
$4.9, $5.0 - a tiny real-world wobble), its baseline stddev becomes very
small, so even a trivial absolute deviation (e.g. $5.20 instead of $5.00)
can compute as "many stddevs above baseline" despite being financially
meaningless. MIN_ABSOLUTE_DEVIATION requires the dollar gap above
baseline to also clear a real threshold, not just a statistical one -
this is the same dollar-floor principle as DEFAULT_DOLLAR_FLOOR, applied
to the deviation itself rather than the raw amount.
"""

import statistics
from dataclasses import dataclass

from cost_fetcher import CostReport

DEFAULT_DOLLAR_FLOOR = 1.00          # ignore any service-day below this, regardless of statistics
DEFAULT_STDDEV_THRESHOLD = 2.0        # flag if a day is this many stddevs above trailing average
MIN_DAYS_FOR_BASELINE = 3              # need at least this many prior days to compute a meaningful baseline
MIN_ABSOLUTE_DEVIATION = 1.00          # the dollar gap above baseline must also clear this, even if stddevs are high


@dataclass
class Anomaly:
    date: str
    service: str
    amount: float
    baseline_average: float
    baseline_stddev: float
    stddevs_above_baseline: float


def detect_anomalies(
    report: CostReport,
    dollar_floor: float = DEFAULT_DOLLAR_FLOOR,
    stddev_threshold: float = DEFAULT_STDDEV_THRESHOLD,
) -> list[Anomaly]:
    anomalies = []
    dates = report.all_dates()

    for service in report.all_services():
        # Build this service's full daily series across the report's date
        # range. A date with no entry for this service means its cost was
        # exactly zero that day (or absent), per the verified API behavior
        # that not every service appears on every day.
        series = []
        for date in dates:
            matching = [c for c in report.services_on(date) if c.service == service]
            amount = matching[0].amount if matching else 0.0
            series.append((date, amount))

        for i, (date, amount) in enumerate(series):
            if amount < dollar_floor:
                continue  # below the noise floor, never considered an anomaly regardless of statistics

            baseline_amounts = [a for _, a in series[:i]]
            if len(baseline_amounts) < MIN_DAYS_FOR_BASELINE:
                continue  # not enough prior history to judge this day fairly

            baseline_avg = statistics.mean(baseline_amounts)
            baseline_stddev = statistics.pstdev(baseline_amounts)

            if baseline_stddev == 0:
                # A perfectly flat baseline (e.g. always exactly $0 before
                # today) means ANY positive amount is a meaningful change,
                # not just a statistical artifact of dividing by zero.
                if amount > baseline_avg:
                    anomalies.append(
                        Anomaly(
                            date=date,
                            service=service,
                            amount=amount,
                            baseline_average=baseline_avg,
                            baseline_stddev=0.0,
                            stddevs_above_baseline=float("inf"),
                        )
                    )
                continue

            stddevs_above = (amount - baseline_avg) / baseline_stddev
            absolute_deviation = amount - baseline_avg
            if stddevs_above >= stddev_threshold and absolute_deviation >= MIN_ABSOLUTE_DEVIATION:
                anomalies.append(
                    Anomaly(
                        date=date,
                        service=service,
                        amount=amount,
                        baseline_average=baseline_avg,
                        baseline_stddev=baseline_stddev,
                        stddevs_above_baseline=stddevs_above,
                    )
                )

    return sorted(anomalies, key=lambda a: a.stddevs_above_baseline, reverse=True)


def summarize_by_service(report: CostReport) -> dict[str, float]:
    """Total cost per service across the entire report range - the deterministic basis for the narrated breakdown."""
    totals: dict[str, float] = {}
    for cost in report.daily_costs:
        totals[cost.service] = totals.get(cost.service, 0.0) + cost.amount
    return dict(sorted(totals.items(), key=lambda item: item[1], reverse=True))


if __name__ == "__main__":
    import sys

    from cost_fetcher import fetch_daily_costs_by_service

    start = sys.argv[1] if len(sys.argv) > 1 else "2026-06-21"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-28"

    report = fetch_daily_costs_by_service(start, end)

    print("=== Spend by service (total over range) ===")
    for service, total in summarize_by_service(report).items():
        if total > 0:
            print(f"  {service}: ${total:.6f}")

    print("\n=== Anomalies detected ===")
    anomalies = detect_anomalies(report)
    if not anomalies:
        print("  None - either no spend cleared the dollar floor, or nothing deviated enough from baseline.")
    for a in anomalies:
        print(
            f"  {a.date} | {a.service}: ${a.amount:.2f} "
            f"(baseline avg ${a.baseline_average:.2f}, {a.stddevs_above_baseline:.1f} stddevs above)"
        )
