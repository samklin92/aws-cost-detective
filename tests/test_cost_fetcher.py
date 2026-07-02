"""
test_cost_fetcher.py

Two layers of coverage:

1. CostReport's own query methods (total_for_date, services_on, all_dates,
   all_services) - pure logic, no AWS involved.
2. fetch_daily_costs_by_service against a MOCKED boto3 client shaped exactly
   like the verified real Cost Explorer response documented in this file's
   own docstring. This locks in the three quirks that were confirmed
   against live data and would silently break if boto3's response shape
   assumptions ever drift:
     - Amount is a string and must be converted to float
     - Zero-cost services are NOT filtered by the fetcher (caller's job)
     - The service set is not consistent day-to-day

No live AWS calls are made anywhere in this file - zero Cost Explorer
API cost, safe to run on every push/PR.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

from cost_fetcher import CostReport, DailyServiceCost, fetch_daily_costs_by_service


def test_total_for_date_sums_only_matching_date():
    report = CostReport(start_date="2026-06-01", end_date="2026-06-03")
    report.daily_costs = [
        DailyServiceCost("2026-06-01", "Amazon EC2", 10.0, False),
        DailyServiceCost("2026-06-01", "Amazon S3", 5.0, False),
        DailyServiceCost("2026-06-02", "Amazon EC2", 99.0, False),
    ]
    assert report.total_for_date("2026-06-01") == 15.0


def test_services_on_returns_only_matching_date_entries():
    report = CostReport(start_date="2026-06-01", end_date="2026-06-03")
    report.daily_costs = [
        DailyServiceCost("2026-06-01", "Amazon EC2", 10.0, False),
        DailyServiceCost("2026-06-02", "Amazon S3", 5.0, False),
    ]
    result = report.services_on("2026-06-01")
    assert len(result) == 1
    assert result[0].service == "Amazon EC2"


def test_all_dates_and_all_services_are_sorted_and_deduplicated():
    report = CostReport(start_date="2026-06-01", end_date="2026-06-03")
    report.daily_costs = [
        DailyServiceCost("2026-06-02", "Amazon S3", 5.0, False),
        DailyServiceCost("2026-06-01", "Amazon EC2", 10.0, False),
        DailyServiceCost("2026-06-01", "Amazon S3", 1.0, False),  # duplicate date+service pairing pattern
    ]
    assert report.all_dates() == ["2026-06-01", "2026-06-02"]
    assert report.all_services() == ["Amazon EC2", "Amazon S3"]


def _mock_ce_response():
    """Shaped exactly like the verified real Cost Explorer response documented
    in cost_fetcher.py's module docstring: Amount as a string, a zero-cost
    service present in Groups, and an inconsistent service set across days."""
    return {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-06-21", "End": "2026-06-22"},
                "Estimated": True,
                "Groups": [
                    {"Keys": ["Amazon EC2"], "Metrics": {"UnblendedCost": {"Amount": "12.345678", "Unit": "USD"}}},
                    {"Keys": ["Amazon S3"], "Metrics": {"UnblendedCost": {"Amount": "0.0000000016", "Unit": "USD"}}},
                ],
            },
            {
                "TimePeriod": {"Start": "2026-06-22", "End": "2026-06-23"},
                "Estimated": False,
                "Groups": [
                    {"Keys": ["Amazon EC2"], "Metrics": {"UnblendedCost": {"Amount": "13.000000", "Unit": "USD"}}},
                    # Amazon S3 absent this day - service set is not consistent day-to-day
                    {"Keys": ["AWS Lambda"], "Metrics": {"UnblendedCost": {"Amount": "0.500000", "Unit": "USD"}}},
                ],
            },
        ]
    }


@patch("cost_fetcher.boto3")
def test_amount_string_is_converted_to_float(mock_boto3):
    mock_client = MagicMock()
    mock_client.get_cost_and_usage.return_value = _mock_ce_response()
    mock_boto3.client.return_value = mock_client

    report = fetch_daily_costs_by_service("2026-06-21", "2026-06-23")

    ec2_day1 = [c for c in report.services_on("2026-06-21") if c.service == "Amazon EC2"][0]
    assert isinstance(ec2_day1.amount, float)
    assert ec2_day1.amount == 12.345678


@patch("cost_fetcher.boto3")
def test_zero_and_near_zero_cost_services_are_not_filtered_by_fetcher(mock_boto3):
    """Per the verified API behavior, the fetcher passes zero/near-zero
    entries through unfiltered - filtering is explicitly the analyzer's
    job (via DEFAULT_DOLLAR_FLOOR), not the fetcher's."""
    mock_client = MagicMock()
    mock_client.get_cost_and_usage.return_value = _mock_ce_response()
    mock_boto3.client.return_value = mock_client

    report = fetch_daily_costs_by_service("2026-06-21", "2026-06-23")

    s3_entry = [c for c in report.services_on("2026-06-21") if c.service == "Amazon S3"][0]
    assert s3_entry.amount == 0.0000000016  # present, not dropped


@patch("cost_fetcher.boto3")
def test_service_set_is_not_assumed_consistent_across_days(mock_boto3):
    mock_client = MagicMock()
    mock_client.get_cost_and_usage.return_value = _mock_ce_response()
    mock_boto3.client.return_value = mock_client

    report = fetch_daily_costs_by_service("2026-06-21", "2026-06-23")

    day1_services = {c.service for c in report.services_on("2026-06-21")}
    day2_services = {c.service for c in report.services_on("2026-06-22")}
    assert day1_services == {"Amazon EC2", "Amazon S3"}
    assert day2_services == {"Amazon EC2", "AWS Lambda"}
    assert day1_services != day2_services


@patch("cost_fetcher.boto3")
def test_estimated_flag_is_captured_per_period(mock_boto3):
    mock_client = MagicMock()
    mock_client.get_cost_and_usage.return_value = _mock_ce_response()
    mock_boto3.client.return_value = mock_client

    report = fetch_daily_costs_by_service("2026-06-21", "2026-06-23")

    day1_entry = report.services_on("2026-06-21")[0]
    day2_entry = report.services_on("2026-06-22")[0]
    assert day1_entry.estimated is True
    assert day2_entry.estimated is False


@patch("cost_fetcher.boto3")
def test_calls_cost_explorer_exactly_once_for_a_single_page_response(mock_boto3):
    """Regression guard for the documented cost model: a typical single-page
    query must issue exactly one get_cost_and_usage call ($0.01), not more."""
    mock_client = MagicMock()
    mock_client.get_cost_and_usage.return_value = _mock_ce_response()
    mock_boto3.client.return_value = mock_client

    fetch_daily_costs_by_service("2026-06-21", "2026-06-23")

    assert mock_client.get_cost_and_usage.call_count == 1
