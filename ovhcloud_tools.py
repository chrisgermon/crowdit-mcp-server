"""
OVHCloud Integration Tools for Crowd IT MCP Server

This module provides OVHCloud management capabilities via the OVH API v1.0.

Capabilities:
- Account / Me: identity, billing contacts, API credentials
- Public Cloud: projects, instances (list, get, start, stop, reboot, delete),
  flavors, images, regions, SSH keys, volumes
- Dedicated Servers: list, get details, hardware specs, IP management, reboot
- VPS: list, get, start, stop, reboot, reinstall
- Domains: list, get, DNS zone records (list, create, update, delete, refresh)
- IP: list addresses, get details, reverse DNS
- SSL Gateway / certificates: list and get
- Bills / Orders: list and get
- Vrack: list and get

Authentication: Custom OVH signature scheme.
    signature = "$1$" + sha1(application_secret + "+" + consumer_key + "+" +
                            METHOD + "+" + url + "+" + body + "+" + timestamp)

Headers on every request:
    X-Ovh-Application: <application key>
    X-Ovh-Consumer:    <consumer key>
    X-Ovh-Timestamp:   <unix timestamp from /auth/time>
    X-Ovh-Signature:   $1$<sha1 hex>

Environment Variables / Secret Manager keys:
    OVH_APPLICATION_KEY
    OVH_APPLICATION_SECRET
    OVH_CONSUMER_KEY
    OVH_ENDPOINT (optional, default "ovh-eu"; one of:
        ovh-eu, ovh-us, ovh-ca, kimsufi-eu, kimsufi-ca, soyoustart-eu, soyoustart-ca)
"""

import os
import json
import time
import hashlib
import logging
from typing import Optional, Any
from urllib.parse import urlencode

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)


ENDPOINTS = {
    "ovh-eu": "https://eu.api.ovh.com/1.0",
    "ovh-us": "https://api.us.ovhcloud.com/1.0",
    "ovh-ca": "https://ca.api.ovh.com/1.0",
    "kimsufi-eu": "https://eu.api.kimsufi.com/1.0",
    "kimsufi-ca": "https://ca.api.kimsufi.com/1.0",
    "soyoustart-eu": "https://eu.api.soyoustart.com/1.0",
    "soyoustart-ca": "https://ca.api.soyoustart.com/1.0",
}


class OVHCloudConfig:
    """OVHCloud API config using SHA-1 signed request authentication."""

    def __init__(self):
        self.application_key = os.getenv("OVH_APPLICATION_KEY", "")
        self.application_secret = os.getenv("OVH_APPLICATION_SECRET", "")
        self.consumer_key = os.getenv("OVH_CONSUMER_KEY", "")
        self.endpoint_name = (os.getenv("OVH_ENDPOINT") or "ovh-eu").strip()
        self._secrets_loaded = False
        self._time_delta: Optional[int] = None

    def _load_secrets(self) -> None:
        if self._secrets_loaded:
            return
        try:
            from app.core.config import get_secret_sync
            if not self.application_key:
                self.application_key = get_secret_sync("OVH_APPLICATION_KEY") or ""
            if not self.application_secret:
                self.application_secret = get_secret_sync("OVH_APPLICATION_SECRET") or ""
            if not self.consumer_key:
                self.consumer_key = get_secret_sync("OVH_CONSUMER_KEY") or ""
        except Exception as e:
            logger.warning(f"Failed to load OVH secrets from Secret Manager: {e}")
        self._secrets_loaded = True

    @property
    def base_url(self) -> str:
        return ENDPOINTS.get(self.endpoint_name, ENDPOINTS["ovh-eu"])

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return bool(self.application_key and self.application_secret and self.consumer_key)

    async def _server_time(self, client: httpx.AsyncClient) -> int:
        """Get the OVH server timestamp; cache the local-vs-server delta."""
        if self._time_delta is None:
            r = await client.get(f"{self.base_url}/auth/time")
            r.raise_for_status()
            server_ts = int(r.text.strip())
            self._time_delta = server_ts - int(time.time())
        return int(time.time()) + self._time_delta

    def _sign(self, method: str, url: str, body: str, timestamp: int) -> str:
        raw = f"{self.application_secret}+{self.consumer_key}+{method}+{url}+{body}+{timestamp}"
        return "$1$" + hashlib.sha1(raw.encode("utf-8")).hexdigest()

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Any = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        self._load_secrets()
        method = method.upper()
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urlencode(clean, doseq=True)}"
        body = "" if json_body is None else json.dumps(json_body)

        async with httpx.AsyncClient(timeout=timeout) as client:
            ts = await self._server_time(client)
            headers = {
                "X-Ovh-Application": self.application_key,
                "X-Ovh-Consumer": self.consumer_key,
                "X-Ovh-Timestamp": str(ts),
                "X-Ovh-Signature": self._sign(method, url, body, ts),
                "Content-Type": "application/json",
            }
            return await client.request(method, url, headers=headers, content=body or None)


def _check(response: httpx.Response) -> Optional[str]:
    if response.status_code >= 400:
        try:
            data = response.json()
            message = data.get("message") or data.get("errorMessage") or json.dumps(data)
        except Exception:
            message = response.text
        return f"OVH API Error {response.status_code}: {message}"
    return None


def _decode(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except Exception:
        return response.text


def register_ovhcloud_tools(mcp, config: "OVHCloudConfig") -> None:
    """Register OVHCloud tools with the MCP server."""

    NOT_CONFIGURED = (
        "Error: OVHCloud not configured. Set OVH_APPLICATION_KEY, "
        "OVH_APPLICATION_SECRET and OVH_CONSUMER_KEY."
    )

    async def _call(method: str, path: str, params=None, body=None) -> str:
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await config.request(method, path, params=params, json_body=body)
            error = _check(response)
            if error:
                return error
            data = _decode(response)
            return json.dumps(data, indent=2, default=str) if data is not None else "OK"
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # ACCOUNT / ME
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_me() -> str:
        """Get the OVH account holder's identity and contact details."""
        return await _call("GET", "/me")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_api_credentials() -> str:
        """List all API consumer keys (credentials) issued for this account."""
        return await _call("GET", "/me/api/credential")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_api_credential(
        credential_id: int = Field(..., description="Credential / consumer key id"),
    ) -> str:
        """Get details for a specific OVH API credential."""
        return await _call("GET", f"/me/api/credential/{credential_id}")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_bills(
        date_from: Optional[str] = Field(None, description="Filter from date (ISO 8601, e.g. 2024-01-01)"),
        date_to: Optional[str] = Field(None, description="Filter to date (ISO 8601)"),
    ) -> str:
        """List recent bills for the OVH account."""
        params = {"date.from": date_from, "date.to": date_to}
        return await _call("GET", "/me/bill", params=params)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_bill(
        bill_id: str = Field(..., description="Bill ID"),
    ) -> str:
        """Get details of a specific bill."""
        return await _call("GET", f"/me/bill/{bill_id}")

    # =========================================================================
    # PUBLIC CLOUD - PROJECTS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_projects() -> str:
        """List Public Cloud project IDs."""
        return await _call("GET", "/cloud/project")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_cloud_project(
        project_id: str = Field(..., description="Public Cloud project (service) id"),
    ) -> str:
        """Get details for a Public Cloud project."""
        return await _call("GET", f"/cloud/project/{project_id}")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_regions(
        project_id: str = Field(..., description="Public Cloud project id"),
    ) -> str:
        """List regions available to a Public Cloud project."""
        return await _call("GET", f"/cloud/project/{project_id}/region")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_flavors(
        project_id: str = Field(..., description="Public Cloud project id"),
        region: Optional[str] = Field(None, description="Filter by region (e.g. GRA11, BHS5)"),
    ) -> str:
        """List instance flavors (sizes) available in a Public Cloud project."""
        return await _call("GET", f"/cloud/project/{project_id}/flavor", params={"region": region})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_images(
        project_id: str = Field(..., description="Public Cloud project id"),
        region: Optional[str] = Field(None, description="Filter by region"),
        flavor_type: Optional[str] = Field(None, description="Filter by flavor type"),
        os_type: Optional[str] = Field(None, description="Filter by OS type (e.g. linux, windows)"),
    ) -> str:
        """List images available in a Public Cloud project."""
        params = {"region": region, "flavorType": flavor_type, "osType": os_type}
        return await _call("GET", f"/cloud/project/{project_id}/image", params=params)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_ssh_keys(
        project_id: str = Field(..., description="Public Cloud project id"),
        region: Optional[str] = Field(None, description="Filter by region"),
    ) -> str:
        """List SSH keys registered in a Public Cloud project."""
        return await _call("GET", f"/cloud/project/{project_id}/sshkey", params={"region": region})

    # =========================================================================
    # PUBLIC CLOUD - INSTANCES
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_instances(
        project_id: str = Field(..., description="Public Cloud project id"),
        region: Optional[str] = Field(None, description="Filter by region"),
    ) -> str:
        """List Public Cloud instances in a project."""
        return await _call("GET", f"/cloud/project/{project_id}/instance", params={"region": region})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_cloud_instance(
        project_id: str = Field(..., description="Public Cloud project id"),
        instance_id: str = Field(..., description="Instance id"),
    ) -> str:
        """Get details for a Public Cloud instance."""
        return await _call("GET", f"/cloud/project/{project_id}/instance/{instance_id}")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def ovh_create_cloud_instance(
        project_id: str = Field(..., description="Public Cloud project id"),
        name: str = Field(..., description="Instance name"),
        flavor_id: str = Field(..., description="Flavor id (size)"),
        image_id: str = Field(..., description="Image id"),
        region: str = Field(..., description="Region (e.g. GRA11)"),
        ssh_key_id: Optional[str] = Field(None, description="SSH key id to inject"),
        monthly_billing: bool = Field(False, description="Use monthly billing instead of hourly"),
        user_data: Optional[str] = Field(None, description="cloud-init user data"),
    ) -> str:
        """Create a Public Cloud instance."""
        body: dict[str, Any] = {
            "name": name,
            "flavorId": flavor_id,
            "imageId": image_id,
            "region": region,
            "monthlyBilling": monthly_billing,
        }
        if ssh_key_id:
            body["sshKeyId"] = ssh_key_id
        if user_data:
            body["userData"] = user_data
        return await _call("POST", f"/cloud/project/{project_id}/instance", body=body)

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def ovh_start_cloud_instance(
        project_id: str = Field(..., description="Public Cloud project id"),
        instance_id: str = Field(..., description="Instance id"),
    ) -> str:
        """Start a stopped Public Cloud instance."""
        return await _call("POST", f"/cloud/project/{project_id}/instance/{instance_id}/start")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_stop_cloud_instance(
        project_id: str = Field(..., description="Public Cloud project id"),
        instance_id: str = Field(..., description="Instance id"),
    ) -> str:
        """Stop a running Public Cloud instance."""
        return await _call("POST", f"/cloud/project/{project_id}/instance/{instance_id}/stop")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_reboot_cloud_instance(
        project_id: str = Field(..., description="Public Cloud project id"),
        instance_id: str = Field(..., description="Instance id"),
        reboot_type: str = Field("soft", description="Reboot type: soft or hard"),
    ) -> str:
        """Reboot a Public Cloud instance (soft or hard)."""
        return await _call(
            "POST",
            f"/cloud/project/{project_id}/instance/{instance_id}/reboot",
            body={"type": reboot_type},
        )

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_delete_cloud_instance(
        project_id: str = Field(..., description="Public Cloud project id"),
        instance_id: str = Field(..., description="Instance id"),
    ) -> str:
        """Delete a Public Cloud instance. This is destructive and irreversible."""
        return await _call("DELETE", f"/cloud/project/{project_id}/instance/{instance_id}")

    # =========================================================================
    # PUBLIC CLOUD - VOLUMES
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_volumes(
        project_id: str = Field(..., description="Public Cloud project id"),
        region: Optional[str] = Field(None, description="Filter by region"),
    ) -> str:
        """List block storage volumes in a Public Cloud project."""
        return await _call("GET", f"/cloud/project/{project_id}/volume", params={"region": region})

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_cloud_volume(
        project_id: str = Field(..., description="Public Cloud project id"),
        volume_id: str = Field(..., description="Volume id"),
    ) -> str:
        """Get details for a Public Cloud block volume."""
        return await _call("GET", f"/cloud/project/{project_id}/volume/{volume_id}")

    # =========================================================================
    # DEDICATED SERVERS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_dedicated_servers() -> str:
        """List all dedicated server names on the account."""
        return await _call("GET", "/dedicated/server")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_dedicated_server(
        server_name: str = Field(..., description="Server service name (e.g. ns123456.ip-1-2-3.eu)"),
    ) -> str:
        """Get details for a dedicated server."""
        return await _call("GET", f"/dedicated/server/{server_name}")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_dedicated_server_hardware(
        server_name: str = Field(..., description="Server service name"),
    ) -> str:
        """Get hardware specifications for a dedicated server."""
        return await _call("GET", f"/dedicated/server/{server_name}/specifications/hardware")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_dedicated_server_ips(
        server_name: str = Field(..., description="Server service name"),
    ) -> str:
        """List IPs assigned to a dedicated server."""
        return await _call("GET", f"/dedicated/server/{server_name}/ips")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_reboot_dedicated_server(
        server_name: str = Field(..., description="Server service name"),
    ) -> str:
        """Hard-reboot a dedicated server."""
        return await _call("POST", f"/dedicated/server/{server_name}/reboot")

    # =========================================================================
    # VPS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_vps() -> str:
        """List all VPS service names on the account."""
        return await _call("GET", "/vps")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_vps(
        service_name: str = Field(..., description="VPS service name (e.g. vpsXXXXX.ovh.net)"),
    ) -> str:
        """Get details for a VPS."""
        return await _call("GET", f"/vps/{service_name}")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def ovh_start_vps(
        service_name: str = Field(..., description="VPS service name"),
    ) -> str:
        """Start a stopped VPS."""
        return await _call("POST", f"/vps/{service_name}/start")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_stop_vps(
        service_name: str = Field(..., description="VPS service name"),
    ) -> str:
        """Stop a running VPS."""
        return await _call("POST", f"/vps/{service_name}/stop")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_reboot_vps(
        service_name: str = Field(..., description="VPS service name"),
    ) -> str:
        """Reboot a VPS."""
        return await _call("POST", f"/vps/{service_name}/reboot")

    # =========================================================================
    # DOMAINS & DNS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_domains() -> str:
        """List all domain names on the account."""
        return await _call("GET", "/domain")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_domain(
        domain: str = Field(..., description="Domain name (e.g. example.com)"),
    ) -> str:
        """Get details for a domain."""
        return await _call("GET", f"/domain/{domain}")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_dns_records(
        zone: str = Field(..., description="DNS zone name (e.g. example.com)"),
        field_type: Optional[str] = Field(None, description="Filter by record type: A, AAAA, CNAME, TXT, MX, NS, SRV, etc."),
        sub_domain: Optional[str] = Field(None, description="Filter by subdomain"),
    ) -> str:
        """List DNS record IDs in a zone (filterable by type / subdomain)."""
        params = {"fieldType": field_type, "subDomain": sub_domain}
        return await _call("GET", f"/domain/zone/{zone}/record", params=params)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_dns_record(
        zone: str = Field(..., description="DNS zone name"),
        record_id: int = Field(..., description="DNS record id"),
    ) -> str:
        """Get details for a DNS record."""
        return await _call("GET", f"/domain/zone/{zone}/record/{record_id}")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def ovh_create_dns_record(
        zone: str = Field(..., description="DNS zone name"),
        field_type: str = Field(..., description="Record type: A, AAAA, CNAME, TXT, MX, NS, SRV, etc."),
        target: str = Field(..., description="Target value (e.g. IP address, hostname, text value)"),
        sub_domain: Optional[str] = Field(None, description="Subdomain (omit for root)"),
        ttl: Optional[int] = Field(None, description="TTL in seconds (default 0 = zone default)"),
    ) -> str:
        """Create a DNS record. Call ovh_refresh_dns_zone afterwards to apply."""
        body: dict[str, Any] = {"fieldType": field_type, "target": target}
        if sub_domain is not None:
            body["subDomain"] = sub_domain
        if ttl is not None:
            body["ttl"] = ttl
        return await _call("POST", f"/domain/zone/{zone}/record", body=body)

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def ovh_update_dns_record(
        zone: str = Field(..., description="DNS zone name"),
        record_id: int = Field(..., description="DNS record id"),
        target: Optional[str] = Field(None, description="New target value"),
        sub_domain: Optional[str] = Field(None, description="New subdomain"),
        ttl: Optional[int] = Field(None, description="New TTL"),
    ) -> str:
        """Update an existing DNS record. Call ovh_refresh_dns_zone afterwards."""
        body: dict[str, Any] = {}
        if target is not None:
            body["target"] = target
        if sub_domain is not None:
            body["subDomain"] = sub_domain
        if ttl is not None:
            body["ttl"] = ttl
        if not body:
            return "Error: provide at least one field to update."
        return await _call("PUT", f"/domain/zone/{zone}/record/{record_id}", body=body)

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_delete_dns_record(
        zone: str = Field(..., description="DNS zone name"),
        record_id: int = Field(..., description="DNS record id"),
    ) -> str:
        """Delete a DNS record. Call ovh_refresh_dns_zone afterwards to apply."""
        return await _call("DELETE", f"/domain/zone/{zone}/record/{record_id}")

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def ovh_refresh_dns_zone(
        zone: str = Field(..., description="DNS zone name"),
    ) -> str:
        """Apply pending DNS record changes to the zone."""
        return await _call("POST", f"/domain/zone/{zone}/refresh")

    # =========================================================================
    # IP MANAGEMENT
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_ips(
        ip_type: Optional[str] = Field(None, description="Filter by type, e.g. dedicated, vps, cloud"),
        description: Optional[str] = Field(None, description="Filter by description"),
    ) -> str:
        """List IP blocks on the account."""
        params = {"type": ip_type, "description": description}
        return await _call("GET", "/ip", params=params)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_ip(
        ip: str = Field(..., description="IP block in CIDR form (URL-encoded slashes are handled)"),
    ) -> str:
        """Get details for an IP block."""
        # Slash in path must stay literal; OVH accepts both forms.
        return await _call("GET", f"/ip/{ip.replace('/', '%2F')}")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_ip_reverse(
        ip_block: str = Field(..., description="IP block in CIDR form"),
    ) -> str:
        """List reverse DNS entries for an IP block."""
        return await _call("GET", f"/ip/{ip_block.replace('/', '%2F')}/reverse")

    # =========================================================================
    # SSL GATEWAY
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_ssl_gateways() -> str:
        """List SSL gateway service names on the account."""
        return await _call("GET", "/sslGateway")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_ssl_gateway(
        service_name: str = Field(..., description="SSL gateway service name"),
    ) -> str:
        """Get details for an SSL gateway."""
        return await _call("GET", f"/sslGateway/{service_name}")

    # =========================================================================
    # VRACK
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_vracks() -> str:
        """List vRack service names on the account."""
        return await _call("GET", "/vrack")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_vrack(
        service_name: str = Field(..., description="vRack service name"),
    ) -> str:
        """Get details for a vRack."""
        return await _call("GET", f"/vrack/{service_name}")

    # =========================================================================
    # GENERIC ESCAPE HATCH
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def ovh_raw_request(
        method: str = Field(..., description="HTTP method: GET, POST, PUT, DELETE"),
        path: str = Field(..., description="API path starting with /, e.g. /me/api/credential"),
        body_json: Optional[str] = Field(None, description="Optional JSON body as a string"),
    ) -> str:
        """Make a raw signed request to any OVH API endpoint. Use sparingly."""
        try:
            payload = json.loads(body_json) if body_json else None
        except json.JSONDecodeError as e:
            return f"Error: invalid JSON in body_json: {e}"
        return await _call(method, path, body=payload)
