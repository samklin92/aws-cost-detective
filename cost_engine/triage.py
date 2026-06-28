"""
triage.py

Claude-powered narration layer over the deterministic outputs of
analyzer.py. Two jobs, both narration/explanation, never detection:

1. Narrate the spend breakdown in plain English - which services dominate
   spend, whether the pattern looks expected for the account's apparent
   workload.
2. Explain detected anomalies - plausible cause, severity framing, and a
   recommended next step.

Same separation-of-concerns principle as the drift detector's triage.py:
analyzer.py decides WHAT is anomalous (deterministic, rule-based, already
unit-tested). Claude explains WHY it might have happened and WHAT TO DO -
if an anomaly is mis-detected, that's a bug in analyzer.py; if Claude's
explanation is unclear or unhelpful, that's a prompt issue here.

Cost note: this module makes Claude API calls, not Cost Explorer calls -
no per-request AWS billing impact, only standard Anthropic token cost.
"""

import json
import os

import anthropic
from dotenv import load_dotenv

from analyzer import Anomaly, summarize_by_service
from cost_fetcher import CostReport

load_dotenv()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a FinOps assistant explaining AWS cost data to a DevOps engineer.

You will receive:
1. A spend breakdown by service for a date range
2. A list of detected cost anomalies (if any) - already identified by deterministic
   statistical analysis, not by you. Your job is to explain them, not to second-guess
   whether they are real anomalies.

For the breakdown: write 2-3 sentences describing where spend concentrates and whether
that looks like a normal pattern for typical cloud usage (compute-heavy, storage-heavy,
data-transfer-heavy, etc.) - be specific about which service(s) drive the total, not
generic.

For each anomaly: state the likely cause as a hypothesis to verify, not a confirmed fact -
you do not have access to CloudTrail or deployment history, only the cost numbers
themselves. Recommend one concrete next step (e.g. "check CloudTrail for what created
this resource", "review if a forgotten dev instance is still running").

If there are no anomalies, say so plainly - do not invent a sense of urgency or imply
something is wrong when the data shows a quiet, stable account.

Respond ONLY with JSON in this exact shape, no markdown fences, no preamble:
{
  "breakdown_summary": "...",
  "anomaly_explanations": [
    {"date": "...", "service": "...", "explanation": "...", "likely_cause": "...", "recommended_action": "..."}
  ],
  "overall_summary": "..."
}
"""


def triage_cost_report(report: CostReport, anomalies: list[Anomaly]) -> dict:
    breakdown = summarize_by_service(report)
    payload = {
        "date_range": f"{report.start_date} to {report.end_date}",
        "spend_by_service": {k: round(v, 4) for k, v in breakdown.items() if v > 0},
        "anomalies": [
            {
                "date": a.date,
                "service": a.service,
                "amount": round(a.amount, 2),
                "baseline_average": round(a.baseline_average, 2),
                "stddevs_above_baseline": (
                    round(a.stddevs_above_baseline, 1)
                    if a.stddevs_above_baseline != float("inf")
                    else "new spend (no prior baseline)"
                ),
            }
            for a in anomalies
        ],
    }

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )

    return _parse_json_response(response.content[0].text)


def _parse_json_response(raw_text: str) -> dict:
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


if __name__ == "__main__":
    import sys

    from analyzer import detect_anomalies
    from cost_fetcher import fetch_daily_costs_by_service

    start = sys.argv[1] if len(sys.argv) > 1 else "2026-06-21"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-28"

    report = fetch_daily_costs_by_service(start, end)
    anomalies = detect_anomalies(report)

    result = triage_cost_report(report, anomalies)
    print(json.dumps(result, indent=2))
