"""
scripts/smoke_test_live.py

Runs the full pipeline against REAL AWS Cost Explorer and REAL Anthropic
API. This is deliberately NOT a pytest file and lives outside tests/ so
it can never run accidentally in the free test-tools job.

Cost per run: ~$0.01 (one Cost Explorer call) + Anthropic token cost for
one triage call. Intended to run once per push to main, not per commit
in a PR - see the CI job's `if:` condition.

Exit code 0 = pipeline ran end-to-end without crashing and produced a
report with at least one day of data. This does NOT validate the
QUALITY of Claude's narration - that's a human judgment call, not
something to gate merges on.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

from analyzer import detect_anomalies
from cost_fetcher import fetch_daily_costs_by_service
from triage import triage_cost_report


def main() -> int:
    end = datetime.utcnow().date()
    start = end - timedelta(days=7)

    print(f"Fetching real Cost Explorer data: {start} to {end}")
    report = fetch_daily_costs_by_service(str(start), str(end))

    if not report.all_dates():
        print("FAIL: report has zero days of data - unexpected for any active AWS account")
        return 1
    print(f"OK: fetched {len(report.all_dates())} days, {len(report.all_services())} distinct services")

    anomalies = detect_anomalies(report)
    print(f"OK: anomaly detection ran cleanly, {len(anomalies)} anomalies found")

    print("Calling Claude for triage narration...")
    result = triage_cost_report(report, anomalies)

    required_keys = {"breakdown_summary", "anomaly_explanations", "overall_summary"}
    missing = required_keys - result.keys()
    if missing:
        print(f"FAIL: triage response missing expected keys: {missing}")
        return 1

    print("OK: triage response has all expected keys")
    print(f"\nOverall summary: {result['overall_summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
