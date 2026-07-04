"""
test_lambda_handler.py

Not part of the main tests/ suite (this file lives in lambda/ and is
run manually / in a separate CI step if desired) - verifies the handler
wiring itself: secret-retrieval ordering, correct triage_cost_report
call signature, and Slack payload construction. Everything is mocked -
no real AWS, SSM, or Anthropic calls.

This exists specifically because the handler was written against
assumptions about triage_cost_report()'s signature that turned out to
be wrong on first read (see handler.py's docstring) - this test would
have caught that TypeError before it ever reached a real Lambda
invocation.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

os.environ["ANTHROPIC_API_KEY_SSM_PARAM"] = "/cost-detective/anthropic-api-key"
os.environ["SLACK_WEBHOOK_SSM_PARAM"] = "/cost-detective/slack-webhook-url"


@patch("handler.urlrequest.urlopen")
@patch("handler.triage_cost_report")
@patch("handler.detect_anomalies")
@patch("handler.fetch_daily_costs_by_service")
@patch("handler._get_ssm_client")
def test_lambda_handler_calls_triage_with_correct_signature(
    mock_ssm, mock_fetch, mock_detect, mock_triage, mock_urlopen
):
    """This is the test that would have caught the mode='slack' bug - it
    asserts triage_cost_report was called with exactly (report, anomalies),
    no extra keyword arguments."""
    import handler

    mock_ssm.return_value.get_parameter.side_effect = [
        {"Parameter": {"Value": "sk-ant-fake-key"}},
        {"Parameter": {"Value": "https://hooks.slack.com/fake"}},
    ]
    mock_fetch.return_value = MagicMock()
    mock_detect.return_value = []
    mock_triage.return_value = {
        "overall_summary": "Quiet account, no anomalies.",
        "breakdown_summary": "EC2 dominates spend.",
        "anomaly_explanations": [],
    }
    mock_urlopen.return_value.__enter__.return_value.status = 200

    result = handler.lambda_handler({}, None)

    mock_triage.assert_called_once()
    call_args = mock_triage.call_args
    assert len(call_args.args) == 2  # exactly (report, anomalies) - no mode kwarg
    assert "mode" not in call_args.kwargs

    assert result["statusCode"] == 200


@patch("handler.urlrequest.urlopen")
@patch("handler.triage_cost_report")
@patch("handler.detect_anomalies")
@patch("handler.fetch_daily_costs_by_service")
@patch("handler._get_ssm_client")
def test_secrets_are_resolved_before_triage_is_called(
    mock_ssm, mock_fetch, mock_detect, mock_triage, mock_urlopen
):
    """Guards against the ordering bug caught in review: ANTHROPIC_API_KEY
    must be set in os.environ BEFORE triage_cost_report runs, since that
    function reads it internally via os.environ["ANTHROPIC_API_KEY"]."""
    import handler

    call_order = []
    mock_ssm.return_value.get_parameter.side_effect = lambda **kw: (
        call_order.append("ssm_get_parameter"),
        {"Parameter": {"Value": "https://hooks.slack.com/fake"}},
    )[1]

    def fake_triage(*a, **kw):
        call_order.append("triage_cost_report")
        assert "ANTHROPIC_API_KEY" in os.environ, "ANTHROPIC_API_KEY must be set before triage runs"
        return {"overall_summary": "x", "breakdown_summary": "x", "anomaly_explanations": []}

    mock_triage.side_effect = fake_triage
    mock_fetch.return_value = MagicMock()
    mock_detect.return_value = []
    mock_urlopen.return_value.__enter__.return_value.status = 200

    handler.lambda_handler({}, None)

    assert call_order.index("ssm_get_parameter") < call_order.index("triage_cost_report")


@patch("handler.urlrequest.urlopen")
@patch("handler.triage_cost_report")
@patch("handler.detect_anomalies")
@patch("handler.fetch_daily_costs_by_service")
@patch("handler._get_ssm_client")
def test_slack_message_includes_overall_summary(
    mock_ssm, mock_fetch, mock_detect, mock_triage, mock_urlopen
):
    import handler

    mock_ssm.return_value.get_parameter.return_value = {"Parameter": {"Value": "https://hooks.slack.com/fake"}}
    mock_fetch.return_value = MagicMock()
    mock_detect.return_value = []
    mock_triage.return_value = {
        "overall_summary": "UNIQUE_MARKER_TEXT",
        "breakdown_summary": "breakdown here",
        "anomaly_explanations": [],
    }
    mock_urlopen.return_value.__enter__.return_value.status = 200

    handler.lambda_handler({}, None)

    sent_request = mock_urlopen.call_args.args[0]
    sent_body = sent_request.data.decode("utf-8")
    assert "UNIQUE_MARKER_TEXT" in sent_body
