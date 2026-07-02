"""
conftest.py

Shared fixtures for the deterministic test suite. Builds CostReport/
DailyServiceCost objects directly - no boto3, no network, no cost. This
matches the project's own separation-of-concerns principle: analyzer.py's
logic is pure and testable in isolation from the AWS-fetching layer.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

import pytest

from cost_fetcher import CostReport, DailyServiceCost


def build_report(start_date: str, end_date: str, entries: list[tuple[str, str, float]]) -> CostReport:
    """
    entries: list of (date, service, amount) tuples. estimated defaults to False -
    not relevant to analyzer.py's logic, which never reads that field.
    """
    report = CostReport(start_date=start_date, end_date=end_date)
    for date, service, amount in entries:
        report.daily_costs.append(
            DailyServiceCost(date=date, service=service, amount=amount, estimated=False)
        )
    return report


@pytest.fixture
def report_factory():
    """Returns the build_report helper so tests can construct reports inline."""
    return build_report
