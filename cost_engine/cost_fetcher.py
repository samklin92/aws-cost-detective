"""
cost_fetcher.py

Fetches daily cost-by-service data from AWS Cost Explorer, normalized into
a clean structure for the analyzer.

Cost note: every get_cost_and_usage call costs $0.01 against the primary
billing view, and paginated results multiply that cost per page (verified
against AWS's own pricing documentation - see project README). This
fetcher is deliberately built to request a single, bounded date range per
call rather than encouraging exploratory/repeated querying.

Verified API shape (confirmed against a real 7-day query before writing
this code):
- ResultsByTime is a list, one entry per day (for DAILY granularity)
- Groups[].Keys is a list (one entry per GroupBy dimension requested)
- Groups[].Metrics.UnblendedCost.Amount is a STRING, not a float -
  requires explicit conversion
- Services with zero cost still appear in Groups - the caller must
  filter zeros deliberately, they are not absent automatically
- The set of services present is NOT consistent day-to-day - a service
  with real cost on one day may not appear at all on another day if its
  cost was exactly zero that day. Code must not assume a fixed service
  list across the date range.
- Every period in early results was marked "Estimated": true - Cost
  Explorer has a documented ~24 hour data lag for the current month,
  so very recent days' figures are not final.
"""

from dataclasses import dataclass, field

import boto3


@dataclass
class DailyServiceCost:
    date: str            # YYYY-MM-DD, the period start date
    service: str
    amount: float
    estimated: bool


@dataclass
class CostReport:
    start_date: str
    end_date: str
    daily_costs: list[DailyServiceCost] = field(default_factory=list)

    def total_for_date(self, date: str) -> float:
        return sum(c.amount for c in self.daily_costs if c.date == date)

    def services_on(self, date: str) -> list[DailyServiceCost]:
        return [c for c in self.daily_costs if c.date == date]

    def all_dates(self) -> list[str]:
        return sorted({c.date for c in self.daily_costs})

    def all_services(self) -> list[str]:
        return sorted({c.service for c in self.daily_costs})


def fetch_daily_costs_by_service(start_date: str, end_date: str) -> CostReport:
    """
    Fetches daily cost broken down by AWS service for the given date range.

    start_date/end_date: 'YYYY-MM-DD' strings. end_date is exclusive, per
    Cost Explorer's own convention (confirmed in the verified sample: a
    query for 2026-06-21 to 2026-06-28 returned 7 daily periods, not 8).

    This issues exactly ONE API call for typical date ranges and group
    counts. Pagination (and its associated per-page cost) only occurs if
    the result set is unusually large - not expected for a single
    SERVICE-grouped daily query over a few weeks.
    """
    client = boto3.client("ce")

    response = client.get_cost_and_usage(
        TimePeriod={"Start": start_date, "End": end_date},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    report = CostReport(start_date=start_date, end_date=end_date)

    for period in response.get("ResultsByTime", []):
        date = period["TimePeriod"]["Start"]
        estimated = period.get("Estimated", False)

        for group in period.get("Groups", []):
            service = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])

            report.daily_costs.append(
                DailyServiceCost(
                    date=date,
                    service=service,
                    amount=amount,
                    estimated=estimated,
                )
            )

    return report


if __name__ == "__main__":
    import sys

    start = sys.argv[1] if len(sys.argv) > 1 else "2026-06-21"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-28"

    report = fetch_daily_costs_by_service(start, end)
    print(f"Fetched cost report: {report.start_date} to {report.end_date}")
    print(f"Dates: {report.all_dates()}")
    print(f"Services seen: {report.all_services()}")
    for date in report.all_dates():
        print(f"\n{date} (total: ${report.total_for_date(date):.6f}):")
        for entry in report.services_on(date):
            if entry.amount > 0:
                print(f"  {entry.service}: ${entry.amount:.6f}")