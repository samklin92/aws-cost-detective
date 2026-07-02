"""
test_triage.py

triage.py's actual Claude call is the one thing in this codebase that's
intentionally NOT unit-tested here - that's an explanation-quality
concern, not a correctness concern, and costs real tokens to exercise.

What IS deterministic and worth locking in with tests:
  - _parse_json_response must handle raw JSON, ```json-fenced blocks, and
    bare ```-fenced blocks (Claude's output format isn't fully predictable
    across calls, so this parsing needs to be robust to fence variance).
  - triage_cost_report's payload construction: zero-value services are
    excluded from spend_by_service, and an anomaly with an infinite
    stddev (the zero-baseline case) is serialized as a readable string,
    not a literal 'Infinity' JSON token which isn't valid JSON.

The anthropic client is mocked throughout - no network calls, no API
key required, zero token cost. Safe to run on every push/PR.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")

from analyzer import Anomaly
from cost_fetcher import CostReport, DailyServiceCost
from triage import _parse_json_response, triage_cost_report


def test_parse_json_response_handles_raw_json():
    raw = '{"breakdown_summary": "test", "anomaly_explanations": [], "overall_summary": "quiet"}'
    result = _parse_json_response(raw)
    assert result["breakdown_summary"] == "test"


def test_parse_json_response_handles_json_fenced_block():
    raw = '```json\n{"breakdown_summary": "test", "anomaly_explanations": [], "overall_summary": "quiet"}\n```'
    result = _parse_json_response(raw)
    assert result["overall_summary"] == "quiet"


def test_parse_json_response_handles_bare_fenced_block():
    raw = '```\n{"breakdown_summary": "test", "anomaly_explanations": [], "overall_summary": "quiet"}\n```'
    result = _parse_json_response(raw)
    assert result["breakdown_summary"] == "test"


def _fake_anthropic_response(payload_json: str):
    fake_response = MagicMock()
    fake_block = MagicMock()
    fake_block.text = payload_json
    fake_response.content = [fake_block]
    return fake_response


@patch("triage.anthropic.Anthropic")
def test_triage_cost_report_excludes_zero_value_services_from_payload(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_anthropic_response(
        '{"breakdown_summary": "x", "anomaly_explanations": [], "overall_summary": "x"}'
    )
    mock_anthropic_cls.return_value = mock_client

    report = CostReport(start_date="2026-06-01", end_date="2026-06-03")
    report.daily_costs = [
        DailyServiceCost("2026-06-01", "Amazon EC2", 10.0, False),
        DailyServiceCost("2026-06-01", "Amazon S3", 0.0, False),  # zero-value, should be excluded
    ]

    triage_cost_report(report, anomalies=[])

    sent_payload = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Amazon EC2" in sent_payload
    assert "Amazon S3" not in sent_payload


@patch("triage.anthropic.Anthropic")
def test_triage_cost_report_serializes_infinite_stddev_as_readable_string(mock_anthropic_cls):
    """An Anomaly with stddevs_above_baseline == float('inf') (the zero-baseline
    new-spend case) must not be passed through as raw Infinity - that isn't
    valid JSON and would break json.dumps or downstream consumers."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_anthropic_response(
        '{"breakdown_summary": "x", "anomaly_explanations": [], "overall_summary": "x"}'
    )
    mock_anthropic_cls.return_value = mock_client

    report = CostReport(start_date="2026-06-01", end_date="2026-06-03")
    report.daily_costs = [DailyServiceCost("2026-06-01", "Amazon EC2", 12.0, False)]
    anomaly = Anomaly(
        date="2026-06-01",
        service="Amazon EC2",
        amount=12.0,
        baseline_average=0.0,
        baseline_stddev=0.0,
        stddevs_above_baseline=float("inf"),
    )

    triage_cost_report(report, anomalies=[anomaly])

    sent_payload = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Infinity" not in sent_payload
    assert "new spend (no prior baseline)" in sent_payload
