"""
CIPP (CyberDrain Improved Partner Portal) Integration Tools for Crowd IT MCP Server

This module provides CIPP management capabilities for Microsoft 365 tenants
via the CIPP API.

Capabilities:
- Tenants: list all managed tenants, get tenant details
- Users: list users, mailbox details, sign-in logs
- Devices: list Intune-managed devices
- Groups: list groups
- Mailboxes: list mailboxes, shared mailbox status
- Conditional Access: list CA policies
- Alerts: list alert queue
- Domains: list domains per tenant
- Licenses: list license assignments
- Standards: list applied standards, best practice analysis
- Service Health: Microsoft service health status

Authentication: Azure AD client credentials (OAuth2 client_credentials grant)

Environment Variables:
    CIPP_TENANT_ID: Azure AD tenant ID for the CIPP app registration
    CIPP_CLIENT_ID: Azure AD application (client) ID
    CIPP_CLIENT_SECRET: Azure AD client secret (loaded via secrets manager)
    CIPP_API_URL: CIPP API base URL (e.g. https://cippq7gcl.azurewebsites.net)
"""

import os
import json
import logging
import re
import time
from typing import Literal, Optional

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)


# Group type values accepted by the AddGroup endpoint (CIPP-native enum).
# CIPP's New-CIPPGroup normalizes/branches on these strings.
GroupType = Literal["Distribution", "Security", "M365", "Generic", "Dynamic", "DynamicDistribution"]

# CIPP's EditGroup and ExecGroupsDelete endpoints expect different strings
# than AddGroup. Map our user-facing enum to those endpoint expectations.
_EDIT_GROUP_TYPE_MAP: dict[str, str] = {
    "Distribution": "Distribution List",
    "Security": "Mail-Enabled Security",
    "M365": "Microsoft 365",
    "Generic": "Security",
    "Dynamic": "Security",
    "DynamicDistribution": "Distribution List",
}


def _to_edit_group_type(group_type: str) -> str:
    return _EDIT_GROUP_TYPE_MAP.get(group_type, group_type)


def _derive_mail_nickname(display_name: str) -> str:
    nickname = re.sub(r"[^a-zA-Z0-9]", "", display_name).lower()
    return nickname[:64] or "group"


class CIPPConfig:
    def __init__(self):
        self.tenant_id = os.getenv("CIPP_TENANT_ID", "")
        self.client_id = os.getenv("CIPP_CLIENT_ID", "")
        self.api_url = os.getenv("CIPP_API_URL", "")  # e.g. https://cippq7gcl.azurewebsites.net
        self._client_secret = ""
        self._secrets_loaded = False
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0

    def _load_secrets(self):
        if self._secrets_loaded:
            return
        if not self._client_secret:
            try:
                from app.core.config import get_secret_sync
                self._client_secret = get_secret_sync("CIPP_CLIENT_SECRET") or ""
            except Exception:
                pass
        self._secrets_loaded = True

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return all([self.tenant_id, self.client_id, self._client_secret, self.api_url])

    async def get_access_token(self) -> str:
        """Get a valid access token, requesting a new one if expired."""
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        self._load_secrets()
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self._client_secret,
            "scope": f"api://{self.client_id}/.default",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, data=data)
            response.raise_for_status()
            token_data = response.json()

        self._access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        # Refresh 60 seconds before actual expiry
        self._token_expiry = time.time() + expires_in - 60
        return self._access_token


async def _cipp_get(config: 'CIPPConfig', path: str, params: dict = None) -> httpx.Response:
    """Make an authenticated GET request to the CIPP API."""
    token = await config.get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"{config.api_url.rstrip('/')}{path}"
    async with httpx.AsyncClient() as client:
        return await client.get(url, headers=headers, params=params, timeout=30.0)


async def _cipp_post(config: 'CIPPConfig', path: str, body: dict) -> httpx.Response:
    """Make an authenticated POST request to the CIPP API."""
    token = await config.get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"{config.api_url.rstrip('/')}{path}"
    async with httpx.AsyncClient() as client:
        return await client.post(url, headers=headers, json=body, timeout=60.0)


def _format_results(payload: object) -> str:
    """CIPP returns {Results: str | [str, ...]} for write ops. Render to text."""
    if isinstance(payload, dict):
        results = payload.get("Results", payload.get("results", payload))
    else:
        results = payload
    if isinstance(results, list):
        return "\n".join(str(r) for r in results)
    return str(results)


def _looks_like_failure(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("failed", "error", "could not"))


def register_cipp_tools(mcp, config: 'CIPPConfig') -> None:
    """Register all CIPP tools with the MCP server."""

    # ── Tenants ──────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_tenants() -> str:
        """List all managed Microsoft 365 tenants in CIPP."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListTenants")
            response.raise_for_status()
            tenants = response.json()
            if not tenants:
                return "No tenants found."
            results = []
            for t in tenants[:50]:
                name = t.get("displayName", t.get("defaultDomainName", "Unknown"))
                domain = t.get("defaultDomainName", "N/A")
                results.append(f"- **{name}** (Domain: `{domain}`)")
            return f"## CIPP Managed Tenants ({len(tenants)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing tenants: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_get_tenant_details(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """Get detailed information about a specific managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListTenantDetails", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            data = response.json()
            name = data.get("displayName", "Unknown")
            return f"## Tenant: {name}\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error getting tenant details: {str(e)}"

    # ── Users ────────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_users(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List all users in a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListUsers", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            users = response.json()
            if not users:
                return f"No users found for tenant {tenant_filter}."
            results = []
            for u in users[:50]:
                display = u.get("displayName", "Unknown")
                upn = u.get("userPrincipalName", "N/A")
                enabled = u.get("accountEnabled", "N/A")
                results.append(f"- **{display}** ({upn}) - Enabled: {enabled}")
            return f"## Users for {tenant_filter} ({len(users)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing users: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_get_user_mailbox_details(
        tenant_filter: str = Field(..., description="Tenant domain name"),
        user_id: str = Field(..., description="User ID or UPN")
    ) -> str:
        """Get mailbox details for a specific user in a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListUserMailboxDetails", params={
                "TenantFilter": tenant_filter,
                "userId": user_id,
            })
            response.raise_for_status()
            data = response.json()
            return f"## Mailbox Details for {user_id}\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error getting mailbox details: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_get_user_signin_logs(
        tenant_filter: str = Field(..., description="Tenant domain name"),
        user_id: str = Field(..., description="User ID or UPN")
    ) -> str:
        """Get sign-in logs for a specific user in a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListUserSigninLogs", params={
                "TenantFilter": tenant_filter,
                "userId": user_id,
            })
            response.raise_for_status()
            logs = response.json()
            if not logs:
                return f"No sign-in logs found for user {user_id} in {tenant_filter}."
            results = []
            for log in logs[:25]:
                ts = log.get("createdDateTime", "N/A")
                app = log.get("appDisplayName", "N/A")
                status = log.get("status", {}).get("errorCode", "N/A")
                ip = log.get("ipAddress", "N/A")
                results.append(f"- {ts} | App: {app} | Status: {status} | IP: {ip}")
            return f"## Sign-in Logs for {user_id} ({len(logs)} entries)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error getting sign-in logs: {str(e)}"

    # ── Devices ──────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_devices(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List all Intune-managed devices in a tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListDevices", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            devices = response.json()
            if not devices:
                return f"No devices found for tenant {tenant_filter}."
            results = []
            for d in devices[:50]:
                name = d.get("deviceName", d.get("displayName", "Unknown"))
                os_type = d.get("operatingSystem", "N/A")
                compliance = d.get("complianceState", "N/A")
                results.append(f"- **{name}** (OS: {os_type}) - Compliance: {compliance}")
            return f"## Devices for {tenant_filter} ({len(devices)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing devices: {str(e)}"

    # ── Groups ───────────────────────────────────────────────────────────

    @mcp.tool()
    async def cipp_create_group(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)"),
        display_name: str = Field(..., description="Display name for the new group"),
        group_type: GroupType = Field(
            ...,
            description=(
                "Group type. 'Distribution' = Distribution List, "
                "'Security' = Mail-Enabled Security Group, "
                "'M365' = Microsoft 365 Group, "
                "'Generic' = pure Security Group (no mail), "
                "'Dynamic' = Dynamic Security Group, "
                "'DynamicDistribution' = Dynamic Distribution Group."
            ),
        ),
        username: Optional[str] = Field(
            None,
            description="Mail nickname / alias. Defaults to a slug derived from display_name.",
        ),
        description: str = Field("", description="Optional group description"),
        allow_external: bool = Field(
            False,
            description="Allow external senders. Only meaningful for Distribution and M365 groups.",
        ),
        membership_rules: str = Field(
            "",
            description="Graph filter rule (required for Dynamic / DynamicDistribution groups).",
        ),
        owners: Optional[list[str]] = Field(None, description="UPNs of group owners"),
        members: Optional[list[str]] = Field(None, description="UPNs of initial members"),
    ) -> str:
        """Create a new group in a managed M365 tenant via CIPP.

        Supports Distribution Lists, Mail-Enabled Security, Microsoft 365 Groups,
        plain Security Groups, and Dynamic variants. For Dynamic groups the
        membership_rules argument must be a valid Graph membership rule
        (e.g. (user.department -eq "Sales")).
        """
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        if group_type in ("Dynamic", "DynamicDistribution") and not membership_rules:
            return f"Error: membership_rules is required for {group_type} groups."

        nickname = username or _derive_mail_nickname(display_name)
        body: dict = {
            "tenantFilter": tenant_filter,
            "displayName": display_name,
            "description": description,
            "username": nickname,
            "groupType": group_type,
            "allowExternal": allow_external,
            "membershipRules": membership_rules,
            "owners": list(owners) if owners else [],
            "members": list(members) if members else [],
        }
        logger.info(
            "CIPP AddGroup tenant=%s name=%s type=%s",
            tenant_filter,
            display_name,
            group_type,
        )
        try:
            response = await _cipp_post(config, "/api/AddGroup", body)
            response.raise_for_status()
            text = _format_results(response.json())
            prefix = "Failed" if _looks_like_failure(text) else "Created"
            return f"## CIPP AddGroup — {prefix}\n\n{text}"
        except httpx.HTTPStatusError as e:
            return f"Error creating group ({e.response.status_code}): {e.response.text}"
        except Exception as e:
            return f"Error creating group: {str(e)}"

    @mcp.tool()
    async def cipp_add_group_member(
        tenant_filter: str = Field(..., description="Tenant domain name"),
        group_id: str = Field(..., description="Group object ID (GUID) or DL Identity"),
        group_type: GroupType = Field(..., description="Group type — same enum as cipp_create_group"),
        member_upns: list[str] = Field(..., description="UPNs of users to add"),
    ) -> str:
        """Add one or more members to an existing M365 group via CIPP."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        if not member_upns:
            return "Error: member_upns must contain at least one UPN."

        body = {
            "tenantFilter": tenant_filter,
            "groupId": group_id,
            "groupType": _to_edit_group_type(group_type),
            "AddMember": [
                {"value": upn, "addedFields": {"userPrincipalName": upn}}
                for upn in member_upns
            ],
        }
        logger.info(
            "CIPP EditGroup AddMember tenant=%s group=%s count=%d",
            tenant_filter,
            group_id,
            len(member_upns),
        )
        try:
            response = await _cipp_post(config, "/api/EditGroup", body)
            response.raise_for_status()
            return f"## CIPP AddMember\n\n{_format_results(response.json())}"
        except httpx.HTTPStatusError as e:
            return f"Error adding members ({e.response.status_code}): {e.response.text}"
        except Exception as e:
            return f"Error adding members: {str(e)}"

    @mcp.tool()
    async def cipp_remove_group_member(
        tenant_filter: str = Field(..., description="Tenant domain name"),
        group_id: str = Field(..., description="Group object ID (GUID) or DL Identity"),
        group_type: GroupType = Field(..., description="Group type — same enum as cipp_create_group"),
        members: list[str] = Field(
            ...,
            description=(
                "Members to remove. For Distribution/Mail-Enabled Security groups, pass UPNs. "
                "For Microsoft 365 / Security (Graph) groups, pass directory object IDs."
            ),
        ),
    ) -> str:
        """Remove one or more members from an existing M365 group via CIPP."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        if not members:
            return "Error: members must contain at least one entry."

        body = {
            "tenantFilter": tenant_filter,
            "groupId": group_id,
            "groupType": _to_edit_group_type(group_type),
            "RemoveMember": [
                {"value": m, "addedFields": {"userPrincipalName": m}}
                for m in members
            ],
        }
        logger.info(
            "CIPP EditGroup RemoveMember tenant=%s group=%s count=%d",
            tenant_filter,
            group_id,
            len(members),
        )
        try:
            response = await _cipp_post(config, "/api/EditGroup", body)
            response.raise_for_status()
            return f"## CIPP RemoveMember\n\n{_format_results(response.json())}"
        except httpx.HTTPStatusError as e:
            return f"Error removing members ({e.response.status_code}): {e.response.text}"
        except Exception as e:
            return f"Error removing members: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def cipp_delete_group(
        tenant_filter: str = Field(..., description="Tenant domain name"),
        group_id: str = Field(..., description="Group object ID (GUID) or DL Identity"),
        group_type: GroupType = Field(..., description="Group type — same enum as cipp_create_group"),
        display_name: str = Field(..., description="Display name (used for confirmation/logging)"),
    ) -> str:
        """Delete a group from a managed M365 tenant via CIPP. Destructive."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."

        body = {
            "tenantFilter": tenant_filter,
            "id": group_id,
            "GroupType": _to_edit_group_type(group_type),
            "displayName": display_name,
        }
        logger.info(
            "CIPP ExecGroupsDelete tenant=%s group=%s name=%s",
            tenant_filter,
            group_id,
            display_name,
        )
        try:
            response = await _cipp_post(config, "/api/ExecGroupsDelete", body)
            response.raise_for_status()
            return f"## CIPP DeleteGroup\n\n{_format_results(response.json())}"
        except httpx.HTTPStatusError as e:
            return f"Error deleting group ({e.response.status_code}): {e.response.text}"
        except Exception as e:
            return f"Error deleting group: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_get_group(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)"),
        group_id: str = Field(..., description="Group object ID (GUID) or DL Identity"),
        include_members: bool = Field(False, description="Include the full members list in the response"),
        include_owners: bool = Field(False, description="Include the full owners list in the response"),
        group_type: Optional[str] = Field(
            None,
            description=(
                "Group type — only required when include_owners=True for "
                "Distribution / Mail-Enabled Security groups. Pass the same "
                "enum used by cipp_create_group."
            ),
        ),
    ) -> str:
        """Get a single group's details, including its GUID, type, mail, and (optionally) members/owners.

        Useful for finding the groupId you need to feed to cipp_add_group_member,
        cipp_remove_group_member, or cipp_delete_group.
        """
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."

        params = {"tenantFilter": tenant_filter, "groupID": group_id}
        if include_members:
            params["members"] = "true"
        if include_owners:
            params["owners"] = "true"
        if group_type:
            params["groupType"] = _to_edit_group_type(group_type)

        try:
            response = await _cipp_get(config, "/api/ListGroups", params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            return f"Error getting group ({e.response.status_code}): {e.response.text}"
        except Exception as e:
            return f"Error getting group: {str(e)}"

        # Single-group lookup wraps the group inside `groupInfo`.
        info = data.get("groupInfo") if isinstance(data, dict) else None
        if not info:
            info = data if isinstance(data, dict) else {}

        gid = info.get("id", group_id)
        name = info.get("displayName", "Unknown")
        gtype = info.get("groupType", "Unknown")
        calc_type = info.get("calculatedGroupType", "")
        mail = info.get("mail", "—")
        nickname = info.get("mailNickname", "—")
        is_dynamic = info.get("dynamicGroupBool", False)
        membership_rule = info.get("membershipRule", "") or ""
        teams_enabled = info.get("teamsEnabled", False)

        lines = [
            f"## Group: {name}",
            "",
            f"- **ID**: `{gid}`",
            f"- **Type**: {gtype}" + (f" (`{calc_type}`)" if calc_type else ""),
            f"- **Mail**: {mail}",
            f"- **Mail nickname**: {nickname}",
            f"- **Dynamic**: {is_dynamic}",
            f"- **Teams-enabled**: {teams_enabled}",
        ]
        if membership_rule:
            lines.append(f"- **Membership rule**: `{membership_rule}`")

        members = data.get("members") if isinstance(data, dict) else None
        owners = data.get("owners") if isinstance(data, dict) else None
        if include_members:
            lines.append(f"\n### Members ({len(members or [])})")
            for m in (members or [])[:100]:
                lines.append(f"- {m.get('displayName', '?')} ({m.get('userPrincipalName', m.get('mail', '?'))})")
        if include_owners:
            lines.append(f"\n### Owners ({len(owners or [])})")
            for o in (owners or [])[:100]:
                lines.append(f"- {o.get('displayName', '?')} ({o.get('userPrincipalName', o.get('mail', '?'))})")

        return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_groups(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List all groups in a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListGroups", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            groups = response.json()
            if not groups:
                return f"No groups found for tenant {tenant_filter}."
            results = []
            for g in groups[:50]:
                name = g.get("displayName", "Unknown")
                gtype = g.get("groupType", g.get("mailEnabled", "N/A"))
                results.append(f"- **{name}** (Type: {gtype})")
            return f"## Groups for {tenant_filter} ({len(groups)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing groups: {str(e)}"

    # ── Mailboxes ────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_mailboxes(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List all mailboxes in a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListMailboxes", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            mailboxes = response.json()
            if not mailboxes:
                return f"No mailboxes found for tenant {tenant_filter}."
            results = []
            for m in mailboxes[:50]:
                name = m.get("displayName", "Unknown")
                mtype = m.get("recipientTypeDetails", m.get("recipientType", "N/A"))
                email = m.get("primarySmtpAddress", m.get("mail", "N/A"))
                results.append(f"- **{name}** ({email}) - Type: {mtype}")
            return f"## Mailboxes for {tenant_filter} ({len(mailboxes)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing mailboxes: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_shared_mailbox_account_enabled(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List shared mailboxes that have direct sign-in enabled (security concern)."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListSharedMailboxAccountEnabled", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            data = response.json()
            if not data:
                return f"No shared mailboxes with sign-in enabled found for {tenant_filter}."
            results = []
            for m in data[:50]:
                name = m.get("displayName", "Unknown")
                email = m.get("primarySmtpAddress", m.get("mail", "N/A"))
                results.append(f"- **{name}** ({email})")
            return f"## Shared Mailboxes with Sign-in Enabled for {tenant_filter} ({len(data)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing shared mailbox status: {str(e)}"

    # ── Conditional Access ───────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_conditional_access_policies(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List all Conditional Access policies for a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListConditionalAccessPolicies", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            policies = response.json()
            if not policies:
                return f"No Conditional Access policies found for {tenant_filter}."
            results = []
            for p in policies[:50]:
                name = p.get("displayName", "Unknown")
                state = p.get("state", "N/A")
                results.append(f"- **{name}** (State: {state})")
            return f"## Conditional Access Policies for {tenant_filter} ({len(policies)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing CA policies: {str(e)}"

    # ── Alerts ───────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_alerts_queue() -> str:
        """List the CIPP alert queue across all tenants."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListAlertsQueue")
            response.raise_for_status()
            alerts = response.json()
            if not alerts:
                return "No alerts in the queue."
            results = []
            for a in alerts[:50]:
                title = a.get("Title", a.get("title", "Unknown"))
                tenant = a.get("Tenant", a.get("tenant", "N/A"))
                severity = a.get("Severity", a.get("severity", "N/A"))
                results.append(f"- [{severity}] **{title}** (Tenant: {tenant})")
            return f"## CIPP Alert Queue ({len(alerts)} alerts)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing alerts: {str(e)}"

    # ── Domains ──────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_domains(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List all domains for a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListDomains", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            domains = response.json()
            if not domains:
                return f"No domains found for tenant {tenant_filter}."
            results = []
            for d in domains[:50]:
                name = d.get("id", d.get("name", "Unknown"))
                is_default = d.get("isDefault", False)
                is_verified = d.get("isVerified", False)
                tag = " (default)" if is_default else ""
                verified = "Verified" if is_verified else "Unverified"
                results.append(f"- **{name}**{tag} - {verified}")
            return f"## Domains for {tenant_filter} ({len(domains)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing domains: {str(e)}"

    # ── Licenses ─────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_licenses(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """List all license subscriptions for a managed M365 tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListLicenses", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            licenses = response.json()
            if not licenses:
                return f"No licenses found for tenant {tenant_filter}."
            results = []
            for lic in licenses[:50]:
                name = lic.get("skuPartNumber", lic.get("License", "Unknown"))
                consumed = lic.get("consumedUnits", "N/A")
                total = lic.get("prepaidUnits", {}).get("enabled", "N/A") if isinstance(lic.get("prepaidUnits"), dict) else lic.get("TotalLicenses", "N/A")
                results.append(f"- **{name}** - Used: {consumed} / {total}")
            return f"## Licenses for {tenant_filter} ({len(licenses)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error listing licenses: {str(e)}"

    # ── Standards ────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_standards() -> str:
        """List all applied CIPP standards across tenants."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListStandards")
            response.raise_for_status()
            standards = response.json()
            if not standards:
                return "No standards applied."
            return f"## CIPP Applied Standards\n\n```json\n{json.dumps(standards, indent=2)}\n```"
        except Exception as e:
            return f"Error listing standards: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_best_practice_analyser(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """Run best practice analysis on a managed M365 tenant via CIPP."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/BestPracticeAnalyser", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            data = response.json()
            if not data:
                return f"No best practice analysis data for {tenant_filter}."
            return f"## Best Practice Analysis for {tenant_filter}\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error running best practice analysis: {str(e)}"

    # ── Service Health ───────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cipp_list_service_health(
        tenant_filter: str = Field(..., description="Tenant domain name (e.g. contoso.onmicrosoft.com)")
    ) -> str:
        """Get Microsoft 365 service health status for a managed tenant."""
        if not config.is_configured:
            return "Error: CIPP not configured (missing CIPP_TENANT_ID, CIPP_CLIENT_ID, CIPP_CLIENT_SECRET, or CIPP_API_URL)."
        try:
            response = await _cipp_get(config, "/api/ListServiceHealth", params={"TenantFilter": tenant_filter})
            response.raise_for_status()
            services = response.json()
            if not services:
                return f"No service health data for {tenant_filter}."
            results = []
            for s in services[:50]:
                service = s.get("service", s.get("Service", "Unknown"))
                status = s.get("status", s.get("Status", "N/A"))
                results.append(f"- **{service}**: {status}")
            return f"## Service Health for {tenant_filter} ({len(services)} services)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error getting service health: {str(e)}"
