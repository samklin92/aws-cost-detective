"""
tag_fetcher.py

Fetches every taggable resource in the account via the Resource Groups
Tagging API (resourcegroupstaggingapi) - a single API that covers most
AWS services without needing a separate boto3 client per service. This
is deliberately the ONLY file in tag_auditor that talks to AWS, matching
the project's established separation of fetching from logic
(cost_fetcher.py / analyzer.py, state_extractor.py / differ.py).

Cost note: GetResources is NOT in the Cost Explorer pricing tier - it's
a free, standard API call (unlike ce:GetCostAndUsage at $0.01/call). No
per-call cost tracking needed here.

Pagination: AWS returns resources in pages of up to 100 (PaginationToken).
This fetcher follows the token until exhausted - do not assume a single
page is the whole account, that assumption caused the Drift Detector's
own comparison bugs earlier in this build.
"""

from dataclasses import dataclass, field

import boto3


@dataclass
class TaggedResource:
    arn: str
    resource_type: str  # derived from the ARN's service+resource segment, e.g. "ec2:instance"
    tags: dict[str, str] = field(default_factory=dict)


def _resource_type_from_arn(arn: str) -> str:
    """
    ARN shape: arn:partition:service:region:account-id:resource-type/resource-id
    or         arn:partition:service:region:account-id:resource-type:resource-id
    Not every service follows this identically, so this is a best-effort
    label for grouping/reporting, not a strict parser relied on for logic.
    """
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return "unknown"
    service = parts[2]
    remainder = parts[5]
    resource_type = remainder.split("/")[0].split(":")[0] if remainder else "unknown"
    return f"{service}:{resource_type}"


def fetch_tagged_resources(resource_type_filters: list[str] | None = None) -> list[TaggedResource]:
    """
    resource_type_filters: optional AWS service-level filters (e.g. ["ec2", "s3", "rds"]).
    None means all supported resource types in the account/region.
    """
    client = boto3.client("resourcegroupstaggingapi")
    resources: list[TaggedResource] = []
    pagination_token = ""

    while True:
        kwargs = {"PaginationToken": pagination_token}
        if resource_type_filters:
            kwargs["ResourceTypeFilters"] = resource_type_filters

        response = client.get_resources(**kwargs)

        for mapping in response.get("ResourceTagMappingList", []):
            arn = mapping["ResourceARN"]
            tags = {t["Key"]: t["Value"] for t in mapping.get("Tags", [])}
            resources.append(
                TaggedResource(arn=arn, resource_type=_resource_type_from_arn(arn), tags=tags)
            )

        pagination_token = response.get("PaginationToken", "")
        if not pagination_token:
            break

    return resources


if __name__ == "__main__":
    import sys

    filters = sys.argv[1:] if len(sys.argv) > 1 else None
    found = fetch_tagged_resources(filters)
    print(f"Fetched {len(found)} taggable resources" + (f" (filtered to {filters})" if filters else ""))
    for r in found[:10]:
        print(f"  {r.resource_type} | {r.arn} | tags: {list(r.tags.keys())}")
    if len(found) > 10:
        print(f"  ... and {len(found) - 10} more")
