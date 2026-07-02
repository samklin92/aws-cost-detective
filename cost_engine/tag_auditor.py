"""
tag_auditor.py

Deterministic compliance checking over a list of TaggedResource objects.
No AWS calls here, no Claude - pure logic, same separation-of-concerns
principle as analyzer.py (Cost Detective) and differ.py (Drift Detector).
If a resource is wrongly flagged as compliant/non-compliant, that's a
code bug here, not a data or explanation problem.

Design decision: exact-match tag KEYS are required (case-sensitive) -
"Project" and "project" are treated as different tags. This mirrors how
AWS itself treats tag keys (case-sensitive) and how cost allocation tag
activation works in Billing - a lenient case-insensitive checker here
would create a false sense of compliance that doesn't match what Cost
Explorer's own cost allocation tags actually see.

Empty-string tag VALUES are treated as non-compliant, not just missing
keys - a resource tagged Environment="" is not meaningfully attributed
to any environment, and this is a real pattern seen in accounts where a
tag was added by a script that failed to populate the value.
"""

from dataclasses import dataclass

from tag_fetcher import TaggedResource

REQUIRED_TAGS = ["Project", "Environment", "ManagedBy"]


@dataclass
class TagViolation:
    arn: str
    resource_type: str
    missing_tags: list[str]  # tags absent or present-but-empty


def audit_resources(
    resources: list[TaggedResource],
    required_tags: list[str] = REQUIRED_TAGS,
) -> list[TagViolation]:
    violations = []
    for resource in resources:
        missing = [
            tag for tag in required_tags
            if tag not in resource.tags or resource.tags[tag].strip() == ""
        ]
        if missing:
            violations.append(
                TagViolation(arn=resource.arn, resource_type=resource.resource_type, missing_tags=missing)
            )
    return violations


def summarize_by_resource_type(violations: list[TagViolation]) -> dict[str, int]:
    """Count of non-compliant resources per resource type, highest first -
    tells you where to focus tagging effort, not just the raw total."""
    counts: dict[str, int] = {}
    for v in violations:
        counts[v.resource_type] = counts.get(v.resource_type, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def compliance_rate(resources: list[TaggedResource], violations: list[TagViolation]) -> float:
    """Percentage of resources that are FULLY compliant (zero missing tags).
    Returns 100.0 for an empty account - no resources means nothing is
    out of compliance, which is a true (if trivial) statement, not an
    error condition."""
    if not resources:
        return 100.0
    non_compliant_count = len(violations)
    compliant_count = len(resources) - non_compliant_count
    return round((compliant_count / len(resources)) * 100, 1)


if __name__ == "__main__":
    import sys

    from tag_fetcher import fetch_tagged_resources

    filters = sys.argv[1:] if len(sys.argv) > 1 else None
    resources = fetch_tagged_resources(filters)
    violations = audit_resources(resources)

    print(f"=== Tag Compliance Report ===")
    print(f"Resources scanned: {len(resources)}")
    print(f"Compliance rate: {compliance_rate(resources, violations)}%")
    print(f"Required tags: {REQUIRED_TAGS}")

    print("\n=== Violations by resource type ===")
    by_type = summarize_by_resource_type(violations)
    if not by_type:
        print("  None - every scanned resource has all required tags.")
    for resource_type, count in by_type.items():
        print(f"  {resource_type}: {count} non-compliant")

    print(f"\n=== Full violation list ({len(violations)}) ===")
    for v in violations[:20]:
        print(f"  {v.arn} | missing: {v.missing_tags}")
    if len(violations) > 20:
        print(f"  ... and {len(violations) - 20} more")
