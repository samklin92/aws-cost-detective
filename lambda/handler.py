"""
handler.py

Triggered by an SNS notification when an AWS Budget crosses a threshold
(80% actual or 100% forecasted, per terraform/main.tf). Runs the same
fetch -> analyze -> triage pipeline as main.py, then formats the plain
engineer-facing triage_cost_report() output as a Slack message and posts
it directly - this is the "reactive, not just scheduled" path the CLI
tool doesn't cover on its own.

Note: triage_cost_report() returns one JSON shape (breakdown_summary,
anomaly_explanations, overall_summary) - there is no separate Slack mode
in this project's triage.py (unlike the Terraform Drift Detector's
triage.py, which does have engineer/Slack modes - do not assume the two
projects share an API just because they're structurally similar).

Secrets are pulled from SSM Parameter Store at invocation time, not
baked into the deployment package or set as plaintext Lambda environment
variables - only the SSM *paths* are environment variables, matching the
project's existing pattern of never hardcoding credentials (see: the PAT
scrub in Notion, the External Secrets Operator usage elsewhere).

Deliberately does NOT re-run tag_auditor.py here - a budget breach is a
cost-anomaly question, not a tag-compliance question. Keeping this
handler scoped to one concern per the project's separation-of-concerns
principle.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest

import boto3

from analyzer import detect_anomalies
from cost_fetcher import fetch_daily_costs_by_service
from triage import triage_cost_report

_ssm_client = None


def _get_ssm_client():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm")
    return _ssm_client


def _get_ssm_secret(param_name: str) -> str:
    response = _get_ssm_client().get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]


def _post_to_slack(webhook_url: str, text: str) -> None:
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urlrequest.Request(
        webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urlrequest.urlopen(req, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"Slack webhook returned HTTP {response.status}")


def lambda_handler(event, context):
    # Budget breach doesn't tell us WHICH days to inspect - look at the
    # trailing 7 days, same window the CLI and the CI smoke test use, so
    # behavior stays consistent across all three entry points.
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=7)

    print(f"Budget alert received. Analyzing {start} to {end}.")

    # Secrets must be resolved BEFORE triage_cost_report runs - it reads
    # ANTHROPIC_API_KEY from the process environment internally, so
    # setting it after calling triage_cost_report would fail with a
    # KeyError. Caught this ordering bug in review before it ever ran.
    anthropic_key_param = os.environ["ANTHROPIC_API_KEY_SSM_PARAM"]
    slack_webhook_param = os.environ["SLACK_WEBHOOK_SSM_PARAM"]
    os.environ["ANTHROPIC_API_KEY"] = _get_ssm_secret(anthropic_key_param)
    webhook_url = _get_ssm_secret(slack_webhook_param)

    report = fetch_daily_costs_by_service(str(start), str(end))
    anomalies = detect_anomalies(report)
    triage_result = triage_cost_report(report, anomalies)

    slack_text = (
        f":rotating_light: *AWS Budget Alert - Cost Detective Analysis*\n\n"
        f"{triage_result.get('overall_summary', 'No summary available.')}\n\n"
        f"{triage_result.get('breakdown_summary', '')}"
    )

    _post_to_slack(webhook_url, slack_text)
    print("Posted analysis to Slack.")

    return {
        "statusCode": 200,
        "body": json.dumps({"anomalies_found": len(anomalies), "posted_to_slack": True}),
    }
