"""
main.py

CLI orchestrator: fetch real Cost Explorer data, run deterministic
anomaly detection, narrate the result through Claude, print a report.

Usage:
    python cost_engine/main.py --start 2026-06-21 --end 2026-06-28

Cost note: each run makes exactly one Cost Explorer API call ($0.01)
plus one Claude API call (Anthropic token cost, no fixed per-call fee).
Re-running this command repeatedly for the same date range re-incurs the
$0.01 Cost Explorer charge each time - there is no local caching layer
in this version.
"""

import argparse
import os
import sys


def run(start_date: str, end_date: str) -> dict:
    from analyzer import detect_anomalies
    from cost_fetcher import fetch_daily_costs_by_service
    from triage import triage_cost_report

    report = fetch_daily_costs_by_service(start_date, end_date)
    anomalies = detect_anomalies(report)
    return triage_cost_report(report, anomalies)


def print_report(result: dict) -> None:
    print("\n=== AWS Cost Report ===\n")
    print(result["breakdown_summary"])
    print()

    if not result["anomaly_explanations"]:
        print("No anomalies detected.\n")
    else:
        print("--- Anomalies ---\n")
        for a in result["anomaly_explanations"]:
            print(f"  {a['date']} | {a['service']}")
            print(f"    {a['explanation']}")
            print(f"    Likely cause: {a['likely_cause']}")
            print(f"    Recommended action: {a['recommended_action']}\n")

    print("--- Summary ---")
    print(result["overall_summary"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI-augmented AWS cost anomaly detector")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD (exclusive)")
    args = parser.parse_args()

    if "ANTHROPIC_API_KEY" not in os.environ:
        from dotenv import load_dotenv
        load_dotenv()

    if "ANTHROPIC_API_KEY" not in os.environ:
        print("ERROR: ANTHROPIC_API_KEY not set (checked .env too).", file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    result = run(args.start, args.end)
    print_report(result)
