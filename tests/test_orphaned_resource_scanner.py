"""
test_orphaned_resource_scanner.py

Fully mocked - no live AWS calls, zero cost, matching the convention in
test_cost_fetcher.py. Each scan_* function takes a client directly (no
internal boto3.client() call to patch), so tests just pass a MagicMock
shaped like the real API response for that specific call.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "cost_engine"))

from orphaned_resource_scanner import (
    scan_unattached_snapshots,
    scan_unattached_amis,
    scan_unattached_eips,
    scan_unhealthy_load_balancers,
    scan_all_orphaned_resources,
)


def test_snapshot_with_no_live_volume_and_no_ami_reference_is_orphaned():
    ec2 = MagicMock()
    ec2.describe_snapshots.return_value = {
        "Snapshots": [
            {"SnapshotId": "snap-orphaned", "VolumeId": "vol-deleted", "VolumeSize": 8, "StartTime": "2026-01-01"}
        ]
    }
    ec2.describe_volumes.return_value = {"Volumes": [{"VolumeId": "vol-still-alive"}]}
    ec2.describe_images.return_value = {"Images": []}

    result = scan_unattached_snapshots(ec2)

    assert len(result) == 1
    assert result[0].resource_id == "snap-orphaned"
    assert result[0].resource_type == "ebs_snapshot"


def test_snapshot_referenced_by_current_ami_is_not_orphaned():
    """A snapshot backing a live AMI is doing a job, even with no volume -
    this is the case that would false-positive without the AMI cross-check."""
    ec2 = MagicMock()
    ec2.describe_snapshots.return_value = {
        "Snapshots": [{"SnapshotId": "snap-in-use", "VolumeId": None, "VolumeSize": 8, "StartTime": "2026-01-01"}]
    }
    ec2.describe_volumes.return_value = {"Volumes": []}
    ec2.describe_images.return_value = {
        "Images": [{"ImageId": "ami-1", "BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-in-use"}}]}]
    }

    result = scan_unattached_snapshots(ec2)

    assert result == []


def test_snapshot_with_live_volume_is_not_orphaned():
    ec2 = MagicMock()
    ec2.describe_snapshots.return_value = {
        "Snapshots": [{"SnapshotId": "snap-attached", "VolumeId": "vol-live", "VolumeSize": 8, "StartTime": "2026-01-01"}]
    }
    ec2.describe_volumes.return_value = {"Volumes": [{"VolumeId": "vol-live"}]}
    ec2.describe_images.return_value = {"Images": []}

    result = scan_unattached_snapshots(ec2)

    assert result == []


def test_ami_with_no_running_instance_is_orphaned():
    ec2 = MagicMock()
    ec2.describe_images.return_value = {
        "Images": [{"ImageId": "ami-orphaned", "Name": "old-build", "CreationDate": "2026-01-01"}]
    }
    ec2.describe_instances.return_value = {"Reservations": []}

    result = scan_unattached_amis(ec2)

    assert len(result) == 1
    assert result[0].resource_id == "ami-orphaned"


def test_ami_with_running_instance_is_not_orphaned():
    ec2 = MagicMock()
    ec2.describe_images.return_value = {
        "Images": [{"ImageId": "ami-in-use", "Name": "current-build", "CreationDate": "2026-01-01"}]
    }
    ec2.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"ImageId": "ami-in-use", "State": {"Name": "running"}}]}]
    }

    result = scan_unattached_amis(ec2)

    assert result == []


def test_ami_with_only_terminated_instance_is_still_orphaned():
    """A terminated instance doesn't keep an AMI 'in use' - this is the
    case that would false-negative without checking instance state."""
    ec2 = MagicMock()
    ec2.describe_images.return_value = {
        "Images": [{"ImageId": "ami-was-used", "Name": "old-build", "CreationDate": "2026-01-01"}]
    }
    ec2.describe_instances.return_value = {
        "Reservations": [{"Instances": [{"ImageId": "ami-was-used", "State": {"Name": "terminated"}}]}]
    }

    result = scan_unattached_amis(ec2)

    assert len(result) == 1


def test_eip_with_no_instance_or_eni_is_orphaned():
    ec2 = MagicMock()
    ec2.describe_addresses.return_value = {
        "Addresses": [{"AllocationId": "eipalloc-orphaned", "PublicIp": "1.2.3.4"}]
    }

    result = scan_unattached_eips(ec2)

    assert len(result) == 1
    assert result[0].resource_id == "eipalloc-orphaned"


def test_eip_attached_to_instance_is_not_orphaned():
    ec2 = MagicMock()
    ec2.describe_addresses.return_value = {
        "Addresses": [{"AllocationId": "eipalloc-attached", "PublicIp": "1.2.3.4", "InstanceId": "i-123"}]
    }

    result = scan_unattached_eips(ec2)

    assert result == []


def test_load_balancer_with_zero_healthy_targets_is_orphaned():
    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {
        "LoadBalancers": [{"LoadBalancerArn": "arn:lb:1", "LoadBalancerName": "unhealthy-lb"}]
    }
    elbv2.describe_target_groups.return_value = {"TargetGroups": [{"TargetGroupArn": "arn:tg:1"}]}
    elbv2.describe_target_health.return_value = {
        "TargetHealthDescriptions": [{"TargetHealth": {"State": "unhealthy"}}]
    }

    result = scan_unhealthy_load_balancers(elbv2)

    assert len(result) == 1
    assert result[0].resource_id == "unhealthy-lb"


def test_load_balancer_with_at_least_one_healthy_target_is_not_orphaned():
    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {
        "LoadBalancers": [{"LoadBalancerArn": "arn:lb:1", "LoadBalancerName": "healthy-lb"}]
    }
    elbv2.describe_target_groups.return_value = {"TargetGroups": [{"TargetGroupArn": "arn:tg:1"}]}
    elbv2.describe_target_health.return_value = {
        "TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}}]
    }

    result = scan_unhealthy_load_balancers(elbv2)

    assert result == []


def test_scan_all_aggregates_across_all_four_checks():
    """Regression guard: scan_all_orphaned_resources must call all four
    scan functions and combine their results, not silently drop any."""
    ec2 = MagicMock()
    ec2.describe_snapshots.return_value = {"Snapshots": []}
    ec2.describe_volumes.return_value = {"Volumes": []}
    ec2.describe_images.return_value = {"Images": []}
    ec2.describe_instances.return_value = {"Reservations": []}
    ec2.describe_addresses.return_value = {
        "Addresses": [{"AllocationId": "eipalloc-x", "PublicIp": "9.9.9.9"}]
    }

    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {"LoadBalancers": []}

    result = scan_all_orphaned_resources(ec2, elbv2)

    assert len(result) == 1
    assert result[0].resource_type == "elastic_ip"
