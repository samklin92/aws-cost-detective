"""
test_tag_auditor.py

Pure logic tests - no boto3, no network, no cost. Covers the two
deliberate design decisions documented in tag_auditor.py's own docstring:
case-sensitive tag key matching, and empty-string values treated as
non-compliant (not just missing keys).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

from tag_auditor import audit_resources, compliance_rate, summarize_by_resource_type
from tag_fetcher import TaggedResource


def test_fully_tagged_resource_has_no_violations():
    resources = [
        TaggedResource(
            arn="arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
            resource_type="ec2:instance",
            tags={"Project": "PyOps", "Environment": "prod", "ManagedBy": "terraform"},
        )
    ]
    violations = audit_resources(resources)
    assert violations == []


def test_missing_tag_key_is_flagged():
    resources = [
        TaggedResource(
            arn="arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
            resource_type="ec2:instance",
            tags={"Project": "PyOps", "Environment": "prod"},  # ManagedBy absent entirely
        )
    ]
    violations = audit_resources(resources)
    assert len(violations) == 1
    assert violations[0].missing_tags == ["ManagedBy"]


def test_empty_string_tag_value_is_flagged_same_as_missing():
    """A tag key that exists but with an empty value is not meaningfully
    attributed - must be treated as non-compliant, not compliant."""
    resources = [
        TaggedResource(
            arn="arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
            resource_type="ec2:instance",
            tags={"Project": "PyOps", "Environment": "", "ManagedBy": "terraform"},
        )
    ]
    violations = audit_resources(resources)
    assert len(violations) == 1
    assert violations[0].missing_tags == ["Environment"]


def test_tag_key_matching_is_case_sensitive():
    """'project' (lowercase) must NOT satisfy the requirement for 'Project' -
    this mirrors real AWS/Cost Explorer tag key case-sensitivity."""
    resources = [
        TaggedResource(
            arn="arn:aws:ec2:us-east-1:123456789012:instance/i-abc123",
            resource_type="ec2:instance",
            tags={"project": "PyOps", "Environment": "prod", "ManagedBy": "terraform"},
        )
    ]
    violations = audit_resources(resources)
    assert len(violations) == 1
    assert violations[0].missing_tags == ["Project"]


def test_multiple_missing_tags_all_listed():
    resources = [
        TaggedResource(
            arn="arn:aws:s3:::my-bucket",
            resource_type="s3:my-bucket",
            tags={},
        )
    ]
    violations = audit_resources(resources)
    assert len(violations) == 1
    assert set(violations[0].missing_tags) == {"Project", "Environment", "ManagedBy"}


def test_summarize_by_resource_type_counts_and_sorts_descending():
    resources = [
        TaggedResource(arn="arn:1", resource_type="ec2:instance", tags={}),
        TaggedResource(arn="arn:2", resource_type="ec2:instance", tags={}),
        TaggedResource(arn="arn:3", resource_type="s3:bucket", tags={}),
    ]
    violations = audit_resources(resources)
    summary = summarize_by_resource_type(violations)
    assert summary == {"ec2:instance": 2, "s3:bucket": 1}
    assert list(summary.keys())[0] == "ec2:instance"  # highest violation count first


def test_compliance_rate_calculation():
    resources = [
        TaggedResource(
            arn="arn:1", resource_type="ec2:instance",
            tags={"Project": "x", "Environment": "prod", "ManagedBy": "terraform"},
        ),
        TaggedResource(arn="arn:2", resource_type="ec2:instance", tags={}),
        TaggedResource(arn="arn:3", resource_type="ec2:instance", tags={}),
        TaggedResource(arn="arn:4", resource_type="ec2:instance", tags={}),
    ]
    violations = audit_resources(resources)
    assert compliance_rate(resources, violations) == 25.0  # 1 of 4 fully compliant


def test_compliance_rate_is_100_for_empty_account():
    """No resources means nothing is out of compliance - a true, if
    trivial, statement. Must not raise a ZeroDivisionError."""
    assert compliance_rate([], []) == 100.0
