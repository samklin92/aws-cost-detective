"""
handler.py

Two independent trigger paths share this one Lambda function:

1. SNS-triggered (existing): fires when an AWS Budget crosses a threshold
   (80% actual or 100% forecasted, per terraform/main.tf). Runs the same
   fetch -> analyze -> triage pipeline as main.py, then formats the plain
   engineer-facing triage_cost_report() output as a Slack message and posts
   it directly - this is the "reactive, not just scheduled" path the CLI
   tool doesn't cover on its own.

2. EventBridge-triggered (new): fires on a schedule (terraform/cross_account_scan.tf,
   default daily) and runs the orphaned-resource scan against the workload
   account from multi-account-observability, via a cross-account assumed
   role. This exists because trend-based anomaly detection (path 1, via
   analyzer.py) is keyed on CHANGE - a resource sitting at a flat, unmoving
   cost every day never triggers it, no matter how expensive. This second
   path catches that blind spot specifically.

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
cost-anomaly question, not a tag-compliance question. Keeping the SNS path
scoped to one concern per the project's separation-of-concerns principle.
The orphaned-resource path is a third, distinct concern (static inventory,
not anomaly, not tags) - kept in its own branch rather than folded into
either existing pipeline.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest

import boto3

from analyzer import detect_anomalies
from cost_fetcher import fetch_daily_costs_by_service
from orphaned_resource_scanner import scan_all_orphaned_resources
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


def _is_scheduled_event(event: dict) -> bool:
    """EventBridge scheduled rules deliver a distinct shape from SNS -
    'source': 'aws.events' is the reliable discriminator, present on every
    EventBridge-originated invocation regardless of rule configuration."""
    return event.get("source") == "aws.events"


def _assume_cross_account_session(role_arn: str, external_id: str, region: str) -> boto3.Session:
    """Assumes the cross-account role from multi-account-observability and
    returns a Session scoped to those temporary credentials - callers get
    ec2/elbv2 clients from this session, never from the Lambda's own
    (single-account) execution role."""
    sts_client = boto3.client("sts")
    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName="cost-detective-orphaned-scan",
        ExternalId=external_id,
    )
    creds = response["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


def _run_orphaned_resource_scan(region: str) -> dict:
    workload_account_id = os.environ["WORKLOAD_ACCOUNT_ID"]
    role_name = os.environ["CROSS_ACCOUNT_ROLE_NAME"]
    external_id_param = os.environ["CROSS_ACCOUNT_EXTERNAL_ID_SSM_PARAM"]
    slack_webhook_param = os.environ["SLACK_WEBHOOK_SSM_PARAM"]

    external_id = _get_ssm_secret(external_id_param)
    webhook_url = _get_ssm_secret(slack_webhook_param)

    role_arn = f"arn:aws:iam::{workload_account_id}:role/{role_name}"
    session = _assume_cross_account_session(role_arn, external_id, region)

    ec2_client = session.client("ec2")
    elbv2_client = session.client("elbv2")

    findings = scan_all_orphaned_resources(ec2_client, elbv2_client)

    print(f"Orphaned-resource scan complete. {len(findings)} finding(s) in account {workload_account_id}.")

    if findings:
        lines = "\n".join(f"- *{f.resource_type}* `{f.resource_id}` - {f.detail}" for f in findings)
        slack_text = (
            f":mag: *Orphaned Resource Scan - account {workload_account_id}*\n\n"
            f"{len(findings)} steady-state cost item(s) found (not flagged by anomaly "
            f"detection since their cost doesn't change day to day):\n\n{lines}"
        )
        _post_to_slack(webhook_url, slack_text)
        print("Posted findings to Slack.")
    else:
        print("No orphaned resources found - skipping Slack post.")

    return {
        "statusCode": 200,
        "body": json.dumps({"findings_count": len(findings), "posted_to_slack": bool(findings)}),
    }


def _run_budget_alert_analysis() -> dict:
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


def lambda_handler(event, context):
    region = os.environ.get("AWS_REGION", "us-east-1")

    if _is_scheduled_event(event):
        return _run_orphaned_resource_scan(region)

    return _run_budget_alert_analysis()
