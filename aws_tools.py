"""
AWS Integration Tools for Crowd IT MCP Server

Multi-account AWS management with cross-account role assumption.

Accounts:
    - prod (default): optiq.prod (979437352159) — base IAM user, no role assumption
    - nonprod: optiq.nonprod (886331869150) — assumes AWS_ROLE_ARN_NONPROD
    - admin: optiq.admin (816069165718) — assumes AWS_ROLE_ARN_ADMIN

Credentials (Google Secret Manager, project crowdmcp):
    AWS_ACCESS_KEY_ID: Base IAM user access key (home account)
    AWS_SECRET_ACCESS_KEY: Base IAM user secret key
    AWS_DEFAULT_REGION: Default region (ap-southeast-2)
    AWS_ROLE_ARN_NONPROD: Role ARN for optiq.nonprod
    AWS_ROLE_ARN_ADMIN: Role ARN for optiq.admin

Requirements:
    pip install boto3
"""

import os
import json
import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from pydantic import Field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration and Multi-Account Authentication
# =============================================================================

class AWSConfig:
    """AWS multi-account configuration with cross-account role assumption.

    Uses a base IAM user in the 'prod' (home) account. For 'nonprod' and
    'admin' accounts, assumes the corresponding role ARN via STS to get
    temporary credentials. Assumed-role sessions are cached and refreshed
    when they expire (default 1 hour).
    """

    ACCOUNT_MAP = {
        "prod": {"name": "optiq.prod", "id": "979437352159"},
        "nonprod": {"name": "optiq.nonprod", "id": "886331869150"},
        "admin": {"name": "optiq.admin", "id": "816069165718"},
    }

    def __init__(self):
        self.access_key_id = ""
        self.secret_access_key = ""
        self.region = ""
        self.role_arn_nonprod = ""
        self.role_arn_admin = ""
        # Cache for assumed-role sessions: {account: {"credentials": {...}, "expiry": datetime}}
        self._session_cache: Dict[str, Dict[str, Any]] = {}
        self._load_credentials()

    def _load_credentials(self):
        """Load credentials from environment variables, falling back to Secret Manager."""
        from app.core.config import get_secret_sync

        self.access_key_id = os.getenv("AWS_ACCESS_KEY_ID", "") or get_secret_sync("AWS_ACCESS_KEY_ID") or ""
        self.secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY", "") or get_secret_sync("AWS_SECRET_ACCESS_KEY") or ""
        self.region = os.getenv("AWS_DEFAULT_REGION", "") or get_secret_sync("AWS_DEFAULT_REGION") or "ap-southeast-2"
        self.role_arn_nonprod = os.getenv("AWS_ROLE_ARN_NONPROD", "") or get_secret_sync("AWS_ROLE_ARN_NONPROD") or ""
        self.role_arn_admin = os.getenv("AWS_ROLE_ARN_ADMIN", "") or get_secret_sync("AWS_ROLE_ARN_ADMIN") or ""

    @property
    def is_configured(self) -> bool:
        return bool(self.access_key_id) and bool(self.secret_access_key)

    def _get_base_session(self):
        """Get a boto3 session with base IAM user credentials (prod account)."""
        import boto3
        return boto3.Session(
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
        )

    def _get_role_arn(self, account: str) -> Optional[str]:
        """Get the role ARN for a given account alias."""
        if account == "nonprod":
            return self.role_arn_nonprod
        elif account == "admin":
            return self.role_arn_admin
        return None  # prod uses base creds

    def _assume_role(self, role_arn: str, session_name: str = "crowdit-mcp") -> Dict[str, Any]:
        """Assume a cross-account role and return temporary credentials."""
        base_session = self._get_base_session()
        sts = base_session.client("sts")
        response = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name,
            DurationSeconds=3600,
        )
        return response["Credentials"]

    def get_session(self, account: str = "prod"):
        """Get a boto3 session for the specified account.

        For 'prod', returns a session with base IAM credentials.
        For 'nonprod' or 'admin', assumes the corresponding role and caches
        the temporary session. Refreshes automatically when expired.
        """
        import boto3

        account = (account or "prod").lower().strip()
        if account not in self.ACCOUNT_MAP:
            raise ValueError(f"Unknown account '{account}'. Use: prod, nonprod, admin")

        # Prod uses base credentials directly
        if account == "prod":
            return self._get_base_session()

        # Check cache for assumed-role sessions
        cached = self._session_cache.get(account)
        if cached:
            # Refresh if within 5 minutes of expiry
            expiry = cached["expiry"]
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < expiry - timedelta(minutes=5):
                return cached["session"]

        # Assume role for this account
        role_arn = self._get_role_arn(account)
        if not role_arn:
            raise ValueError(f"No role ARN configured for account '{account}'. Set AWS_ROLE_ARN_{account.upper()} environment variable.")

        creds = self._assume_role(role_arn, session_name=f"crowdit-mcp-{account}")
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=self.region,
        )

        # Cache the session
        self._session_cache[account] = {
            "session": session,
            "expiry": creds["Expiration"],
        }

        return session

    def get_client(self, service_name: str, account: str = "prod", region: str = None):
        """Get a boto3 client for the specified service and account."""
        session = self.get_session(account)
        return session.client(service_name, region_name=region or self.region)

    def get_account_label(self, account: str = "prod") -> str:
        """Get a human-readable label for the account."""
        account = (account or "prod").lower().strip()
        info = self.ACCOUNT_MAP.get(account, {})
        return f"{info.get('name', account)} ({info.get('id', '?')})"


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
    if isinstance(e, ValueError):
        return f"Error: {str(e)}"
    return f"Error: {type(e).__name__}: {str(e)}"


# Account parameter description used by all tools
ACCOUNT_DESC = "AWS account: 'prod' (default, optiq.prod 979437352159), 'nonprod' (optiq.nonprod 886331869150), or 'admin' (optiq.admin 816069165718)"


# =============================================================================
# Tool Registration
# =============================================================================

def register_aws_tools(mcp, aws_config: AWSConfig):
    """Register all AWS tools with the MCP server."""

    # =========================================================================
    # aws_list_ec2_instances
    # =========================================================================

    @mcp.tool(
        name="aws_list_ec2_instances",
        annotations={
            "title": "List EC2 Instances",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_ec2_instances(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region (uses default ap-southeast-2 if not provided)"),
        state_filter: Optional[str] = Field(default=None, description="Filter by state: 'running', 'stopped', 'terminated', 'all'"),
    ) -> str:
        """List EC2 instances with name, state, type, and IPs.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            ec2 = aws_config.get_client("ec2", account=account, region=region)
            filters = []
            if state_filter and state_filter != "all":
                filters.append({"Name": "instance-state-name", "Values": [state_filter]})

            kwargs = {}
            if filters:
                kwargs["Filters"] = filters

            paginator = ec2.get_paginator("describe_instances")
            instances = []
            for page in paginator.paginate(**kwargs):
                for reservation in page["Reservations"]:
                    for inst in reservation["Instances"]:
                        name = ""
                        for tag in inst.get("Tags", []):
                            if tag["Key"] == "Name":
                                name = tag["Value"]
                                break
                        instances.append({
                            "id": inst["InstanceId"],
                            "name": name,
                            "type": inst["InstanceType"],
                            "state": inst["State"]["Name"],
                            "private_ip": inst.get("PrivateIpAddress", "-"),
                            "public_ip": inst.get("PublicIpAddress", "-"),
                            "az": inst["Placement"]["AvailabilityZone"],
                        })

            acct_label = aws_config.get_account_label(account)
            rgn = region or aws_config.region

            if not instances:
                return f"No EC2 instances found in {acct_label} ({rgn})"

            result = f"# EC2 Instances — {acct_label}\n**Region:** {rgn}\n\n"
            result += "| Name | Instance ID | Type | State | Private IP | Public IP | AZ |\n"
            result += "|------|-------------|------|-------|------------|-----------|----|\n"
            for inst in instances:
                result += f"| {inst['name'] or '-'} | {inst['id']} | {inst['type']} | {inst['state']} | {inst['private_ip']} | {inst['public_ip']} | {inst['az']} |\n"

            result += f"\n**Total:** {len(instances)} instance(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_ec2_action
    # =========================================================================

    @mcp.tool(
        name="aws_ec2_action",
        annotations={
            "title": "EC2 Instance Action",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_ec2_action(
        instance_ids: str = Field(..., description="Comma-separated instance IDs (e.g., 'i-0abc123,i-0def456')"),
        action: str = Field(..., description="Action: 'start', 'stop', 'reboot'"),
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """Start, stop, or reboot EC2 instances.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            ec2 = aws_config.get_client("ec2", account=account, region=region)
            ids = [i.strip() for i in instance_ids.split(",") if i.strip()]
            action_lower = action.lower()
            acct_label = aws_config.get_account_label(account)

            if action_lower == "start":
                ec2.start_instances(InstanceIds=ids)
                return f"Starting {len(ids)} instance(s) in {acct_label}: {', '.join(ids)}\n\nUse aws_list_ec2_instances to check status."
            elif action_lower == "stop":
                ec2.stop_instances(InstanceIds=ids)
                return f"Stopping {len(ids)} instance(s) in {acct_label}: {', '.join(ids)}\n\nUse aws_list_ec2_instances to check status."
            elif action_lower == "reboot":
                ec2.reboot_instances(InstanceIds=ids)
                return f"Rebooting {len(ids)} instance(s) in {acct_label}: {', '.join(ids)}\n\nUse aws_list_ec2_instances to check status."
            else:
                return f"Error: Invalid action '{action}'. Use: start, stop, reboot"
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_list_rds_instances
    # =========================================================================

    @mcp.tool(
        name="aws_list_rds_instances",
        annotations={
            "title": "List RDS Instances",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_rds_instances(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List all RDS database instances with engine, status, and size info.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            rds = aws_config.get_client("rds", account=account, region=region)
            paginator = rds.get_paginator("describe_db_instances")
            instances = []
            for page in paginator.paginate():
                instances.extend(page.get("DBInstances", []))

            acct_label = aws_config.get_account_label(account)
            rgn = region or aws_config.region

            if not instances:
                return f"No RDS instances found in {acct_label} ({rgn})"

            result = f"# RDS Instances — {acct_label}\n**Region:** {rgn}\n\n"
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

    # =========================================================================
    # aws_list_s3_buckets
    # =========================================================================

    @mcp.tool(
        name="aws_list_s3_buckets",
        annotations={
            "title": "List S3 Buckets",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_s3_buckets(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
    ) -> str:
        """List all S3 buckets in an AWS account.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            s3 = aws_config.get_client("s3", account=account)
            response = s3.list_buckets()
            buckets = response.get("Buckets", [])
            acct_label = aws_config.get_account_label(account)

            if not buckets:
                return f"No S3 buckets found in {acct_label}"

            result = f"# S3 Buckets — {acct_label}\n\n"
            result += "| Bucket Name | Created |\n"
            result += "|-------------|----------|\n"
            for b in sorted(buckets, key=lambda x: x["Name"]):
                created = b["CreationDate"].strftime("%Y-%m-%d %H:%M") if b.get("CreationDate") else "-"
                result += f"| {b['Name']} | {created} |\n"

            result += f"\n**Total:** {len(buckets)} bucket(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_list_vpcs
    # =========================================================================

    @mcp.tool(
        name="aws_list_vpcs",
        annotations={
            "title": "List VPCs and Subnets",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_vpcs(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
        include_subnets: bool = Field(default=True, description="Include subnets for each VPC"),
    ) -> str:
        """List VPCs and their subnets.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            ec2 = aws_config.get_client("ec2", account=account, region=region)
            acct_label = aws_config.get_account_label(account)
            rgn = region or aws_config.region

            vpcs = ec2.describe_vpcs().get("Vpcs", [])
            if not vpcs:
                return f"No VPCs found in {acct_label} ({rgn})"

            # Pre-fetch subnets if requested
            subnets_by_vpc: Dict[str, list] = {}
            if include_subnets:
                all_subnets = ec2.describe_subnets().get("Subnets", [])
                for s in all_subnets:
                    subnets_by_vpc.setdefault(s["VpcId"], []).append(s)

            result = f"# VPCs — {acct_label}\n**Region:** {rgn}\n\n"
            for vpc in vpcs:
                name = ""
                for tag in vpc.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break

                result += f"## {name or vpc['VpcId']}\n"
                result += f"- **VPC ID:** `{vpc['VpcId']}`\n"
                result += f"- **CIDR:** {vpc['CidrBlock']}\n"
                result += f"- **State:** {vpc['State']}\n"
                result += f"- **Default:** {'Yes' if vpc.get('IsDefault') else 'No'}\n"

                if include_subnets:
                    subs = subnets_by_vpc.get(vpc["VpcId"], [])
                    if subs:
                        result += f"- **Subnets ({len(subs)}):**\n"
                        for s in sorted(subs, key=lambda x: x.get("AvailabilityZone", "")):
                            sname = ""
                            for tag in s.get("Tags", []):
                                if tag["Key"] == "Name":
                                    sname = tag["Value"]
                                    break
                            pub = " (public)" if s.get("MapPublicIpOnLaunch") else ""
                            result += f"  - `{s['SubnetId']}` {sname} — {s['CidrBlock']} ({s['AvailabilityZone']}, {s['AvailableIpAddressCount']} IPs free){pub}\n"

                result += "\n"

            result += f"**Total:** {len(vpcs)} VPC(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_get_cost_summary
    # =========================================================================

    @mcp.tool(
        name="aws_get_cost_summary",
        annotations={
            "title": "Get AWS Cost Summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_get_cost_summary(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        days: int = Field(default=30, description="Number of days to analyze (1-90)"),
        group_by: str = Field(default="SERVICE", description="Group by: 'SERVICE', 'REGION', 'LINKED_ACCOUNT', 'USAGE_TYPE'"),
    ) -> str:
        """Get AWS cost summary for the last N days, grouped by service.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        Note: Cost Explorer must be enabled in the target account.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            # Cost Explorer endpoint is always us-east-1
            ce = aws_config.get_client("ce", account=account, region="us-east-1")
            acct_label = aws_config.get_account_label(account)

            end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start_date = (datetime.now(timezone.utc) - timedelta(days=min(max(1, days), 90))).strftime("%Y-%m-%d")

            response = ce.get_cost_and_usage(
                TimePeriod={"Start": start_date, "End": end_date},
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": group_by}],
            )

            result = f"# AWS Cost Summary — {acct_label}\n\n"
            result += f"**Period:** {start_date} to {end_date} ({days} days)\n"
            result += f"**Grouped by:** {group_by}\n\n"

            # Aggregate across time periods
            cost_by_group: Dict[str, float] = {}
            for period in response.get("ResultsByTime", []):
                for group in period.get("Groups", []):
                    key = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    cost_by_group[key] = cost_by_group.get(key, 0) + amount

            if not cost_by_group:
                return result + "No cost data available for this period."

            result += f"| {group_by.replace('_', ' ').title()} | Cost (USD) |\n"
            result += f"|{'-' * 30}|------------|\n"
            total = 0.0
            for key, cost in sorted(cost_by_group.items(), key=lambda x: x[1], reverse=True):
                if cost < 0.01:
                    continue
                total += cost
                result += f"| {key} | ${cost:,.2f} |\n"

            result += f"| **TOTAL** | **${total:,.2f}** |\n"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_run_command — generic catch-all (like gcp_gcloud)
    # =========================================================================

    @mcp.tool(
        name="aws_run_command",
        annotations={
            "title": "Run AWS CLI Command",
            "readOnlyHint": False,
            "destructiveHint": False,
            "openWorldHint": True,
        },
    )
    async def aws_run_command(
        command: str = Field(..., description="AWS CLI command to execute (without 'aws' prefix). E.g., 'ec2 describe-instances --filters Name=tag:Name,Values=web'"),
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region override"),
        timeout_seconds: int = Field(default=120, description="Command timeout in seconds (max 300)"),
    ) -> str:
        """Execute any AWS CLI command. Full catch-all for operations not covered by specific tools.

        The command runs with credentials for the selected account (role assumption
        is handled automatically for nonprod/admin).

        Examples:
        - ec2 describe-instances --filters Name=tag:Name,Values=web
        - s3 ls s3://my-bucket/prefix/
        - ecs describe-services --cluster my-cluster --services my-service
        - lambda get-function --function-name my-func
        - cloudformation describe-stacks
        - logs filter-log-events --log-group-name /aws/lambda/my-func --limit 20
        - ssm describe-instance-information
        - route53 list-hosted-zones
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."

        try:
            # Get credentials for the target account
            session = aws_config.get_session(account)
            creds = session.get_credentials().get_frozen_credentials()
            acct_label = aws_config.get_account_label(account)
            rgn = region or aws_config.region

            timeout_seconds = min(max(10, timeout_seconds), 300)

            # Build environment with credentials
            env = os.environ.copy()
            env["AWS_ACCESS_KEY_ID"] = creds.access_key
            env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
            if creds.token:
                env["AWS_SESSION_TOKEN"] = creds.token
            env["AWS_DEFAULT_REGION"] = rgn

            full_command = f"aws {command} --output json --region {rgn}"

            proc = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)

            stdout_text = stdout.decode("utf-8").strip() if stdout else ""
            stderr_text = stderr.decode("utf-8").strip() if stderr else ""

            if proc.returncode != 0:
                error_msg = stderr_text or stdout_text or f"Command failed with exit code {proc.returncode}"
                return f"**Error running AWS CLI** ({acct_label}):\n```\n{error_msg}\n```"

            if not stdout_text:
                return f"Command completed successfully in {acct_label} (no output)."

            # Format JSON output
            try:
                data = json.loads(stdout_text)
                formatted = json.dumps(data, indent=2)
                # Truncate very large output
                if len(formatted) > 15000:
                    formatted = formatted[:15000] + "\n... (truncated)"
                return f"**Account:** {acct_label}\n```json\n{formatted}\n```"
            except json.JSONDecodeError:
                output = stdout_text[:15000]
                if len(stdout_text) > 15000:
                    output += "\n... (truncated)"
                return f"**Account:** {acct_label}\n```\n{output}\n```"

        except asyncio.TimeoutError:
            return f"Error: Command timed out after {timeout_seconds} seconds."
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_get_caller_identity — useful for verifying which account/role is active
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
    async def aws_get_caller_identity(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
    ) -> str:
        """Verify which AWS account and identity is active.

        Useful for confirming credentials work and role assumption is correct.
        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            sts = aws_config.get_client("sts", account=account)
            identity = sts.get_caller_identity()
            acct_label = aws_config.get_account_label(account)
            return (
                f"# AWS Caller Identity — {acct_label}\n\n"
                f"**Account:** {identity['Account']}\n"
                f"**ARN:** `{identity['Arn']}`\n"
                f"**User ID:** {identity['UserId']}\n"
                f"**Region:** {aws_config.region}"
            )
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_list_security_groups
    # =========================================================================

    @mcp.tool(
        name="aws_list_security_groups",
        annotations={
            "title": "List Security Groups",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_security_groups(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
        vpc_id: Optional[str] = Field(default=None, description="Filter by VPC ID"),
    ) -> str:
        """List security groups with inbound/outbound rules.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            ec2 = aws_config.get_client("ec2", account=account, region=region)
            acct_label = aws_config.get_account_label(account)
            filters = []
            if vpc_id:
                filters.append({"Name": "vpc-id", "Values": [vpc_id]})

            kwargs = {}
            if filters:
                kwargs["Filters"] = filters

            response = ec2.describe_security_groups(**kwargs)
            sgs = response.get("SecurityGroups", [])

            if not sgs:
                return f"No security groups found in {acct_label}"

            result = f"# Security Groups — {acct_label}\n\n"
            for sg in sgs:
                result += f"## {sg['GroupName']} (`{sg['GroupId']}`)\n"
                result += f"- **VPC:** {sg.get('VpcId', '-')}\n"
                result += f"- **Description:** {sg.get('Description', '-')}\n"

                if sg.get("IpPermissions"):
                    result += "- **Inbound:**\n"
                    for rule in sg["IpPermissions"]:
                        proto = rule.get("IpProtocol", "-")
                        from_port = rule.get("FromPort", "All")
                        to_port = rule.get("ToPort", "All")
                        port_range = f"{from_port}-{to_port}" if from_port != to_port else str(from_port)
                        if proto == "-1":
                            proto, port_range = "All", "All"
                        sources = [r["CidrIp"] for r in rule.get("IpRanges", [])]
                        sources += [r["GroupId"] for r in rule.get("UserIdGroupPairs", [])]
                        result += f"  - {proto} port {port_range} from {', '.join(sources) or 'N/A'}\n"

                if sg.get("IpPermissionsEgress"):
                    result += "- **Outbound:**\n"
                    for rule in sg["IpPermissionsEgress"]:
                        proto = rule.get("IpProtocol", "-")
                        from_port = rule.get("FromPort", "All")
                        to_port = rule.get("ToPort", "All")
                        port_range = f"{from_port}-{to_port}" if from_port != to_port else str(from_port)
                        if proto == "-1":
                            proto, port_range = "All", "All"
                        targets = [r["CidrIp"] for r in rule.get("IpRanges", [])]
                        result += f"  - {proto} port {port_range} to {', '.join(targets) or 'All'}\n"

                result += "\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_list_lambda_functions
    # =========================================================================

    @mcp.tool(
        name="aws_list_lambda_functions",
        annotations={
            "title": "List Lambda Functions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_lambda_functions(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List Lambda functions with runtime, memory, and last modified.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            lam = aws_config.get_client("lambda", account=account, region=region)
            acct_label = aws_config.get_account_label(account)

            paginator = lam.get_paginator("list_functions")
            functions = []
            for page in paginator.paginate():
                functions.extend(page.get("Functions", []))

            if not functions:
                return f"No Lambda functions found in {acct_label} ({region or aws_config.region})"

            result = f"# Lambda Functions — {acct_label}\n**Region:** {region or aws_config.region}\n\n"
            result += "| Function Name | Runtime | Memory (MB) | Timeout (s) | Last Modified |\n"
            result += "|---------------|---------|-------------|-------------|---------------|\n"
            for fn in sorted(functions, key=lambda x: x["FunctionName"]):
                result += f"| {fn['FunctionName']} | {fn.get('Runtime', '-')} | {fn.get('MemorySize', '-')} | {fn.get('Timeout', '-')} | {fn.get('LastModified', '-')[:19]} |\n"

            result += f"\n**Total:** {len(functions)} function(s)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_list_ecs_services
    # =========================================================================

    @mcp.tool(
        name="aws_list_ecs_services",
        annotations={
            "title": "List ECS Clusters and Services",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_ecs_services(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
        cluster: Optional[str] = Field(default=None, description="Specific cluster name (lists all clusters if not provided)"),
    ) -> str:
        """List ECS clusters and their services.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            ecs = aws_config.get_client("ecs", account=account, region=region)
            acct_label = aws_config.get_account_label(account)
            rgn = region or aws_config.region

            if cluster:
                cluster_arns = [cluster]
            else:
                cluster_arns = ecs.list_clusters().get("clusterArns", [])

            if not cluster_arns:
                return f"No ECS clusters found in {acct_label} ({rgn})"

            clusters = ecs.describe_clusters(clusters=cluster_arns, include=["STATISTICS"]).get("clusters", [])

            result = f"# ECS — {acct_label}\n**Region:** {rgn}\n\n"

            for c in clusters:
                result += f"## Cluster: {c['clusterName']} ({c['status']})\n"
                result += f"- Services: {c.get('activeServicesCount', 0)} | Tasks: {c.get('runningTasksCount', 0)} running, {c.get('pendingTasksCount', 0)} pending\n\n"

                # List services in this cluster
                svc_arns = ecs.list_services(cluster=c["clusterArn"]).get("serviceArns", [])
                if svc_arns:
                    svcs = ecs.describe_services(cluster=c["clusterArn"], services=svc_arns).get("services", [])
                    result += "| Service | Status | Desired | Running | Launch Type |\n"
                    result += "|---------|--------|---------|---------|-------------|\n"
                    for s in svcs:
                        result += f"| {s['serviceName']} | {s['status']} | {s.get('desiredCount', 0)} | {s.get('runningCount', 0)} | {s.get('launchType', '-')} |\n"
                    result += "\n"

            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_list_cloudwatch_alarms
    # =========================================================================

    @mcp.tool(
        name="aws_list_cloudwatch_alarms",
        annotations={
            "title": "List CloudWatch Alarms",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_cloudwatch_alarms(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
        state_filter: Optional[str] = Field(default=None, description="Filter: 'OK', 'ALARM', 'INSUFFICIENT_DATA'"),
    ) -> str:
        """List CloudWatch alarms with current state.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            cw = aws_config.get_client("cloudwatch", account=account, region=region)
            acct_label = aws_config.get_account_label(account)

            kwargs = {}
            if state_filter:
                kwargs["StateValue"] = state_filter

            response = cw.describe_alarms(**kwargs)
            alarms = response.get("MetricAlarms", [])

            if not alarms:
                return f"No CloudWatch alarms found in {acct_label} ({region or aws_config.region})"

            result = f"# CloudWatch Alarms — {acct_label}\n\n"
            result += "| Alarm Name | State | Metric | Threshold | Namespace |\n"
            result += "|------------|-------|--------|-----------|----------|\n"
            for a in sorted(alarms, key=lambda x: x.get("StateValue", "")):
                name = a["AlarmName"]
                if len(name) > 40:
                    name = name[:37] + "..."
                result += f"| {name} | {a.get('StateValue', '-')} | {a.get('MetricName', '-')} | {a.get('Threshold', '-')} | {a.get('Namespace', '-')} |\n"

            result += f"\n**Total:** {len(alarms)} alarm(s)"
            alarm_count = sum(1 for a in alarms if a.get("StateValue") == "ALARM")
            if alarm_count:
                result += f" ({alarm_count} in ALARM state)"
            return result
        except Exception as e:
            return handle_aws_error(e)

    # =========================================================================
    # aws_list_route53_zones
    # =========================================================================

    @mcp.tool(
        name="aws_list_route53_zones",
        annotations={
            "title": "List Route53 Hosted Zones",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_route53_zones(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
    ) -> str:
        """List Route53 hosted zones (DNS zones).

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            r53 = aws_config.get_client("route53", account=account)
            acct_label = aws_config.get_account_label(account)

            response = r53.list_hosted_zones()
            zones = response.get("HostedZones", [])

            if not zones:
                return f"No Route53 hosted zones found in {acct_label}"

            result = f"# Route53 Hosted Zones — {acct_label}\n\n"
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

    # =========================================================================
    # aws_list_cloudformation_stacks
    # =========================================================================

    @mcp.tool(
        name="aws_list_cloudformation_stacks",
        annotations={
            "title": "List CloudFormation Stacks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def aws_list_cloudformation_stacks(
        account: str = Field(default="prod", description=ACCOUNT_DESC),
        region: Optional[str] = Field(default=None, description="AWS region"),
    ) -> str:
        """List CloudFormation stacks with status.

        Supports multi-account: specify account='prod', 'nonprod', or 'admin'.
        """
        if not aws_config.is_configured:
            return "Error: AWS not configured. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        try:
            cf = aws_config.get_client("cloudformation", account=account, region=region)
            acct_label = aws_config.get_account_label(account)

            response = cf.list_stacks()
            stacks = [s for s in response.get("StackSummaries", []) if "DELETE" not in s.get("StackStatus", "")]

            if not stacks:
                return f"No CloudFormation stacks found in {acct_label} ({region or aws_config.region})"

            result = f"# CloudFormation Stacks — {acct_label}\n**Region:** {region or aws_config.region}\n\n"
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

    print("AWS tools registered successfully")
