"""
Cloudflare Integration Tools for Crowd IT MCP Server

This module provides Cloudflare management capabilities via the Cloudflare API v4.

Capabilities:
- Zones: list, get, purge cache
- DNS Records: list, create, update, delete
- Firewall/WAF: list firewall rules, list WAF packages
- Page Rules: list page rules
- SSL: get and update SSL settings
- Analytics: zone analytics dashboard
- Zone Settings: get all settings, update individual setting
- Workers: list and get worker scripts
- Accounts: list and get accounts
- Access (Zero Trust): list apps, list policies
- IP Lists: list rule lists, get list items

Authentication: Bearer token (Authorization: Bearer <TOKEN>)

Environment Variables:
    CLOUDFLARE_API_TOKEN: API token from Cloudflare dashboard
"""

import os
import json
import logging
from typing import Optional

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)


class CloudflareConfig:
    def __init__(self):
        self.api_token = os.getenv("CLOUDFLARE_API_TOKEN", "")
        self._secrets_loaded = False

    def _load_secrets(self) -> None:
        if self._secrets_loaded:
            return
        if not self.api_token:
            try:
                from app.core.config import get_secret_sync
                self.api_token = get_secret_sync("CLOUDFLARE_API_TOKEN") or ""
            except Exception:
                pass
        self._secrets_loaded = True

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return bool(self.api_token)

    def headers(self):
        self._load_secrets()
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }


BASE_URL = "https://api.cloudflare.com/client/v4"


async def _cf_get(config: 'CloudflareConfig', path: str, params: dict = None) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(f"{BASE_URL}{path}", headers=config.headers(), params=params)


async def _cf_post(config: 'CloudflareConfig', path: str, json_data: dict = None) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(f"{BASE_URL}{path}", headers=config.headers(), json=json_data)


async def _cf_put(config: 'CloudflareConfig', path: str, json_data: dict) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.put(f"{BASE_URL}{path}", headers=config.headers(), json=json_data)


async def _cf_patch(config: 'CloudflareConfig', path: str, json_data: dict) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.patch(f"{BASE_URL}{path}", headers=config.headers(), json=json_data)


async def _cf_delete(config: 'CloudflareConfig', path: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.delete(f"{BASE_URL}{path}", headers=config.headers())


def _check_cf_response(response: httpx.Response) -> Optional[str]:
    if response.status_code >= 400:
        try:
            data = response.json()
            errors = data.get("errors", [])
            msg = "; ".join(e.get("message", "") for e in errors) if errors else str(data)
            return f"Cloudflare API Error: {response.status_code} - {msg}"
        except Exception:
            return f"Cloudflare API Error: {response.status_code} - {response.text}"
    return None


def register_cloudflare_tools(mcp, config: 'CloudflareConfig') -> None:
    """Register all Cloudflare tools with the MCP server."""

    NOT_CONFIGURED = "Error: Cloudflare not configured (missing CLOUDFLARE_API_TOKEN)."

    # =========================================================================
    # ZONES
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_zones(
        name: Optional[str] = Field(None, description="Filter by domain name (e.g. example.com)"),
        status: Optional[str] = Field(None, description="Filter by zone status: active, pending, initializing, moved, deleted, deactivated"),
        page: Optional[int] = Field(None, description="Page number (default 1)"),
        per_page: Optional[int] = Field(None, description="Results per page (default 20, max 50)"),
    ) -> str:
        """List Cloudflare zones (domains) in the account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if name:
                params["name"] = name
            if status:
                params["status"] = status
            if page is not None:
                params["page"] = page
            if per_page is not None:
                params["per_page"] = per_page
            response = await _cf_get(config, "/zones", params)
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            zones = data.get("result", [])
            if not zones:
                return "No zones found."
            results = []
            for z in zones:
                results.append(f"- **{z.get('name', 'N/A')}** | Status: {z.get('status', 'N/A')} | Plan: {z.get('plan', {}).get('name', 'N/A')} | ID: `{z.get('id', 'N/A')}`")
            total = data.get("result_info", {}).get("total_count", len(results))
            return f"## Cloudflare Zones ({total} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_get_zone(
        zone_id: str = Field(..., description="Zone ID"),
    ) -> str:
        """Get details for a specific Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/zones/{zone_id}")
            error = _check_cf_response(response)
            if error:
                return error
            z = response.json().get("result", {})
            return json.dumps(z, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def cloudflare_purge_cache(
        zone_id: str = Field(..., description="Zone ID"),
        purge_everything: bool = Field(False, description="Purge all cached content (set true to purge everything)"),
        files: Optional[str] = Field(None, description='JSON array of URLs to purge, e.g. ["https://example.com/style.css"]'),
    ) -> str:
        """Purge cached content for a Cloudflare zone. Either purge everything or specific files."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            body = {}
            if purge_everything:
                body["purge_everything"] = True
            elif files:
                body["files"] = json.loads(files)
            else:
                return "Error: Specify either purge_everything=true or provide files to purge."
            response = await _cf_post(config, f"/zones/{zone_id}/purge_cache", body)
            error = _check_cf_response(response)
            if error:
                return error
            return "Cache purge initiated successfully."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in files parameter."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # DNS RECORDS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_dns_records(
        zone_id: str = Field(..., description="Zone ID"),
        record_type: Optional[str] = Field(None, description="Filter by record type: A, AAAA, CNAME, TXT, MX, NS, SRV, etc."),
        name: Optional[str] = Field(None, description="Filter by record name (e.g. example.com or sub.example.com)"),
        page: Optional[int] = Field(None, description="Page number (default 1)"),
        per_page: Optional[int] = Field(None, description="Results per page (default 20, max 100)"),
    ) -> str:
        """List DNS records for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if record_type:
                params["type"] = record_type
            if name:
                params["name"] = name
            if page is not None:
                params["page"] = page
            if per_page is not None:
                params["per_page"] = per_page
            response = await _cf_get(config, f"/zones/{zone_id}/dns_records", params)
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            records = data.get("result", [])
            if not records:
                return "No DNS records found."
            results = []
            for r in records:
                proxied = " (proxied)" if r.get("proxied") else ""
                results.append(f"- **{r.get('type', '?')}** `{r.get('name', 'N/A')}` -> `{r.get('content', 'N/A')}` | TTL: {r.get('ttl', 'auto')}{proxied} | ID: `{r.get('id', 'N/A')}`")
            total = data.get("result_info", {}).get("total_count", len(results))
            return f"## DNS Records ({total} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def cloudflare_create_dns_record(
        zone_id: str = Field(..., description="Zone ID"),
        record_type: str = Field(..., description="Record type: A, AAAA, CNAME, TXT, MX, NS, SRV, etc."),
        name: str = Field(..., description="Record name (e.g. example.com or sub.example.com, use @ for root)"),
        content: str = Field(..., description="Record content (e.g. IP address, hostname, text value)"),
        ttl: Optional[int] = Field(None, description="TTL in seconds (1 = auto, default auto)"),
        proxied: Optional[bool] = Field(None, description="Whether traffic is proxied through Cloudflare (default false)"),
        priority: Optional[int] = Field(None, description="Priority for MX/SRV records"),
    ) -> str:
        """Create a DNS record in a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            body = {
                "type": record_type,
                "name": name,
                "content": content,
            }
            if ttl is not None:
                body["ttl"] = ttl
            if proxied is not None:
                body["proxied"] = proxied
            if priority is not None:
                body["priority"] = priority
            response = await _cf_post(config, f"/zones/{zone_id}/dns_records", body)
            error = _check_cf_response(response)
            if error:
                return error
            r = response.json().get("result", {})
            return f"DNS record created: **{r.get('type', '?')}** `{r.get('name', 'N/A')}` -> `{r.get('content', 'N/A')}` | ID: `{r.get('id', 'N/A')}`"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def cloudflare_update_dns_record(
        zone_id: str = Field(..., description="Zone ID"),
        record_id: str = Field(..., description="DNS record ID"),
        record_type: Optional[str] = Field(None, description="Record type: A, AAAA, CNAME, TXT, MX, etc."),
        name: Optional[str] = Field(None, description="Record name"),
        content: Optional[str] = Field(None, description="Record content"),
        ttl: Optional[int] = Field(None, description="TTL in seconds (1 = auto)"),
        proxied: Optional[bool] = Field(None, description="Whether traffic is proxied through Cloudflare"),
    ) -> str:
        """Update a DNS record in a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            body = {}
            if record_type is not None:
                body["type"] = record_type
            if name is not None:
                body["name"] = name
            if content is not None:
                body["content"] = content
            if ttl is not None:
                body["ttl"] = ttl
            if proxied is not None:
                body["proxied"] = proxied
            if not body:
                return "Error: No fields provided to update."
            response = await _cf_patch(config, f"/zones/{zone_id}/dns_records/{record_id}", body)
            error = _check_cf_response(response)
            if error:
                return error
            r = response.json().get("result", {})
            return f"DNS record updated: **{r.get('type', '?')}** `{r.get('name', 'N/A')}` -> `{r.get('content', 'N/A')}` | ID: `{r.get('id', 'N/A')}`"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def cloudflare_delete_dns_record(
        zone_id: str = Field(..., description="Zone ID"),
        record_id: str = Field(..., description="DNS record ID to delete"),
    ) -> str:
        """Delete a DNS record from a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_delete(config, f"/zones/{zone_id}/dns_records/{record_id}")
            error = _check_cf_response(response)
            if error:
                return error
            return f"DNS record `{record_id}` deleted successfully."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # FIREWALL / WAF
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_firewall_rules(
        zone_id: str = Field(..., description="Zone ID"),
        page: Optional[int] = Field(None, description="Page number"),
        per_page: Optional[int] = Field(None, description="Results per page"),
    ) -> str:
        """List firewall rules for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if page is not None:
                params["page"] = page
            if per_page is not None:
                params["per_page"] = per_page
            response = await _cf_get(config, f"/zones/{zone_id}/firewall/rules", params)
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            rules = data.get("result", [])
            if not rules:
                return "No firewall rules found."
            return json.dumps(rules, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_waf_rules(
        zone_id: str = Field(..., description="Zone ID"),
    ) -> str:
        """List WAF rule packages for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/zones/{zone_id}/firewall/waf/packages")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            packages = data.get("result", [])
            if not packages:
                return "No WAF packages found."
            return json.dumps(packages, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # PAGE RULES
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_page_rules(
        zone_id: str = Field(..., description="Zone ID"),
    ) -> str:
        """List page rules for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/zones/{zone_id}/pagerules")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            rules = data.get("result", [])
            if not rules:
                return "No page rules found."
            results = []
            for r in rules:
                targets = ", ".join(t.get("constraint", {}).get("value", "") for t in r.get("targets", []))
                actions = ", ".join(a.get("id", "") for a in r.get("actions", []))
                results.append(f"- **{targets}** | Actions: {actions} | Status: {r.get('status', 'N/A')} | Priority: {r.get('priority', 'N/A')} | ID: `{r.get('id', 'N/A')}`")
            return f"## Page Rules ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # SSL
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_get_ssl_settings(
        zone_id: str = Field(..., description="Zone ID"),
    ) -> str:
        """Get SSL/TLS settings for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/zones/{zone_id}/settings/ssl")
            error = _check_cf_response(response)
            if error:
                return error
            result = response.json().get("result", {})
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def cloudflare_update_ssl_settings(
        zone_id: str = Field(..., description="Zone ID"),
        value: str = Field(..., description="SSL mode: off, flexible, full, strict (full_strict)"),
    ) -> str:
        """Update SSL/TLS settings for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_patch(config, f"/zones/{zone_id}/settings/ssl", {"value": value})
            error = _check_cf_response(response)
            if error:
                return error
            result = response.json().get("result", {})
            return f"SSL setting updated to: **{result.get('value', value)}**"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ANALYTICS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_get_zone_analytics(
        zone_id: str = Field(..., description="Zone ID"),
        since: Optional[str] = Field(None, description="Start time (ISO 8601 or relative, e.g. -1440 for last 24h in minutes)"),
        until: Optional[str] = Field(None, description="End time (ISO 8601 or relative, e.g. 0 for now)"),
    ) -> str:
        """Get analytics dashboard data for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if since:
                params["since"] = since
            if until:
                params["until"] = until
            response = await _cf_get(config, f"/zones/{zone_id}/analytics/dashboard", params)
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json().get("result", {})
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ZONE SETTINGS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_get_zone_settings(
        zone_id: str = Field(..., description="Zone ID"),
    ) -> str:
        """Get all settings for a Cloudflare zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/zones/{zone_id}/settings")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            settings = data.get("result", [])
            if not settings:
                return "No settings found."
            results = []
            for s in settings:
                results.append(f"- **{s.get('id', 'N/A')}**: {s.get('value', 'N/A')} (editable: {s.get('editable', 'N/A')})")
            return f"## Zone Settings ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def cloudflare_update_zone_setting(
        zone_id: str = Field(..., description="Zone ID"),
        setting_id: str = Field(..., description="Setting ID (e.g. always_use_https, min_tls_version, security_level, cache_level, browser_cache_ttl)"),
        value: str = Field(..., description="New value for the setting (type varies by setting)"),
    ) -> str:
        """Update a specific zone setting in Cloudflare."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            # Try to parse value as JSON for complex values (objects, booleans, numbers)
            try:
                parsed_value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                parsed_value = value
            response = await _cf_patch(config, f"/zones/{zone_id}/settings/{setting_id}", {"value": parsed_value})
            error = _check_cf_response(response)
            if error:
                return error
            result = response.json().get("result", {})
            return f"Zone setting `{setting_id}` updated to: **{result.get('value', parsed_value)}**"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # WORKERS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_workers(
        account_id: str = Field(..., description="Cloudflare account ID"),
    ) -> str:
        """List Workers scripts in a Cloudflare account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/accounts/{account_id}/workers/scripts")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            scripts = data.get("result", [])
            if not scripts:
                return "No Workers scripts found."
            results = []
            for s in scripts:
                results.append(f"- **{s.get('id', 'N/A')}** | Modified: {s.get('modified_on', 'N/A')} | Created: {s.get('created_on', 'N/A')}")
            return f"## Workers Scripts ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_get_worker(
        account_id: str = Field(..., description="Cloudflare account ID"),
        script_name: str = Field(..., description="Worker script name"),
    ) -> str:
        """Get details for a specific Workers script."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/accounts/{account_id}/workers/scripts/{script_name}")
            error = _check_cf_response(response)
            if error:
                return error
            # Worker script endpoint returns the script content directly
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return json.dumps(response.json(), indent=2)
            else:
                # Script content returned as text
                text = response.text
                if len(text) > 5000:
                    return f"## Worker: {script_name}\n\n```javascript\n{text[:5000]}\n```\n\n... (truncated, {len(text)} chars total)"
                return f"## Worker: {script_name}\n\n```javascript\n{text}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ACCOUNTS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_accounts(
        page: Optional[int] = Field(None, description="Page number"),
        per_page: Optional[int] = Field(None, description="Results per page"),
    ) -> str:
        """List Cloudflare accounts the API token has access to."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if page is not None:
                params["page"] = page
            if per_page is not None:
                params["per_page"] = per_page
            response = await _cf_get(config, "/accounts", params)
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            accounts = data.get("result", [])
            if not accounts:
                return "No accounts found."
            results = []
            for a in accounts:
                results.append(f"- **{a.get('name', 'N/A')}** | Type: {a.get('type', 'N/A')} | ID: `{a.get('id', 'N/A')}`")
            return f"## Cloudflare Accounts ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_get_account(
        account_id: str = Field(..., description="Cloudflare account ID"),
    ) -> str:
        """Get details for a specific Cloudflare account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/accounts/{account_id}")
            error = _check_cf_response(response)
            if error:
                return error
            account = response.json().get("result", {})
            return json.dumps(account, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ACCESS (ZERO TRUST)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_access_apps(
        account_id: str = Field(..., description="Cloudflare account ID"),
    ) -> str:
        """List Access (Zero Trust) applications for a Cloudflare account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/accounts/{account_id}/access/apps")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            apps = data.get("result", [])
            if not apps:
                return "No Access applications found."
            results = []
            for a in apps:
                results.append(f"- **{a.get('name', 'N/A')}** | Domain: {a.get('domain', 'N/A')} | Type: {a.get('type', 'N/A')} | ID: `{a.get('id', 'N/A')}`")
            return f"## Access Applications ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_access_policies(
        account_id: str = Field(..., description="Cloudflare account ID"),
        app_id: str = Field(..., description="Access application ID"),
    ) -> str:
        """List Access policies for a specific application."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/accounts/{account_id}/access/apps/{app_id}/policies")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            policies = data.get("result", [])
            if not policies:
                return "No Access policies found."
            results = []
            for p in policies:
                results.append(f"- **{p.get('name', 'N/A')}** | Decision: {p.get('decision', 'N/A')} | Precedence: {p.get('precedence', 'N/A')} | ID: `{p.get('id', 'N/A')}`")
            return f"## Access Policies ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # IP LISTS (RULES LISTS)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_list_ip_lists(
        account_id: str = Field(..., description="Cloudflare account ID"),
    ) -> str:
        """List IP/rules lists in a Cloudflare account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/accounts/{account_id}/rules/lists")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            lists = data.get("result", [])
            if not lists:
                return "No IP lists found."
            results = []
            for lst in lists:
                results.append(f"- **{lst.get('name', 'N/A')}** | Kind: {lst.get('kind', 'N/A')} | Items: {lst.get('num_items', 0)} | ID: `{lst.get('id', 'N/A')}`")
            return f"## IP/Rules Lists ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def cloudflare_get_ip_list_items(
        account_id: str = Field(..., description="Cloudflare account ID"),
        list_id: str = Field(..., description="List ID"),
    ) -> str:
        """Get items in a specific IP/rules list."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _cf_get(config, f"/accounts/{account_id}/rules/lists/{list_id}/items")
            error = _check_cf_response(response)
            if error:
                return error
            data = response.json()
            items = data.get("result", [])
            if not items:
                return "No items in this list."
            return json.dumps(items, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"
