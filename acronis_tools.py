"""
Acronis Cyber Protect Cloud Integration Tools for Crowd IT MCP Server

This module provides Acronis Cyber Protect Cloud capabilities via the Acronis API v2.

Capabilities:
- Tenants: list, get details, usage
- Clients: list API clients
- Users: list and get details
- Alerts: list and get details with severity/type filters
- Activities: list and get details with status/type filters
- Resources: list and get devices/agents
- Protection Plans: list and get backup plans
- Storage: get tenant storage usage
- Tasks: list tasks, trigger backups
- Agents: list registered agents

Authentication: Uses OAuth 2.0 client credentials flow with token caching.

Environment Variables:
    ACRONIS_API_URL: Base URL (e.g. https://au1-cloud.acronis.com)
    ACRONIS_CLIENT_ID: OAuth client ID
    ACRONIS_CLIENT_SECRET: OAuth client secret
"""

import os
import json
import logging
from typing import Optional
from datetime import datetime, timedelta

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration and Authentication
# =============================================================================

class AcronisConfig:
    def __init__(self):
        self.api_url = os.getenv("ACRONIS_API_URL", "")  # e.g. https://au1-cloud.acronis.com
        self._client_id = os.getenv("ACRONIS_CLIENT_ID", "")
        self._client_secret = ""
        self._access_token = None
        self._token_expiry = None
        self._tenant_id = None
        self._secrets_loaded = False

    def _load_secrets(self):
        if self._secrets_loaded:
            return
        if not self._client_id:
            try:
                from app.core.config import get_secret_sync
                self._client_id = get_secret_sync("ACRONIS_CLIENT_ID") or ""
            except Exception:
                pass
        if not self._client_secret:
            try:
                from app.core.config import get_secret_sync
                self._client_secret = get_secret_sync("ACRONIS_CLIENT_SECRET") or ""
            except Exception:
                pass
        if not self.api_url:
            try:
                from app.core.config import get_secret_sync
                self.api_url = get_secret_sync("ACRONIS_API_URL") or ""
            except Exception:
                pass
        self._secrets_loaded = True

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return all([self.api_url, self._client_id, self._client_secret])

    @property
    def tenant_id(self) -> Optional[str]:
        """Return the tenant ID associated with the API client (extracted from token)."""
        return self._tenant_id

    async def get_access_token(self) -> str:
        """Get valid access token, requesting new one if expired."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        import base64
        self._load_secrets()
        creds = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.api_url}/api/2/idp/token",
                data={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expiry = datetime.now() + timedelta(seconds=data.get("expires_in", 3600) - 60)
            # Extract tenant_id from the token scope (format: "scope:tenant_id")
            scope = data.get("scope", "")
            if ":" in scope:
                self._tenant_id = scope.split(":")[-1]
            return self._access_token


# =============================================================================
# Helpers
# =============================================================================

async def _acr_request(config: AcronisConfig, method: str, path: str, params=None, json_data=None):
    """Make an authenticated request to the Acronis API."""
    token = await config.get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method,
            f"{config.api_url}{path}",
            headers=headers,
            params=params,
            json=json_data,
        )
        return resp


# =============================================================================
# Tool Registration
# =============================================================================

def register_acronis_tools(mcp, config: "AcronisConfig") -> None:
    """Register all Acronis Cyber Protect Cloud tools with the MCP server."""

    NOT_CONFIGURED_MSG = "Error: Acronis not configured. Set ACRONIS_API_URL, ACRONIS_CLIENT_ID, and ACRONIS_CLIENT_SECRET."

    # =========================================================================
    # TENANTS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_tenants(
        parent_id: Optional[str] = Field(None, description="Parent tenant ID to list children of. If omitted, lists root tenant children."),
    ) -> str:
        """List Acronis tenants (children of root or specified parent tenant)."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            # Ensure token is fetched so we have the root tenant_id
            await config.get_access_token()
            pid = parent_id or config.tenant_id
            if not pid:
                return "Error: Could not determine root tenant ID. Provide parent_id explicitly."
            params = {"parent_id": pid}
            resp = await _acr_request(config, "GET", "/api/2/tenants", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_tenant(
        tenant_id: str = Field(..., description="Acronis tenant ID (UUID)"),
    ) -> str:
        """Get detailed information about a specific Acronis tenant."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/2/tenants/{tenant_id}")
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_tenant_usage(
        tenant_id: str = Field(..., description="Acronis tenant ID (UUID)"),
    ) -> str:
        """Get usage statistics for an Acronis tenant including storage, devices, and workloads."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/2/tenants/{tenant_id}/usages")
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # CLIENTS (API Clients)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_clients(
        tenant_id: Optional[str] = Field(None, description="Filter by tenant ID"),
    ) -> str:
        """List Acronis API clients."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            params = {}
            if tenant_id:
                params["tenant_id"] = tenant_id
            resp = await _acr_request(config, "GET", "/api/2/clients", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # USERS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_users(
        tenant_id: Optional[str] = Field(None, description="Filter by tenant ID"),
    ) -> str:
        """List Acronis users, optionally filtered by tenant."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            await config.get_access_token()
            tid = tenant_id or config.tenant_id
            if not tid:
                return "Error: Could not determine tenant ID. Provide tenant_id explicitly."
            params = {"tenant_id": tid}
            resp = await _acr_request(config, "GET", "/api/2/users", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_user(
        user_id: str = Field(..., description="Acronis user ID (UUID)"),
    ) -> str:
        """Get detailed information about a specific Acronis user."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/2/users/{user_id}")
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ALERTS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_alerts(
        type: Optional[str] = Field(None, description="Filter by alert type"),
        severity: Optional[str] = Field(None, description="Filter by severity (critical, error, warning)"),
    ) -> str:
        """List Acronis alerts with optional type and severity filters."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            params = {}
            if type:
                params["type"] = type
            if severity:
                params["severity"] = severity
            resp = await _acr_request(config, "GET", "/api/alert_manager/v1/alerts", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_alert(
        alert_id: str = Field(..., description="Acronis alert ID"),
    ) -> str:
        """Get detailed information about a specific Acronis alert."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/alert_manager/v1/alerts/{alert_id}")
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ACTIVITIES
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_activities(
        status: Optional[str] = Field(None, description="Filter by status (e.g. succeeded, failed, running)"),
        type: Optional[str] = Field(None, description="Filter by activity type"),
    ) -> str:
        """List Acronis activities/tasks with optional status and type filters."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            params = {}
            if status:
                params["status"] = status
            if type:
                params["type"] = type
            resp = await _acr_request(config, "GET", "/api/task_manager/v2/activities", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_activity(
        activity_id: str = Field(..., description="Acronis activity ID (UUID)"),
    ) -> str:
        """Get detailed information about a specific Acronis activity."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/task_manager/v2/activities/{activity_id}")
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # RESOURCES (Devices/Agents)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_resources(
        type: Optional[str] = Field(None, description="Filter by resource type (e.g. machine)"),
        tenant_id: Optional[str] = Field(None, description="Filter by tenant ID"),
    ) -> str:
        """List Acronis resources (machines/agents) with optional filters."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            params = {}
            if type:
                params["type"] = type
            if tenant_id:
                params["tenant_id"] = tenant_id
            resp = await _acr_request(config, "GET", "/api/resource_management/v4/resources", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_resource(
        resource_id: str = Field(..., description="Acronis resource ID (UUID)"),
    ) -> str:
        """Get detailed information about a specific Acronis resource (machine/agent)."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/resource_management/v4/resources/{resource_id}")
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # PROTECTION PLANS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_plans(
        tenant_id: Optional[str] = Field(None, description="Filter by tenant ID"),
    ) -> str:
        """List Acronis protection/backup plans."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            params = {}
            if tenant_id:
                params["tenant_id"] = tenant_id
            resp = await _acr_request(config, "GET", "/api/policy_management/v4/policies", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_plan(
        plan_id: str = Field(..., description="Acronis protection plan ID (UUID)"),
    ) -> str:
        """Get detailed information about a specific Acronis protection/backup plan."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/policy_management/v4/policies/{plan_id}")
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # BACKUP STORAGE
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_get_storage_usage(
        tenant_id: str = Field(..., description="Acronis tenant ID (UUID)"),
    ) -> str:
        """Get storage usage details for an Acronis tenant, focusing on backup storage consumption."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await _acr_request(config, "GET", f"/api/2/tenants/{tenant_id}/usages")
            resp.raise_for_status()
            data = resp.json()
            # Filter to storage-related usage items if possible
            items = data.get("items", data)
            if isinstance(items, list):
                storage_items = [
                    item for item in items
                    if "storage" in str(item.get("name", "")).lower()
                    or "storage" in str(item.get("offering_item", {}).get("name", "")).lower()
                    or "backup" in str(item.get("name", "")).lower()
                ]
                if storage_items:
                    return json.dumps(storage_items, indent=2)
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # TASKS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_tasks(
        status: Optional[str] = Field(None, description="Filter by task status"),
    ) -> str:
        """List Acronis backup tasks."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            params = {}
            if status:
                params["status"] = status
            resp = await _acr_request(config, "GET", "/api/task_manager/v2/tasks", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    async def acronis_run_backup(
        resource_id: str = Field(..., description="Resource ID (machine/agent UUID) to run backup for"),
        plan_id: str = Field(..., description="Protection plan ID (UUID) to execute"),
    ) -> str:
        """Trigger an on-demand backup task for a specific resource and protection plan."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            payload = {
                "type": "backup",
                "resource_id": resource_id,
                "plan_id": plan_id,
            }
            resp = await _acr_request(config, "POST", "/api/task_manager/v2/tasks", json_data=payload)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # AGENTS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def acronis_list_agents(
        tenant_id: Optional[str] = Field(None, description="Filter by tenant ID"),
    ) -> str:
        """List Acronis registered agents/protection agents."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            params = {}
            if tenant_id:
                params["tenant_id"] = tenant_id
            resp = await _acr_request(config, "GET", "/api/agent_manager/v2/agents", params=params)
            resp.raise_for_status()
            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"
