"""
orphaned_resource_scanner.py

Static inventory check for resources that cost money but do nothing -
unattached EBS snapshots, orphaned AMIs, unattached Elastic IPs, and load
balancers with zero healthy targets.

Deliberately NOT anomaly detection. analyzer.py's trend-based detection is
keyed on CHANGE - a resource sitting at a flat, unmoving cost every single
day never triggers a stddev threshold, no matter how expensive it is. This
module exists specifically to catch that blind spot - a real-world example
of this exact pattern (untagged snapshots and AMIs running ~$300/month,
invisible to anomaly detection because the cost never moved) is what
prompted this addition to the project.

Each scan_* function takes an already-constructed boto3 client rather than
constructing one internally - this keeps the function testable by passing
a mock client directly, and lets the caller decide the client's scope
(e.g. a cross-account assumed-role session in the Lambda handler, or a
plain default-credentials client when run locally/via the CLI).
"""

from dataclasses import dataclass


@dataclass
class OrphanedResource:
    resource_type: str
    resource_id: str
    detail: str


def scan_unattached_snapshots(ec2_client) -> list[OrphanedResource]:
    """A snapshot with no source volume still in the account, and not
    referenced by any current AMI, is not doing anything for anyone.
    describe_snapshots doesn't expose "orphaned" directly - determining it
    requires cross-referencing against current volumes and AMIs."""
    snapshots = ec2_client.describe_snapshots(OwnerIds=["self"])["Snapshots"]
    live_volume_ids = {v["VolumeId"] for v in ec2_client.describe_volumes()["Volumes"]}

    snapshot_ids_in_amis = set()
    for image in ec2_client.describe_images(Owners=["self"])["Images"]:
        for mapping in image.get("BlockDeviceMappings", []):
            ebs = mapping.get("Ebs")
            if ebs and "SnapshotId" in ebs:
                snapshot_ids_in_amis.add(ebs["SnapshotId"])

    orphaned = []
    for snap in snapshots:
        volume_id = snap.get("VolumeId")
        snapshot_id = snap["SnapshotId"]
        if (not volume_id or volume_id not in live_volume_ids) and snapshot_id not in snapshot_ids_in_amis:
            orphaned.append(
                OrphanedResource(
                    resource_type="ebs_snapshot",
                    resource_id=snapshot_id,
                    detail=f"{snap.get('VolumeSize', '?')} GiB, started {snap.get('StartTime', 'unknown date')}",
                )
            )
    return orphaned


def scan_unattached_amis(ec2_client) -> list[OrphanedResource]:
    """AMIs themselves are near-zero direct cost, but each references
    snapshots that DO cost money - an AMI nobody launches from is exactly
    why those snapshots never get cleaned up. Flags AMIs with zero
    non-terminated instances currently running from them."""
    images = ec2_client.describe_images(Owners=["self"])["Images"]
    reservations = ec2_client.describe_instances()["Reservations"]
    running_image_ids = {
        instance["ImageId"]
        for reservation in reservations
        for instance in reservation["Instances"]
        if instance["State"]["Name"] not in ("terminated", "shutting-down")
    }

    orphaned = []
    for image in images:
        if image["ImageId"] not in running_image_ids:
            orphaned.append(
                OrphanedResource(
                    resource_type="ami",
                    resource_id=image["ImageId"],
                    detail=f"{image.get('Name', 'unnamed')}, created {image.get('CreationDate', 'unknown date')}",
                )
            )
    return orphaned


def scan_unattached_eips(ec2_client) -> list[OrphanedResource]:
    """An Elastic IP not associated with a running instance or network
    interface bills hourly for doing nothing - one of the most common,
    most invisible steady-state cost leaks in any account."""
    addresses = ec2_client.describe_addresses()["Addresses"]
    orphaned = []
    for addr in addresses:
        if "InstanceId" not in addr and "NetworkInterfaceId" not in addr:
            orphaned.append(
                OrphanedResource(
                    resource_type="elastic_ip",
                    resource_id=addr.get("AllocationId", addr.get("PublicIp", "unknown")),
                    detail=f"Public IP {addr.get('PublicIp', 'unknown')}, not associated with any resource",
                )
            )
    return orphaned


def scan_unhealthy_load_balancers(elbv2_client) -> list[OrphanedResource]:
    """A load balancer with zero healthy targets across every target group
    it has is billing for distributing traffic to nothing - usually a
    forgotten test environment or a broken deployment nobody noticed."""
    load_balancers = elbv2_client.describe_load_balancers()["LoadBalancers"]
    orphaned = []
    for lb in load_balancers:
        lb_arn = lb["LoadBalancerArn"]
        target_groups = elbv2_client.describe_target_groups(LoadBalancerArn=lb_arn)["TargetGroups"]
        any_healthy = False
        for tg in target_groups:
            health = elbv2_client.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])["TargetHealthDescriptions"]
            if any(h["TargetHealth"]["State"] == "healthy" for h in health):
                any_healthy = True
                break
        if not any_healthy:
            orphaned.append(
                OrphanedResource(
                    resource_type="load_balancer",
                    resource_id=lb["LoadBalancerName"],
                    detail=f"{len(target_groups)} target group(s), zero healthy targets",
                )
            )
    return orphaned


def scan_all_orphaned_resources(ec2_client, elbv2_client) -> list[OrphanedResource]:
    """Single entrypoint aggregating all four checks - callers (the Lambda
    handler, or a future CLI flag) only need to know about this function."""
    findings: list[OrphanedResource] = []
    findings.extend(scan_unattached_snapshots(ec2_client))
    findings.extend(scan_unattached_amis(ec2_client))
    findings.extend(scan_unattached_eips(ec2_client))
    findings.extend(scan_unhealthy_load_balancers(elbv2_client))
    return findings
