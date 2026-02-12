"""
AWS Integration Tools for Crowd IT MCP Server

This module provides comprehensive AWS management capabilities including:
- EC2 (instances, security groups, key pairs)
- S3 (buckets, objects)
- VPC (VPCs, subnets, security groups, route tables)
- IAM (users, roles, policies)
- Lambda (functions, invocations)
- CloudWatch (metrics, alarms, logs)
- Cost Explorer (cost summaries, forecasts)
- Route53 (hosted zones, DNS records)
- RDS (DB instances, snapshots)
- ECS (clusters, services, tasks)

Requirements:
    pip install boto3

Environment Variables:
    AWS_ACCESS_KEY_ID: AWS access key ID
    AWS_SECRET_ACCESS_KEY: AWS secret access key
    AWS_DEFAULT_REGION: Default AWS region (e.g., 'ap-southeast-2')
    AWS_SESSION_TOKEN: (Optional) Temporary session token for assumed roles
"""

import os
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from pydantic import Field


# =============================================================================
# Configuration and Authentication
# =============================================================================

class AWSConfig:
    """AWS configuration using boto3 credentials."""

    def __init__(self):
        self.access_key_id = os.getenv("AWS_ACCESS_KEY_ID", "")
        self.secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        self.region = os.getenv("AWS_DEFAULT_REGION", "ap-southeast-2")
        self.session_token = os.getenv("AWS_SESSION_TOKEN", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.access_key_id) and bool(self.secret_access_key)

    def get_client(self, service_name: str, region: str = None):
        """Get a boto3 client for the specified AWS service."""
        import boto3

        kwargs = {
            "service_name": service_name,
            "region_name": region or self.region,
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
        }
        if self.session_token:
            kwargs["aws_session_token"] = self.session_token

        return boto3.client(**kwargs)

    def get_resource(self, service_name: str, region: str = None):
        """Get a boto3 resource for the specified AWS service."""
        import boto3

        kwargs = {
            "service_name": service_name,
            "region_name": region or self.region,
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
        }
        if self.session_token:
            kwargs["aws_session_token"] = self.session_token

        return boto3.resource(**kwargs)


def handle_aws_error(e: Exception) -> str:
    """Handle AWS API errors consistently."""
    try:
        from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError
        if isinstance(e, NoCredentialsError):
            return "Error: AWS credentials not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        elif isinstance(e, ClientError):
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]
            return f"Error: AWS API error ({error_code}): {error_msg}"
        elif isinstance(e, ParamValidationError):
            return f"Error: Invalid parameters: {str(e)}"
    except ImportError:
        pass
    return f"Error: {type(e).__name__}: {str(e)}"


# =============================================================================
# Tool Registration Function
# =============================================================================

def register_aws_tools(mcp, aws_config: AWSConfig):
    """Register all AWS tools with the MCP server."""

    # =========================================================================
    # Account & Identity Tools
    # =========================================================================

    @mcp.tool(
        name="aws_get_caller_identity",
        annotations={
            "title": "Get AWS Caller Identity",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_get_caller_identity() -> str:
        """Get the identity of the AWS caller (account, user ARN, user ID).

        Useful to verify which AWS account and credentials are being used.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            sts = aws_config.get_client("sts")
            identity = sts.get_caller_identity()
            return (
                f"# AWS Caller Identity\n\n"
                f"**Account:** {identity['Account']}\n"
                f"**ARN:** `{identity['Arn']}`\n"
                f"**User ID:** {identity['UserId']}\n"
                f"**Region:** {aws_config.region}"
            )
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_list_regions",
        annotations={
            "title": "List AWS Regions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_regions() -> str:
        """List all available AWS regions."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2")
            response = ec2.describe_regions(AllRegions=True)
            regions = response["Regions"]

            result = "# AWS Regions\n\n"
            result += "| Region | Endpoint | Status |\n"
            result += "|--------|----------|--------|\n"
            for r in sorted(regions, key=lambda x: x["RegionName"]):
                opt_in = r.get("OptInStatus", "opt-in-not-required")
                status = "Active" if opt_in != "not-opted-in" else "Not opted in"
                result += f"| {r['RegionName']} | {r['Endpoint']} | {status} |\n"

            result += f"\n**Default region:** {aws_config.region}"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # EC2 Instance Tools
    # =========================================================================

    @mcp.tool(
        name="aws_ec2_list_instances",
        annotations={
            "title": "List EC2 Instances",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ec2_list_instances(
        region: Optional[str] = Field(default=None, description="AWS region (uses default if not provided)"),
        state_filter: Optional[str] = Field(
            default=None,
            description="Filter by state: 'running', 'stopped', 'terminated', 'pending', 'all'",
        ),
        tag_filter: Optional[str] = Field(
            default=None,
            description="Filter by tag Name value (partial match)",
        ),
    ) -> str:
        """List all EC2 instances in a region with status, type, and tags."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2", region)
            filters = []
            if state_filter and state_filter != "all":
                filters.append({"Name": "instance-state-name", "Values": [state_filter]})

            kwargs = {}
            if filters:
                kwargs["Filters"] = filters

            response = ec2.describe_instances(**kwargs)
            instances = []
            for reservation in response["Reservations"]:
                for inst in reservation["Instances"]:
                    name = ""
                    for tag in inst.get("Tags", []):
                        if tag["Key"] == "Name":
                            name = tag["Value"]
                            break
                    if tag_filter and tag_filter.lower() not in name.lower():
                        continue
                    instances.append({
                        "id": inst["InstanceId"],
                        "name": name,
                        "type": inst["InstanceType"],
                        "state": inst["State"]["Name"],
                        "az": inst["Placement"]["AvailabilityZone"],
                        "private_ip": inst.get("PrivateIpAddress", "-"),
                        "public_ip": inst.get("PublicIpAddress", "-"),
                        "launch_time": inst.get("LaunchTime", "").isoformat() if inst.get("LaunchTime") else "-",
                    })

            if not instances:
                return f"No EC2 instances found in {region or aws_config.region}"

            result = f"# EC2 Instances ({region or aws_config.region})\n\n"
            result += "| Name | Instance ID | Type | State | Private IP | Public IP | AZ |\n"
            result += "|------|-------------|------|-------|------------|-----------|----|\n"
            for inst in instances:
                result += f"| {inst['name'] or '-'} | {inst['id']} | {inst['type']} | {inst['state']} | {inst['private_ip']} | {inst['public_ip']} | {inst['az']} |\n"

            result += f"\n**Total:** {len(instances)} instance(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_ec2_get_instance",
        annotations={
            "title": "Get EC2 Instance Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ec2_get_instance(
        instance_id: str = Field(..., description="EC2 instance ID (e.g., 'i-0abc123def456')"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Get detailed information about a specific EC2 instance."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2", region)
            response = ec2.describe_instances(InstanceIds=[instance_id])

            if not response["Reservations"] or not response["Reservations"][0]["Instances"]:
                return f"Instance {instance_id} not found."

            inst = response["Reservations"][0]["Instances"][0]
            name = ""
            for tag in inst.get("Tags", []):
                if tag["Key"] == "Name":
                    name = tag["Value"]
                    break

            result = f"# EC2 Instance: {name or instance_id}\n\n"
            result += f"**Instance ID:** `{inst['InstanceId']}`\n"
            result += f"**State:** {inst['State']['Name']}\n"
            result += f"**Instance Type:** {inst['InstanceType']}\n"
            result += f"**AMI:** {inst.get('ImageId', '-')}\n"
            result += f"**Availability Zone:** {inst['Placement']['AvailabilityZone']}\n"
            result += f"**Private IP:** {inst.get('PrivateIpAddress', '-')}\n"
            result += f"**Public IP:** {inst.get('PublicIpAddress', '-')}\n"
            result += f"**Private DNS:** {inst.get('PrivateDnsName', '-')}\n"
            result += f"**Public DNS:** {inst.get('PublicDnsName', '-')}\n"
            result += f"**VPC ID:** {inst.get('VpcId', '-')}\n"
            result += f"**Subnet ID:** {inst.get('SubnetId', '-')}\n"
            result += f"**Key Name:** {inst.get('KeyName', '-')}\n"
            result += f"**Launch Time:** {inst.get('LaunchTime', '-')}\n"
            result += f"**Platform:** {inst.get('PlatformDetails', inst.get('Platform', 'Linux/UNIX'))}\n"
            result += f"**Architecture:** {inst.get('Architecture', '-')}\n"

            # Security Groups
            sgs = inst.get("SecurityGroups", [])
            if sgs:
                result += f"\n## Security Groups\n"
                for sg in sgs:
                    result += f"- **{sg['GroupName']}** (`{sg['GroupId']}`)\n"

            # Block Devices
            devices = inst.get("BlockDeviceMappings", [])
            if devices:
                result += f"\n## Block Devices\n"
                for dev in devices:
                    ebs = dev.get("Ebs", {})
                    result += f"- **{dev['DeviceName']}** - Volume: `{ebs.get('VolumeId', '-')}`, Status: {ebs.get('Status', '-')}\n"

            # Tags
            tags = inst.get("Tags", [])
            if tags:
                result += f"\n## Tags\n"
                for tag in sorted(tags, key=lambda t: t["Key"]):
                    result += f"- **{tag['Key']}:** {tag['Value']}\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_ec2_power",
        annotations={
            "title": "EC2 Power Operations",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ec2_power(
        instance_ids: str = Field(..., description="Comma-separated instance IDs (e.g., 'i-0abc123,i-0def456')"),
        action: str = Field(..., description="Power action: 'start', 'stop', 'reboot'"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Start, stop, or reboot EC2 instances."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2", region)
            ids = [i.strip() for i in instance_ids.split(",")]
            action_lower = action.lower()

            if action_lower == "start":
                ec2.start_instances(InstanceIds=ids)
                return f"Starting {len(ids)} instance(s): {', '.join(ids)}\n\nUse aws_ec2_get_instance to check status."
            elif action_lower == "stop":
                ec2.stop_instances(InstanceIds=ids)
                return f"Stopping {len(ids)} instance(s): {', '.join(ids)}\n\nUse aws_ec2_get_instance to check status."
            elif action_lower == "reboot":
                ec2.reboot_instances(InstanceIds=ids)
                return f"Rebooting {len(ids)} instance(s): {', '.join(ids)}\n\nUse aws_ec2_get_instance to check status."
            else:
                return f"Error: Invalid action '{action}'. Use: start, stop, reboot"
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # EC2 Security Group Tools
    # =========================================================================

    @mcp.tool(
        name="aws_ec2_list_security_groups",
        annotations={
            "title": "List Security Groups",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ec2_list_security_groups(
        region: Optional[str] = Field(default=None, description="AWS region"),
        vpc_id: Optional[str] = Field(default=None, description="Filter by VPC ID"),
    ) -> str:
        """List all EC2 security groups."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2", region)
            filters = []
            if vpc_id:
                filters.append({"Name": "vpc-id", "Values": [vpc_id]})

            kwargs = {}
            if filters:
                kwargs["Filters"] = filters

            response = ec2.describe_security_groups(**kwargs)
            sgs = response["SecurityGroups"]

            if not sgs:
                return "No security groups found"

            result = "# Security Groups\n\n"
            for sg in sgs:
                result += f"## {sg['GroupName']} (`{sg['GroupId']}`)\n"
                result += f"- **VPC:** {sg.get('VpcId', '-')}\n"
                result += f"- **Description:** {sg.get('Description', '-')}\n"

                # Inbound rules
                if sg.get("IpPermissions"):
                    result += f"- **Inbound Rules:**\n"
                    for rule in sg["IpPermissions"]:
                        proto = rule.get("IpProtocol", "-")
                        from_port = rule.get("FromPort", "All")
                        to_port = rule.get("ToPort", "All")
                        port_range = f"{from_port}-{to_port}" if from_port != to_port else str(from_port)
                        if proto == "-1":
                            proto = "All"
                            port_range = "All"
                        sources = [r["CidrIp"] for r in rule.get("IpRanges", [])]
                        sources += [r["CidrIpv6"] for r in rule.get("Ipv6Ranges", [])]
                        sources += [r["GroupId"] for r in rule.get("UserIdGroupPairs", [])]
                        result += f"  - {proto} port {port_range} from {', '.join(sources) or 'N/A'}\n"

                # Outbound rules
                if sg.get("IpPermissionsEgress"):
                    result += f"- **Outbound Rules:**\n"
                    for rule in sg["IpPermissionsEgress"]:
                        proto = rule.get("IpProtocol", "-")
                        from_port = rule.get("FromPort", "All")
                        to_port = rule.get("ToPort", "All")
                        port_range = f"{from_port}-{to_port}" if from_port != to_port else str(from_port)
                        if proto == "-1":
                            proto = "All"
                            port_range = "All"
                        targets = [r["CidrIp"] for r in rule.get("IpRanges", [])]
                        targets += [r["CidrIpv6"] for r in rule.get("Ipv6Ranges", [])]
                        result += f"  - {proto} port {port_range} to {', '.join(targets) or 'All'}\n"

                result += "\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # VPC Tools
    # =========================================================================

    @mcp.tool(
        name="aws_vpc_list",
        annotations={
            "title": "List VPCs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_vpc_list(
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List all VPCs in a region with CIDR blocks, state, and tags."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2", region)
            response = ec2.describe_vpcs()
            vpcs = response["Vpcs"]

            if not vpcs:
                return f"No VPCs found in {region or aws_config.region}"

            result = f"# VPCs ({region or aws_config.region})\n\n"
            for vpc in vpcs:
                name = ""
                for tag in vpc.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break

                result += f"## {name or vpc['VpcId']}\n"
                result += f"- **VPC ID:** `{vpc['VpcId']}`\n"
                result += f"- **CIDR Block:** {vpc['CidrBlock']}\n"
                result += f"- **State:** {vpc['State']}\n"
                result += f"- **Default:** {'Yes' if vpc.get('IsDefault') else 'No'}\n"
                result += f"- **DHCP Options:** {vpc.get('DhcpOptionsId', '-')}\n"

                # Additional CIDR blocks
                assoc = vpc.get("CidrBlockAssociationSet", [])
                if len(assoc) > 1:
                    result += f"- **Additional CIDRs:** {', '.join(a['CidrBlock'] for a in assoc[1:])}\n"

                result += "\n"

            result += f"**Total:** {len(vpcs)} VPC(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_vpc_list_subnets",
        annotations={
            "title": "List VPC Subnets",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_vpc_list_subnets(
        region: Optional[str] = Field(default=None, description="AWS region"),
        vpc_id: Optional[str] = Field(default=None, description="Filter by VPC ID"),
    ) -> str:
        """List all subnets, optionally filtered by VPC."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2", region)
            filters = []
            if vpc_id:
                filters.append({"Name": "vpc-id", "Values": [vpc_id]})

            kwargs = {}
            if filters:
                kwargs["Filters"] = filters

            response = ec2.describe_subnets(**kwargs)
            subnets = response["Subnets"]

            if not subnets:
                return "No subnets found"

            result = f"# Subnets ({region or aws_config.region})\n\n"
            result += "| Name | Subnet ID | VPC | CIDR | AZ | Available IPs | Public |\n"
            result += "|------|-----------|-----|------|----|---------------|--------|\n"
            for s in sorted(subnets, key=lambda x: (x.get("VpcId", ""), x.get("AvailabilityZone", ""))):
                name = ""
                for tag in s.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break
                public = "Yes" if s.get("MapPublicIpOnLaunch") else "No"
                result += f"| {name or '-'} | {s['SubnetId']} | {s['VpcId']} | {s['CidrBlock']} | {s['AvailabilityZone']} | {s['AvailableIpAddressCount']} | {public} |\n"

            result += f"\n**Total:** {len(subnets)} subnet(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_vpc_list_route_tables",
        annotations={
            "title": "List Route Tables",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_vpc_list_route_tables(
        region: Optional[str] = Field(default=None, description="AWS region"),
        vpc_id: Optional[str] = Field(default=None, description="Filter by VPC ID"),
    ) -> str:
        """List route tables with their routes."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ec2 = aws_config.get_client("ec2", region)
            filters = []
            if vpc_id:
                filters.append({"Name": "vpc-id", "Values": [vpc_id]})

            kwargs = {}
            if filters:
                kwargs["Filters"] = filters

            response = ec2.describe_route_tables(**kwargs)
            tables = response["RouteTables"]

            if not tables:
                return "No route tables found"

            result = f"# Route Tables ({region or aws_config.region})\n\n"
            for rt in tables:
                name = ""
                for tag in rt.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break

                result += f"## {name or rt['RouteTableId']}\n"
                result += f"- **Route Table ID:** `{rt['RouteTableId']}`\n"
                result += f"- **VPC:** {rt['VpcId']}\n"

                # Associations
                assocs = rt.get("Associations", [])
                if assocs:
                    result += "- **Associations:** "
                    parts = []
                    for a in assocs:
                        if a.get("Main"):
                            parts.append("Main")
                        elif a.get("SubnetId"):
                            parts.append(a["SubnetId"])
                    result += ", ".join(parts) + "\n"

                # Routes
                routes = rt.get("Routes", [])
                if routes:
                    result += "\n| Destination | Target | Status |\n"
                    result += "|-------------|--------|--------|\n"
                    for r in routes:
                        dest = r.get("DestinationCidrBlock") or r.get("DestinationIpv6CidrBlock") or r.get("DestinationPrefixListId", "-")
                        target = r.get("GatewayId") or r.get("NatGatewayId") or r.get("NetworkInterfaceId") or r.get("VpcPeeringConnectionId") or r.get("TransitGatewayId") or "local"
                        status = r.get("State", "-")
                        result += f"| {dest} | {target} | {status} |\n"
                result += "\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # S3 Tools
    # =========================================================================

    @mcp.tool(
        name="aws_s3_list_buckets",
        annotations={
            "title": "List S3 Buckets",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_s3_list_buckets() -> str:
        """List all S3 buckets in the AWS account."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            s3 = aws_config.get_client("s3")
            response = s3.list_buckets()
            buckets = response.get("Buckets", [])

            if not buckets:
                return "No S3 buckets found"

            result = "# S3 Buckets\n\n"
            result += "| Bucket Name | Created |\n"
            result += "|-------------|----------|\n"
            for b in sorted(buckets, key=lambda x: x["Name"]):
                created = b["CreationDate"].strftime("%Y-%m-%d %H:%M") if b.get("CreationDate") else "-"
                result += f"| {b['Name']} | {created} |\n"

            result += f"\n**Total:** {len(buckets)} bucket(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_s3_list_objects",
        annotations={
            "title": "List S3 Objects",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_s3_list_objects(
        bucket: str = Field(..., description="S3 bucket name"),
        prefix: Optional[str] = Field(default=None, description="Key prefix to filter (e.g., 'logs/')"),
        max_keys: int = Field(default=100, description="Maximum number of objects to return (1-1000)"),
    ) -> str:
        """List objects in an S3 bucket with optional prefix filter."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            s3 = aws_config.get_client("s3")
            kwargs = {"Bucket": bucket, "MaxKeys": min(max(1, max_keys), 1000)}
            if prefix:
                kwargs["Prefix"] = prefix

            response = s3.list_objects_v2(**kwargs)
            objects = response.get("Contents", [])

            if not objects:
                return f"No objects found in s3://{bucket}/{prefix or ''}"

            result = f"# Objects in s3://{bucket}/{prefix or ''}\n\n"
            result += "| Key | Size | Last Modified | Storage Class |\n"
            result += "|-----|------|---------------|---------------|\n"
            for obj in objects:
                size = obj["Size"]
                if size >= 1073741824:
                    size_str = f"{size / 1073741824:.1f} GB"
                elif size >= 1048576:
                    size_str = f"{size / 1048576:.1f} MB"
                elif size >= 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} B"
                modified = obj["LastModified"].strftime("%Y-%m-%d %H:%M") if obj.get("LastModified") else "-"
                storage = obj.get("StorageClass", "STANDARD")
                key = obj["Key"]
                if len(key) > 80:
                    key = "..." + key[-77:]
                result += f"| {key} | {size_str} | {modified} | {storage} |\n"

            result += f"\n**Objects shown:** {len(objects)}"
            if response.get("IsTruncated"):
                result += f" (truncated, more objects available)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_s3_get_bucket_info",
        annotations={
            "title": "Get S3 Bucket Info",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_s3_get_bucket_info(
        bucket: str = Field(..., description="S3 bucket name"),
    ) -> str:
        """Get detailed information about an S3 bucket (location, versioning, encryption, public access)."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            s3 = aws_config.get_client("s3")

            result = f"# S3 Bucket: {bucket}\n\n"

            # Location
            try:
                loc = s3.get_bucket_location(Bucket=bucket)
                location = loc.get("LocationConstraint") or "us-east-1"
                result += f"**Region:** {location}\n"
            except Exception:
                result += "**Region:** Unknown\n"

            # Versioning
            try:
                ver = s3.get_bucket_versioning(Bucket=bucket)
                result += f"**Versioning:** {ver.get('Status', 'Disabled')}\n"
            except Exception:
                result += "**Versioning:** Unknown\n"

            # Encryption
            try:
                enc = s3.get_bucket_encryption(Bucket=bucket)
                rules = enc.get("ServerSideEncryptionConfiguration", {}).get("Rules", [])
                if rules:
                    algo = rules[0].get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm", "None")
                    result += f"**Encryption:** {algo}\n"
                else:
                    result += "**Encryption:** None\n"
            except Exception:
                result += "**Encryption:** Not configured\n"

            # Public Access Block
            try:
                pab = s3.get_public_access_block(Bucket=bucket)
                config = pab.get("PublicAccessBlockConfiguration", {})
                all_blocked = all([
                    config.get("BlockPublicAcls", False),
                    config.get("IgnorePublicAcls", False),
                    config.get("BlockPublicPolicy", False),
                    config.get("RestrictPublicBuckets", False),
                ])
                result += f"**Public Access Blocked:** {'Yes' if all_blocked else 'Partially/No'}\n"
            except Exception:
                result += "**Public Access Block:** Not configured\n"

            # Tagging
            try:
                tags = s3.get_bucket_tagging(Bucket=bucket)
                tag_set = tags.get("TagSet", [])
                if tag_set:
                    result += f"\n## Tags\n"
                    for tag in tag_set:
                        result += f"- **{tag['Key']}:** {tag['Value']}\n"
            except Exception:
                pass

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # IAM Tools
    # =========================================================================

    @mcp.tool(
        name="aws_iam_list_users",
        annotations={
            "title": "List IAM Users",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_iam_list_users() -> str:
        """List all IAM users in the AWS account."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            iam = aws_config.get_client("iam")
            response = iam.list_users()
            users = response.get("Users", [])

            if not users:
                return "No IAM users found"

            result = "# IAM Users\n\n"
            result += "| Username | User ID | Created | Last Activity | ARN |\n"
            result += "|----------|---------|---------|---------------|-----|\n"
            for u in users:
                created = u["CreateDate"].strftime("%Y-%m-%d") if u.get("CreateDate") else "-"
                last_used = u.get("PasswordLastUsed", "").strftime("%Y-%m-%d") if u.get("PasswordLastUsed") else "Never"
                arn = u.get("Arn", "-")
                if len(arn) > 60:
                    arn = "..." + arn[-57:]
                result += f"| {u['UserName']} | {u['UserId']} | {created} | {last_used} | {arn} |\n"

            result += f"\n**Total:** {len(users)} user(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_iam_list_roles",
        annotations={
            "title": "List IAM Roles",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_iam_list_roles(
        path_prefix: Optional[str] = Field(default=None, description="Filter by path prefix (e.g., '/service-role/')"),
    ) -> str:
        """List all IAM roles, optionally filtered by path prefix."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            iam = aws_config.get_client("iam")
            kwargs = {}
            if path_prefix:
                kwargs["PathPrefix"] = path_prefix

            response = iam.list_roles(**kwargs)
            roles = response.get("Roles", [])

            if not roles:
                return "No IAM roles found"

            result = "# IAM Roles\n\n"
            result += "| Role Name | Created | Description |\n"
            result += "|-----------|---------|-------------|\n"
            for r in sorted(roles, key=lambda x: x["RoleName"]):
                created = r["CreateDate"].strftime("%Y-%m-%d") if r.get("CreateDate") else "-"
                desc = r.get("Description", "-")
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                result += f"| {r['RoleName']} | {created} | {desc} |\n"

            result += f"\n**Total:** {len(roles)} role(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # Lambda Tools
    # =========================================================================

    @mcp.tool(
        name="aws_lambda_list_functions",
        annotations={
            "title": "List Lambda Functions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_lambda_list_functions(
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List all Lambda functions with runtime, memory, and last modified info."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            lam = aws_config.get_client("lambda", region)
            response = lam.list_functions()
            functions = response.get("Functions", [])

            if not functions:
                return f"No Lambda functions found in {region or aws_config.region}"

            result = f"# Lambda Functions ({region or aws_config.region})\n\n"
            result += "| Function Name | Runtime | Memory (MB) | Timeout (s) | Last Modified |\n"
            result += "|---------------|---------|-------------|-------------|---------------|\n"
            for fn in sorted(functions, key=lambda x: x["FunctionName"]):
                result += f"| {fn['FunctionName']} | {fn.get('Runtime', '-')} | {fn.get('MemorySize', '-')} | {fn.get('Timeout', '-')} | {fn.get('LastModified', '-')[:19]} |\n"

            result += f"\n**Total:** {len(functions)} function(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_lambda_get_function",
        annotations={
            "title": "Get Lambda Function Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_lambda_get_function(
        function_name: str = Field(..., description="Lambda function name or ARN"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Get detailed information about a Lambda function."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            lam = aws_config.get_client("lambda", region)
            response = lam.get_function(FunctionName=function_name)
            config = response["Configuration"]

            result = f"# Lambda: {config['FunctionName']}\n\n"
            result += f"**ARN:** `{config['FunctionArn']}`\n"
            result += f"**Runtime:** {config.get('Runtime', '-')}\n"
            result += f"**Handler:** {config.get('Handler', '-')}\n"
            result += f"**Memory:** {config.get('MemorySize', '-')} MB\n"
            result += f"**Timeout:** {config.get('Timeout', '-')} seconds\n"
            result += f"**Code Size:** {config.get('CodeSize', 0) / 1024:.1f} KB\n"
            result += f"**Last Modified:** {config.get('LastModified', '-')}\n"
            result += f"**State:** {config.get('State', '-')}\n"
            result += f"**Description:** {config.get('Description', '-')}\n"
            result += f"**Role:** `{config.get('Role', '-')}`\n"

            # VPC Config
            vpc = config.get("VpcConfig", {})
            if vpc.get("VpcId"):
                result += f"\n## VPC Configuration\n"
                result += f"- **VPC:** {vpc['VpcId']}\n"
                result += f"- **Subnets:** {', '.join(vpc.get('SubnetIds', []))}\n"
                result += f"- **Security Groups:** {', '.join(vpc.get('SecurityGroupIds', []))}\n"

            # Environment Variables (keys only for security)
            env = config.get("Environment", {}).get("Variables", {})
            if env:
                result += f"\n## Environment Variables ({len(env)} vars)\n"
                for key in sorted(env.keys()):
                    result += f"- `{key}`\n"

            # Tags
            tags = response.get("Tags", {})
            if tags:
                result += f"\n## Tags\n"
                for k, v in sorted(tags.items()):
                    result += f"- **{k}:** {v}\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_lambda_invoke",
        annotations={
            "title": "Invoke Lambda Function",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def aws_lambda_invoke(
        function_name: str = Field(..., description="Lambda function name or ARN"),
        payload: Optional[str] = Field(default=None, description="JSON payload to pass to the function"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Invoke a Lambda function and return the response."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            lam = aws_config.get_client("lambda", region)
            kwargs = {"FunctionName": function_name, "InvocationType": "RequestResponse"}
            if payload:
                kwargs["Payload"] = payload.encode("utf-8")

            response = lam.invoke(**kwargs)
            status = response["StatusCode"]
            response_payload = response["Payload"].read().decode("utf-8")

            result = f"# Lambda Invocation: {function_name}\n\n"
            result += f"**Status Code:** {status}\n"
            if response.get("FunctionError"):
                result += f"**Error:** {response['FunctionError']}\n"
            result += f"\n## Response\n```json\n{response_payload[:5000]}\n```"

            if len(response_payload) > 5000:
                result += f"\n*(Response truncated, total {len(response_payload)} chars)*"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # CloudWatch Tools
    # =========================================================================

    @mcp.tool(
        name="aws_cloudwatch_list_alarms",
        annotations={
            "title": "List CloudWatch Alarms",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_cloudwatch_list_alarms(
        region: Optional[str] = Field(default=None, description="AWS region"),
        state_filter: Optional[str] = Field(default=None, description="Filter by state: 'OK', 'ALARM', 'INSUFFICIENT_DATA'"),
    ) -> str:
        """List all CloudWatch alarms with their current state."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            cw = aws_config.get_client("cloudwatch", region)
            kwargs = {}
            if state_filter:
                kwargs["StateValue"] = state_filter

            response = cw.describe_alarms(**kwargs)
            alarms = response.get("MetricAlarms", [])

            if not alarms:
                return f"No CloudWatch alarms found in {region or aws_config.region}"

            result = f"# CloudWatch Alarms ({region or aws_config.region})\n\n"
            result += "| Alarm Name | State | Metric | Threshold | Namespace |\n"
            result += "|------------|-------|--------|-----------|----------|\n"
            for a in sorted(alarms, key=lambda x: x.get("StateValue", "")):
                name = a["AlarmName"]
                if len(name) > 40:
                    name = name[:37] + "..."
                state = a.get("StateValue", "-")
                metric = a.get("MetricName", "-")
                threshold = a.get("Threshold", "-")
                ns = a.get("Namespace", "-")
                result += f"| {name} | {state} | {metric} | {threshold} | {ns} |\n"

            result += f"\n**Total:** {len(alarms)} alarm(s)"
            alarm_count = sum(1 for a in alarms if a.get("StateValue") == "ALARM")
            if alarm_count > 0:
                result += f" ({alarm_count} in ALARM state)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_cloudwatch_get_metrics",
        annotations={
            "title": "Get CloudWatch Metrics",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_cloudwatch_get_metrics(
        namespace: str = Field(..., description="CloudWatch namespace (e.g., 'AWS/EC2', 'AWS/RDS', 'AWS/Lambda')"),
        metric_name: str = Field(..., description="Metric name (e.g., 'CPUUtilization', 'NetworkIn')"),
        dimension_name: str = Field(..., description="Dimension name (e.g., 'InstanceId', 'FunctionName')"),
        dimension_value: str = Field(..., description="Dimension value (e.g., 'i-0abc123', 'my-function')"),
        period: int = Field(default=300, description="Period in seconds (60, 300, 3600)"),
        hours: int = Field(default=1, description="Number of hours of data to retrieve (1-168)"),
        statistic: str = Field(default="Average", description="Statistic: 'Average', 'Sum', 'Maximum', 'Minimum', 'SampleCount'"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Get CloudWatch metric data for a specific resource."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            cw = aws_config.get_client("cloudwatch", region)
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=min(max(1, hours), 168))

            response = cw.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=[{"Name": dimension_name, "Value": dimension_value}],
                StartTime=start_time,
                EndTime=end_time,
                Period=period,
                Statistics=[statistic],
            )

            datapoints = sorted(response.get("Datapoints", []), key=lambda x: x["Timestamp"])

            if not datapoints:
                return f"No data points found for {namespace}/{metric_name}"

            result = f"# CloudWatch Metric: {metric_name}\n\n"
            result += f"**Namespace:** {namespace}\n"
            result += f"**{dimension_name}:** {dimension_value}\n"
            result += f"**Period:** {period}s | **Statistic:** {statistic}\n"
            result += f"**Time Range:** {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')} UTC\n\n"

            result += "| Timestamp | Value | Unit |\n"
            result += "|-----------|-------|------|\n"
            for dp in datapoints:
                ts = dp["Timestamp"].strftime("%Y-%m-%d %H:%M")
                val = dp.get(statistic, 0)
                unit = dp.get("Unit", "-")
                if isinstance(val, float):
                    result += f"| {ts} | {val:.2f} | {unit} |\n"
                else:
                    result += f"| {ts} | {val} | {unit} |\n"

            result += f"\n**Data points:** {len(datapoints)}"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # Cost Explorer Tools
    # =========================================================================

    @mcp.tool(
        name="aws_cost_get_summary",
        annotations={
            "title": "Get AWS Cost Summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_cost_get_summary(
        days: int = Field(default=30, description="Number of days to analyze (1-90)"),
        group_by: str = Field(default="SERVICE", description="Group costs by: 'SERVICE', 'REGION', 'LINKED_ACCOUNT', 'USAGE_TYPE'"),
    ) -> str:
        """Get AWS cost summary grouped by service, region, or account."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ce = aws_config.get_client("ce", "us-east-1")  # Cost Explorer is global (us-east-1)
            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start_date = (datetime.now(timezone.utc) - timedelta(days=min(max(1, days), 90))).strftime("%Y-%m-%d")

            response = ce.get_cost_and_usage(
                TimePeriod={"Start": start_date, "End": end_date},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": group_by}],
            )

            result = f"# AWS Cost Summary\n\n"
            result += f"**Period:** {start_date} to {end_date} ({days} days)\n"
            result += f"**Grouped by:** {group_by}\n\n"

            # Aggregate across time periods
            cost_by_group = {}
            for period in response.get("ResultsByTime", []):
                for group in period.get("Groups", []):
                    key = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    cost_by_group[key] = cost_by_group.get(key, 0) + amount

            if not cost_by_group:
                return result + "No cost data available for this period."

            result += f"| {group_by.title()} | Cost (USD) |\n"
            result += f"|{'---' * 10}|------------|\n"
            total = 0
            for key, cost in sorted(cost_by_group.items(), key=lambda x: x[1], reverse=True):
                if cost < 0.01:
                    continue
                total += cost
                result += f"| {key} | ${cost:,.2f} |\n"

            result += f"| **TOTAL** | **${total:,.2f}** |\n"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_cost_get_forecast",
        annotations={
            "title": "Get AWS Cost Forecast",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_cost_get_forecast(
        days: int = Field(default=30, description="Number of days to forecast (1-365)"),
    ) -> str:
        """Get AWS cost forecast for upcoming days."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ce = aws_config.get_client("ce", "us-east-1")
            start_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
            end_date = (datetime.now(timezone.utc) + timedelta(days=min(max(1, days), 365))).strftime("%Y-%m-%d")

            response = ce.get_cost_forecast(
                TimePeriod={"Start": start_date, "End": end_date},
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
            )

            result = f"# AWS Cost Forecast\n\n"
            result += f"**Forecast Period:** {start_date} to {end_date}\n\n"

            total = response.get("Total", {})
            if total:
                amount = float(total.get("Amount", 0))
                result += f"**Total Forecasted Cost:** ${amount:,.2f} {total.get('Unit', 'USD')}\n\n"

            periods = response.get("ForecastResultsByTime", [])
            if periods:
                result += "| Period | Forecasted Cost |\n"
                result += "|--------|----------------|\n"
                for p in periods:
                    start = p["TimePeriod"]["Start"]
                    end = p["TimePeriod"]["End"]
                    amount = float(p.get("MeanValue", 0))
                    result += f"| {start} to {end} | ${amount:,.2f} |\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # RDS Tools (Management - complements existing MySQL query tools)
    # =========================================================================

    @mcp.tool(
        name="aws_rds_list_instances",
        annotations={
            "title": "List RDS Instances",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_rds_list_instances(
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List all RDS database instances with engine, status, and size info."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            rds = aws_config.get_client("rds", region)
            response = rds.describe_db_instances()
            instances = response.get("DBInstances", [])

            if not instances:
                return f"No RDS instances found in {region or aws_config.region}"

            result = f"# RDS Instances ({region or aws_config.region})\n\n"
            result += "| DB ID | Engine | Class | Status | Storage | Multi-AZ | Endpoint |\n"
            result += "|-------|--------|-------|--------|---------|----------|----------|\n"
            for db in instances:
                endpoint = db.get("Endpoint", {}).get("Address", "-")
                if len(endpoint) > 40:
                    endpoint = endpoint[:37] + "..."
                engine = f"{db.get('Engine', '-')} {db.get('EngineVersion', '')}"
                result += (
                    f"| {db['DBInstanceIdentifier']} "
                    f"| {engine} "
                    f"| {db.get('DBInstanceClass', '-')} "
                    f"| {db.get('DBInstanceStatus', '-')} "
                    f"| {db.get('AllocatedStorage', '-')} GB "
                    f"| {'Yes' if db.get('MultiAZ') else 'No'} "
                    f"| {endpoint} |\n"
                )

            result += f"\n**Total:** {len(instances)} instance(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_rds_get_instance",
        annotations={
            "title": "Get RDS Instance Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_rds_get_instance(
        db_instance_id: str = Field(..., description="RDS DB instance identifier"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Get detailed information about an RDS instance."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            rds = aws_config.get_client("rds", region)
            response = rds.describe_db_instances(DBInstanceIdentifier=db_instance_id)
            instances = response.get("DBInstances", [])

            if not instances:
                return f"RDS instance '{db_instance_id}' not found"

            db = instances[0]
            endpoint = db.get("Endpoint", {})

            result = f"# RDS Instance: {db['DBInstanceIdentifier']}\n\n"
            result += f"**Engine:** {db.get('Engine', '-')} {db.get('EngineVersion', '')}\n"
            result += f"**Class:** {db.get('DBInstanceClass', '-')}\n"
            result += f"**Status:** {db.get('DBInstanceStatus', '-')}\n"
            result += f"**Endpoint:** {endpoint.get('Address', '-')}:{endpoint.get('Port', '-')}\n"
            result += f"**Storage:** {db.get('AllocatedStorage', '-')} GB ({db.get('StorageType', '-')})\n"
            result += f"**Multi-AZ:** {'Yes' if db.get('MultiAZ') else 'No'}\n"
            result += f"**Availability Zone:** {db.get('AvailabilityZone', '-')}\n"
            result += f"**VPC:** {db.get('DBSubnetGroup', {}).get('VpcId', '-')}\n"
            result += f"**Publicly Accessible:** {'Yes' if db.get('PubliclyAccessible') else 'No'}\n"
            result += f"**Encrypted:** {'Yes' if db.get('StorageEncrypted') else 'No'}\n"
            result += f"**Backup Retention:** {db.get('BackupRetentionPeriod', '-')} days\n"
            result += f"**Auto Minor Version Upgrade:** {'Yes' if db.get('AutoMinorVersionUpgrade') else 'No'}\n"
            result += f"**Created:** {db.get('InstanceCreateTime', '-')}\n"

            # Security Groups
            sgs = db.get("VpcSecurityGroups", [])
            if sgs:
                result += f"\n## Security Groups\n"
                for sg in sgs:
                    result += f"- `{sg['VpcSecurityGroupId']}` ({sg['Status']})\n"

            # Parameter Group
            pgs = db.get("DBParameterGroups", [])
            if pgs:
                result += f"\n## Parameter Groups\n"
                for pg in pgs:
                    result += f"- {pg['DBParameterGroupName']} ({pg['ParameterApplyStatus']})\n"

            # Tags
            tags = db.get("TagList", [])
            if tags:
                result += f"\n## Tags\n"
                for tag in tags:
                    result += f"- **{tag['Key']}:** {tag['Value']}\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # ECS Tools
    # =========================================================================

    @mcp.tool(
        name="aws_ecs_list_clusters",
        annotations={
            "title": "List ECS Clusters",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ecs_list_clusters(
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List all ECS clusters with status and service counts."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ecs = aws_config.get_client("ecs", region)
            arns_response = ecs.list_clusters()
            cluster_arns = arns_response.get("clusterArns", [])

            if not cluster_arns:
                return f"No ECS clusters found in {region or aws_config.region}"

            response = ecs.describe_clusters(clusters=cluster_arns, include=["STATISTICS"])
            clusters = response.get("clusters", [])

            result = f"# ECS Clusters ({region or aws_config.region})\n\n"
            result += "| Cluster | Status | Services | Tasks (Running/Pending) | Instances |\n"
            result += "|---------|--------|----------|------------------------|-----------|\n"
            for c in clusters:
                result += (
                    f"| {c['clusterName']} "
                    f"| {c['status']} "
                    f"| {c.get('activeServicesCount', 0)} "
                    f"| {c.get('runningTasksCount', 0)}/{c.get('pendingTasksCount', 0)} "
                    f"| {c.get('registeredContainerInstancesCount', 0)} |\n"
                )

            result += f"\n**Total:** {len(clusters)} cluster(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_ecs_list_services",
        annotations={
            "title": "List ECS Services",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ecs_list_services(
        cluster: str = Field(..., description="ECS cluster name or ARN"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List all services in an ECS cluster."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ecs = aws_config.get_client("ecs", region)
            arns_response = ecs.list_services(cluster=cluster)
            service_arns = arns_response.get("serviceArns", [])

            if not service_arns:
                return f"No services found in cluster '{cluster}'"

            response = ecs.describe_services(cluster=cluster, services=service_arns)
            services = response.get("services", [])

            result = f"# ECS Services in {cluster}\n\n"
            result += "| Service | Status | Desired | Running | Pending | Launch Type |\n"
            result += "|---------|--------|---------|---------|---------|-------------|\n"
            for s in services:
                result += (
                    f"| {s['serviceName']} "
                    f"| {s['status']} "
                    f"| {s.get('desiredCount', 0)} "
                    f"| {s.get('runningCount', 0)} "
                    f"| {s.get('pendingCount', 0)} "
                    f"| {s.get('launchType', '-')} |\n"
                )

            result += f"\n**Total:** {len(services)} service(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_ecs_list_tasks",
        annotations={
            "title": "List ECS Tasks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ecs_list_tasks(
        cluster: str = Field(..., description="ECS cluster name or ARN"),
        service: Optional[str] = Field(default=None, description="Filter by service name"),
        status: str = Field(default="RUNNING", description="Task status: 'RUNNING', 'STOPPED'"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List tasks in an ECS cluster, optionally filtered by service."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            ecs = aws_config.get_client("ecs", region)
            kwargs = {"cluster": cluster, "desiredStatus": status}
            if service:
                kwargs["serviceName"] = service

            arns_response = ecs.list_tasks(**kwargs)
            task_arns = arns_response.get("taskArns", [])

            if not task_arns:
                return f"No {status.lower()} tasks found in cluster '{cluster}'"

            response = ecs.describe_tasks(cluster=cluster, tasks=task_arns)
            tasks = response.get("tasks", [])

            result = f"# ECS Tasks in {cluster}\n\n"
            result += "| Task ID | Task Definition | Status | CPU | Memory | Started |\n"
            result += "|---------|-----------------|--------|-----|--------|---------|\n"
            for t in tasks:
                task_id = t["taskArn"].split("/")[-1]
                td = t.get("taskDefinitionArn", "-").split("/")[-1]
                started = t.get("startedAt", "")
                started_str = started.strftime("%Y-%m-%d %H:%M") if hasattr(started, "strftime") else str(started)[:16]
                result += (
                    f"| {task_id[:12]}... "
                    f"| {td} "
                    f"| {t.get('lastStatus', '-')} "
                    f"| {t.get('cpu', '-')} "
                    f"| {t.get('memory', '-')} MB "
                    f"| {started_str} |\n"
                )

            result += f"\n**Total:** {len(tasks)} task(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # Route53 Tools
    # =========================================================================

    @mcp.tool(
        name="aws_route53_list_hosted_zones",
        annotations={
            "title": "List Route53 Hosted Zones",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_route53_list_hosted_zones() -> str:
        """List all Route53 hosted zones (DNS zones)."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            r53 = aws_config.get_client("route53")
            response = r53.list_hosted_zones()
            zones = response.get("HostedZones", [])

            if not zones:
                return "No Route53 hosted zones found"

            result = "# Route53 Hosted Zones\n\n"
            result += "| Name | Type | Record Count | ID |\n"
            result += "|------|------|-------------|----|\n"
            for z in zones:
                zone_id = z["Id"].split("/")[-1]
                zone_type = "Private" if z.get("Config", {}).get("PrivateZone") else "Public"
                result += f"| {z['Name']} | {zone_type} | {z.get('ResourceRecordSetCount', 0)} | {zone_id} |\n"

            result += f"\n**Total:** {len(zones)} zone(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_route53_list_records",
        annotations={
            "title": "List DNS Records",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_route53_list_records(
        hosted_zone_id: str = Field(..., description="Hosted zone ID (e.g., 'Z1234567890')"),
        record_type: Optional[str] = Field(default=None, description="Filter by type: 'A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SOA'"),
        max_items: int = Field(default=100, description="Maximum number of records to return"),
    ) -> str:
        """List DNS records in a Route53 hosted zone."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            r53 = aws_config.get_client("route53")
            kwargs = {"HostedZoneId": hosted_zone_id, "MaxItems": str(min(max(1, max_items), 300))}

            response = r53.list_resource_record_sets(**kwargs)
            records = response.get("ResourceRecordSets", [])

            if record_type:
                records = [r for r in records if r["Type"] == record_type.upper()]

            if not records:
                return f"No DNS records found in zone {hosted_zone_id}"

            result = f"# DNS Records ({hosted_zone_id})\n\n"
            result += "| Name | Type | TTL | Values |\n"
            result += "|------|------|-----|--------|\n"
            for r in records:
                name = r["Name"]
                rtype = r["Type"]
                ttl = r.get("TTL", "-")

                if r.get("AliasTarget"):
                    values = f"ALIAS -> {r['AliasTarget'].get('DNSName', '-')}"
                else:
                    vals = [rr.get("Value", "") for rr in r.get("ResourceRecords", [])]
                    values = ", ".join(vals)
                    if len(values) > 60:
                        values = values[:57] + "..."

                result += f"| {name} | {rtype} | {ttl} | {values} |\n"

            result += f"\n**Total:** {len(records)} record(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # CloudFormation Tools
    # =========================================================================

    @mcp.tool(
        name="aws_cloudformation_list_stacks",
        annotations={
            "title": "List CloudFormation Stacks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_cloudformation_list_stacks(
        region: Optional[str] = Field(default=None, description="AWS region"),
        status_filter: Optional[str] = Field(
            default=None,
            description="Filter by status: 'CREATE_COMPLETE', 'UPDATE_COMPLETE', 'DELETE_COMPLETE', etc.",
        ),
    ) -> str:
        """List all CloudFormation stacks with status."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            cf = aws_config.get_client("cloudformation", region)
            kwargs = {}
            if status_filter:
                kwargs["StackStatusFilter"] = [status_filter]

            response = cf.list_stacks(**kwargs)
            stacks = response.get("StackSummaries", [])

            # Filter out deleted stacks by default
            if not status_filter:
                stacks = [s for s in stacks if "DELETE" not in s.get("StackStatus", "")]

            if not stacks:
                return f"No CloudFormation stacks found in {region or aws_config.region}"

            result = f"# CloudFormation Stacks ({region or aws_config.region})\n\n"
            result += "| Stack Name | Status | Created | Updated |\n"
            result += "|------------|--------|---------|----------|\n"
            for s in stacks:
                created = s.get("CreationTime", "").strftime("%Y-%m-%d") if s.get("CreationTime") else "-"
                updated = s.get("LastUpdatedTime", "").strftime("%Y-%m-%d") if s.get("LastUpdatedTime") else "-"
                result += f"| {s['StackName']} | {s['StackStatus']} | {created} | {updated} |\n"

            result += f"\n**Total:** {len(stacks)} stack(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    @mcp.tool(
        name="aws_cloudformation_get_stack",
        annotations={
            "title": "Get CloudFormation Stack Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_cloudformation_get_stack(
        stack_name: str = Field(..., description="Stack name or ID"),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Get detailed information about a CloudFormation stack including outputs and parameters."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            cf = aws_config.get_client("cloudformation", region)
            response = cf.describe_stacks(StackName=stack_name)
            stacks = response.get("Stacks", [])

            if not stacks:
                return f"Stack '{stack_name}' not found"

            stack = stacks[0]
            result = f"# CloudFormation Stack: {stack['StackName']}\n\n"
            result += f"**Stack ID:** `{stack['StackId']}`\n"
            result += f"**Status:** {stack['StackStatus']}\n"
            result += f"**Created:** {stack.get('CreationTime', '-')}\n"
            result += f"**Updated:** {stack.get('LastUpdatedTime', '-')}\n"
            result += f"**Description:** {stack.get('Description', '-')}\n"

            if stack.get("StackStatusReason"):
                result += f"**Status Reason:** {stack['StackStatusReason']}\n"

            # Parameters
            params = stack.get("Parameters", [])
            if params:
                result += f"\n## Parameters\n"
                for p in params:
                    val = "****" if p.get("ParameterValue") and "password" in p["ParameterKey"].lower() else p.get("ParameterValue", "-")
                    result += f"- **{p['ParameterKey']}:** {val}\n"

            # Outputs
            outputs = stack.get("Outputs", [])
            if outputs:
                result += f"\n## Outputs\n"
                for o in outputs:
                    result += f"- **{o['OutputKey']}:** {o.get('OutputValue', '-')}\n"
                    if o.get("Description"):
                        result += f"  _{o['Description']}_\n"

            # Tags
            tags = stack.get("Tags", [])
            if tags:
                result += f"\n## Tags\n"
                for tag in tags:
                    result += f"- **{tag['Key']}:** {tag['Value']}\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # SNS Tools
    # =========================================================================

    @mcp.tool(
        name="aws_sns_list_topics",
        annotations={
            "title": "List SNS Topics",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_sns_list_topics(
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List all SNS topics."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            sns = aws_config.get_client("sns", region)
            response = sns.list_topics()
            topics = response.get("Topics", [])

            if not topics:
                return f"No SNS topics found in {region or aws_config.region}"

            result = f"# SNS Topics ({region or aws_config.region})\n\n"
            for t in topics:
                arn = t["TopicArn"]
                name = arn.split(":")[-1]
                result += f"- **{name}**\n  ARN: `{arn}`\n"

            result += f"\n**Total:** {len(topics)} topic(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # SQS Tools
    # =========================================================================

    @mcp.tool(
        name="aws_sqs_list_queues",
        annotations={
            "title": "List SQS Queues",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_sqs_list_queues(
        region: Optional[str] = Field(default=None, description="AWS region"),
        prefix: Optional[str] = Field(default=None, description="Queue name prefix to filter"),
    ) -> str:
        """List all SQS queues with message counts."""
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
        try:
            sqs = aws_config.get_client("sqs", region)
            kwargs = {}
            if prefix:
                kwargs["QueueNamePrefix"] = prefix

            response = sqs.list_queues(**kwargs)
            queue_urls = response.get("QueueUrls", [])

            if not queue_urls:
                return f"No SQS queues found in {region or aws_config.region}"

            result = f"# SQS Queues ({region or aws_config.region})\n\n"
            result += "| Queue Name | Messages Available | In Flight | URL |\n"
            result += "|------------|-------------------|-----------|-----|\n"

            for url in queue_urls:
                name = url.split("/")[-1]
                try:
                    attrs = sqs.get_queue_attributes(
                        QueueUrl=url,
                        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
                    )
                    available = attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", "?")
                    in_flight = attrs.get("Attributes", {}).get("ApproximateNumberOfMessagesNotVisible", "?")
                except Exception:
                    available = "?"
                    in_flight = "?"

                short_url = url
                if len(short_url) > 60:
                    short_url = "..." + short_url[-57:]
                result += f"| {name} | {available} | {in_flight} | {short_url} |\n"

            result += f"\n**Total:** {len(queue_urls)} queue(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    print("AWS tools registered successfully")
