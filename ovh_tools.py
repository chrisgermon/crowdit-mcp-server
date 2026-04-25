"""
OVHcloud Integration Tools for Crowd IT MCP Server

Provides OVHcloud management capabilities via the OVH API v1.0.

Capabilities:
- Account: me, subsidiary, applications, API credentials, time
- Billing: bills, refunds, payment methods, vouchers
- Services: list all services, get details, renew configuration
- Dedicated Servers: list, get, hardware/specs, IPs, interventions, boot, reboot, MRTG
- VPS: list, get, IPs, datacenter, options, start/stop/reboot, monitoring
- Public Cloud: projects, instances (list/get/start/stop/reboot/delete), flavors, images,
  regions, networks, snapshots, ssh keys, volumes, kube clusters
- Domains: list, get, zone records (list/get/create/update/delete), zone refresh
- DNS Zones: list, get, records
- IP Blocks: list, get info, reverse DNS
- vRack: list, services attached
- Web Hosting: list, get
- Email Pro / Exchange / Office365: list services
- Telephony / SMS: list services
- Order / Catalog: list catalogs
- Support: list tickets, get ticket
- Raw request: arbitrary GET against any OVH API path

Authentication: HMAC-SHA1 signed requests using application key/secret + consumer key.

Environment Variables:
    OVH_APPLICATION_KEY:    Application key from OVH developer console
    OVH_APPLICATION_SECRET: Application secret
    OVH_CONSUMER_KEY:       Consumer key (long-lived; obtained via /auth/credential)
    OVH_ENDPOINT:           Region endpoint name (default: ovh-eu)
        Valid values: ovh-eu, ovh-ca, ovh-us, kimsufi-eu, kimsufi-ca,
                      soyoustart-eu, soyoustart-ca
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
    "ovh-ca": "https://ca.api.ovh.com/1.0",
    "ovh-us": "https://api.us.ovhcloud.com/1.0",
    "kimsufi-eu": "https://eu.api.kimsufi.com/1.0",
    "kimsufi-ca": "https://ca.api.kimsufi.com/1.0",
    "soyoustart-eu": "https://eu.api.soyoustart.com/1.0",
    "soyoustart-ca": "https://ca.api.soyoustart.com/1.0",
}


class OVHConfig:
    def __init__(self):
        self.application_key = os.getenv("OVH_APPLICATION_KEY", "")
        self.application_secret = os.getenv("OVH_APPLICATION_SECRET", "")
        self.consumer_key = os.getenv("OVH_CONSUMER_KEY", "")
        self.endpoint_name = os.getenv("OVH_ENDPOINT", "ovh-eu").strip().lower()
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
            if self.endpoint_name == "ovh-eu":
                ep = get_secret_sync("OVH_ENDPOINT")
                if ep:
                    self.endpoint_name = ep.strip().lower()
        except Exception:
            pass
        self._secrets_loaded = True

    @property
    def base_url(self) -> str:
        self._load_secrets()
        return ENDPOINTS.get(self.endpoint_name, ENDPOINTS["ovh-eu"])

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return bool(self.application_key and self.application_secret and self.consumer_key)


async def _ovh_time_delta(config: OVHConfig) -> int:
    """Return server-local clock skew in seconds (cached on the config)."""
    if config._time_delta is not None:
        return config._time_delta
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{config.base_url}/auth/time")
            if r.status_code == 200:
                server_time = int(r.text.strip())
                config._time_delta = server_time - int(time.time())
                return config._time_delta
    except Exception as e:
        logger.warning(f"OVH time sync failed: {e}")
    config._time_delta = 0
    return 0


def _sign(secret: str, consumer_key: str, method: str, url: str, body: str, timestamp: int) -> str:
    raw = f"{secret}+{consumer_key}+{method}+{url}+{body}+{timestamp}"
    return "$1$" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def _request(
    config: OVHConfig,
    method: str,
    path: str,
    params: Optional[dict] = None,
    body: Optional[Any] = None,
) -> httpx.Response:
    url = f"{config.base_url}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url = f"{url}?{urlencode(clean, doseq=True)}"
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"))
    delta = await _ovh_time_delta(config)
    timestamp = int(time.time()) + delta
    headers = {
        "X-Ovh-Application": config.application_key,
        "X-Ovh-Consumer": config.consumer_key,
        "X-Ovh-Timestamp": str(timestamp),
        "X-Ovh-Signature": _sign(
            config.application_secret,
            config.consumer_key,
            method.upper(),
            url,
            body_str,
            timestamp,
        ),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.request(
            method.upper(),
            url,
            headers=headers,
            content=body_str if body is not None else None,
        )


def _check(response: httpx.Response) -> Optional[str]:
    if response.status_code >= 400:
        try:
            data = response.json()
            msg = data.get("message") or data.get("error") or json.dumps(data)
        except Exception:
            msg = response.text
        return f"OVH API Error: {response.status_code} - {msg}"
    return None


def _fmt_kv(d: dict, keys: list[str]) -> str:
    parts = []
    for k in keys:
        v = d.get(k)
        if v is None or v == "":
            continue
        parts.append(f"{k}: {v}")
    return " | ".join(parts)


def _fmt_full(obj: Any, max_chars: int = 4000) -> str:
    text = json.dumps(obj, indent=2, default=str)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... (truncated, {len(text)} total chars)"
    return text


def register_ovh_tools(mcp, config: OVHConfig) -> None:
    """Register OVHcloud tools with the MCP server."""

    NOT_CONFIGURED = (
        "Error: OVH not configured. Set OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET, "
        "OVH_CONSUMER_KEY (and optionally OVH_ENDPOINT)."
    )

    # =========================================================================
    # ACCOUNT
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_me() -> str:
        """Get the OVH account profile (nichandle, contact info, subsidiary)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/me")
            err = _check(r)
            if err:
                return err
            d = r.json()
            return (
                f"## OVH Account: {d.get('nichandle', 'N/A')}\n\n"
                f"- Name: {d.get('firstname', '')} {d.get('name', '')}\n"
                f"- Email: {d.get('email', 'N/A')}\n"
                f"- Subsidiary: {d.get('ovhSubsidiary', 'N/A')}\n"
                f"- Country: {d.get('country', 'N/A')}\n"
                f"- Currency: {d.get('currency', {}).get('code', 'N/A') if isinstance(d.get('currency'), dict) else d.get('currency', 'N/A')}\n"
                f"- Spare Email: {d.get('spareEmail', 'N/A')}\n"
                f"- State: {d.get('state', 'N/A')}\n"
                f"- Customer Code: {d.get('customerCode', 'N/A')}\n"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_api_credentials() -> str:
        """List API credentials (consumer keys) belonging to this account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/me/api/credential")
            err = _check(r)
            if err:
                return err
            ids = r.json()
            if not ids:
                return "No API credentials found."
            return f"## API Credentials ({len(ids)})\n\n" + "\n".join(f"- ID: `{i}`" for i in ids[:50])
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # BILLING
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_bills(
        date_from: Optional[str] = Field(None, description="Filter from date (YYYY-MM-DD)"),
        date_to: Optional[str] = Field(None, description="Filter to date (YYYY-MM-DD)"),
        order_id: Optional[int] = Field(None, description="Filter by order ID"),
    ) -> str:
        """List billing invoices (bill IDs)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if date_from:
                params["date.from"] = date_from
            if date_to:
                params["date.to"] = date_to
            if order_id is not None:
                params["orderId"] = order_id
            r = await _request(config, "GET", "/me/bill", params=params)
            err = _check(r)
            if err:
                return err
            ids = r.json()
            if not ids:
                return "No bills found."
            return f"## Bills ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids[:100])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_bill(bill_id: str = Field(..., description="Bill ID (from list_bills)")) -> str:
        """Get details of a single bill."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/me/bill/{bill_id}")
            err = _check(r)
            if err:
                return err
            d = r.json()
            price = d.get("priceWithTax", {})
            return (
                f"## Bill {d.get('billId', bill_id)}\n\n"
                f"- Date: {d.get('date', 'N/A')}\n"
                f"- Order ID: {d.get('orderId', 'N/A')}\n"
                f"- Total (excl. tax): {d.get('priceWithoutTax', {}).get('text', 'N/A')}\n"
                f"- Total (incl. tax): {price.get('text', 'N/A')}\n"
                f"- Tax: {d.get('tax', {}).get('text', 'N/A')}\n"
                f"- PDF URL: {d.get('pdfUrl', 'N/A')}\n"
                f"- Category: {d.get('category', 'N/A')}\n"
                f"- Password: `{d.get('password', '')}`\n"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_refunds(
        date_from: Optional[str] = Field(None, description="Filter from date (YYYY-MM-DD)"),
        date_to: Optional[str] = Field(None, description="Filter to date (YYYY-MM-DD)"),
    ) -> str:
        """List refund invoices."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if date_from:
                params["date.from"] = date_from
            if date_to:
                params["date.to"] = date_to
            r = await _request(config, "GET", "/me/refund", params=params)
            err = _check(r)
            if err:
                return err
            ids = r.json()
            if not ids:
                return "No refunds found."
            return f"## Refunds ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids[:100])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_payment_methods() -> str:
        """List active payment methods on the account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/me/payment/method")
            err = _check(r)
            if err:
                return err
            ids = r.json()
            if not ids:
                return "No payment methods found."
            return f"## Payment Methods ({len(ids)})\n\n" + "\n".join(f"- ID: `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # SERVICES (catch-all across product lines)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_services(
        category: Optional[str] = Field(None, description="Filter by category (e.g. SERVER, CLOUD, DOMAIN)"),
    ) -> str:
        """List all services on the account (servers, domains, hosting, etc)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/services")
            err = _check(r)
            if err:
                return err
            services = r.json() or []
            if category:
                cat = category.upper()
                services = [s for s in services if (s.get("billing", {}).get("plan", {}).get("invoiceName") or "").upper().startswith(cat) or cat in str(s.get("resource", {}).get("product", {}).get("description", "")).upper()]
            if not services:
                return "No services found."
            lines = []
            for s in services[:200]:
                rid = s.get("resource", {}).get("displayName") or s.get("resource", {}).get("name") or s.get("serviceId")
                product = s.get("billing", {}).get("plan", {}).get("invoiceName", "")
                state = s.get("resource", {}).get("state", "")
                renew = s.get("billing", {}).get("nextBillingDate", "")
                lines.append(f"- **{rid}** | {product} | state: {state} | next bill: {renew} | id: `{s.get('serviceId')}`")
            return f"## Services ({len(services)} total, showing {min(len(services), 200)})\n\n" + "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_service(service_id: int = Field(..., description="Service ID from list_services")) -> str:
        """Get details for a single service by service ID."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/services/{service_id}")
            err = _check(r)
            if err:
                return err
            return f"## Service {service_id}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # DEDICATED SERVERS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_dedicated_servers() -> str:
        """List dedicated server names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/dedicated/server")
            err = _check(r)
            if err:
                return err
            servers = r.json() or []
            if not servers:
                return "No dedicated servers found."
            return f"## Dedicated Servers ({len(servers)})\n\n" + "\n".join(f"- `{s}`" for s in servers)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_dedicated_server(name: str = Field(..., description="Server name (e.g. ns1234.ip-1-2-3.eu)")) -> str:
        """Get details about a dedicated server."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/dedicated/server/{name}")
            err = _check(r)
            if err:
                return err
            d = r.json()
            return (
                f"## Dedicated Server: {d.get('name', name)}\n\n"
                f"- Datacenter: {d.get('datacenter', 'N/A')}\n"
                f"- Commercial Range: {d.get('commercialRange', 'N/A')}\n"
                f"- State: {d.get('state', 'N/A')}\n"
                f"- IP: {d.get('ip', 'N/A')}\n"
                f"- Reverse: {d.get('reverse', 'N/A')}\n"
                f"- Boot ID: {d.get('bootId', 'N/A')}\n"
                f"- Monitoring: {d.get('monitoring', 'N/A')}\n"
                f"- Professional Use: {d.get('professionalUse', 'N/A')}\n"
                f"- Rescue Mail: {d.get('rescueMail', 'N/A')}\n"
                f"- Server ID: {d.get('serverId', 'N/A')}\n"
                f"- OS: {d.get('os', 'N/A')}\n"
                f"- Support Level: {d.get('supportLevel', 'N/A')}\n"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_dedicated_server_specs(name: str = Field(..., description="Server name")) -> str:
        """Get hardware specifications for a dedicated server."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/dedicated/server/{name}/specifications/hardware")
            err = _check(r)
            if err:
                return err
            return f"## Hardware: {name}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_dedicated_server_ips(name: str = Field(..., description="Server name")) -> str:
        """List IPs assigned to a dedicated server."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/dedicated/server/{name}/ips")
            err = _check(r)
            if err:
                return err
            ips = r.json() or []
            if not ips:
                return "No IPs found."
            return f"## IPs for {name} ({len(ips)})\n\n" + "\n".join(f"- {i}" for i in ips)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_dedicated_server_interventions(name: str = Field(..., description="Server name")) -> str:
        """List intervention IDs (maintenance events) for a dedicated server."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/dedicated/server/{name}/intervention")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No interventions."
            return f"## Interventions ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids[:50])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_reboot_dedicated_server(name: str = Field(..., description="Server name")) -> str:
        """Hard reboot a dedicated server. Returns the reboot task."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "POST", f"/dedicated/server/{name}/reboot")
            err = _check(r)
            if err:
                return err
            return f"## Reboot started for {name}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # VPS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_vps() -> str:
        """List VPS names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/vps")
            err = _check(r)
            if err:
                return err
            names = r.json() or []
            if not names:
                return "No VPS found."
            return f"## VPS ({len(names)})\n\n" + "\n".join(f"- `{n}`" for n in names)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_vps(name: str = Field(..., description="VPS service name")) -> str:
        """Get VPS details (state, model, datacenter, OS)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/vps/{name}")
            err = _check(r)
            if err:
                return err
            d = r.json()
            model = d.get("model", {}) or {}
            return (
                f"## VPS: {d.get('name', name)}\n\n"
                f"- State: {d.get('state', 'N/A')}\n"
                f"- Display Name: {d.get('displayName', 'N/A')}\n"
                f"- Cluster: {d.get('cluster', 'N/A')}\n"
                f"- Zone: {d.get('zone', 'N/A')}\n"
                f"- OS: {d.get('netbootMode', 'N/A')}\n"
                f"- Memory (MB): {d.get('memoryLimit', 'N/A')}\n"
                f"- VCores: {d.get('vcore', 'N/A')}\n"
                f"- Model: {model.get('name', 'N/A')} ({model.get('version', 'N/A')})\n"
                f"- Offer Type: {d.get('offerType', 'N/A')}\n"
                f"- Slamonitoring: {d.get('slaMonitoring', 'N/A')}\n"
                f"- Monitoring IP Blocks: {', '.join(d.get('monitoringIpBlocks', []) or [])}\n"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_vps_ips(name: str = Field(..., description="VPS name")) -> str:
        """List IPs for a VPS."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/vps/{name}/ips")
            err = _check(r)
            if err:
                return err
            ips = r.json() or []
            if not ips:
                return "No IPs."
            return f"## IPs for {name}\n\n" + "\n".join(f"- {i}" for i in ips)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_vps_action(
        name: str = Field(..., description="VPS name"),
        action: str = Field(..., description="Action: start, stop, reboot"),
    ) -> str:
        """Start, stop, or reboot a VPS."""
        if not config.is_configured:
            return NOT_CONFIGURED
        action_lower = action.strip().lower()
        valid = {"start", "stop", "reboot"}
        if action_lower not in valid:
            return f"Error: action must be one of {sorted(valid)}"
        try:
            r = await _request(config, "POST", f"/vps/{name}/{action_lower}")
            err = _check(r)
            if err:
                return err
            return f"## {action_lower.title()} VPS {name}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # PUBLIC CLOUD
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_projects() -> str:
        """List Public Cloud project IDs."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/cloud/project")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No cloud projects found."
            return f"## Cloud Projects ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_cloud_project(project_id: str = Field(..., description="Cloud project service name")) -> str:
        """Get a Public Cloud project's details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/cloud/project/{project_id}")
            err = _check(r)
            if err:
                return err
            d = r.json()
            return (
                f"## Cloud Project: {d.get('description', project_id)}\n\n"
                f"- ID: `{d.get('project_id', project_id)}`\n"
                f"- Status: {d.get('status', 'N/A')}\n"
                f"- Plan: {d.get('planCode', 'N/A')}\n"
                f"- Created: {d.get('creationDate', 'N/A')}\n"
                f"- Unleash: {d.get('unleash', 'N/A')}\n"
                f"- Manual Quota: {d.get('manualQuota', 'N/A')}\n"
                f"- Order ID: {d.get('orderId', 'N/A')}\n"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_instances(
        project_id: str = Field(..., description="Cloud project ID"),
        region: Optional[str] = Field(None, description="Filter by region (e.g. SBG5, GRA11)"),
    ) -> str:
        """List instances in a Public Cloud project."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"region": region} if region else None
            r = await _request(config, "GET", f"/cloud/project/{project_id}/instance", params=params)
            err = _check(r)
            if err:
                return err
            instances = r.json() or []
            if not instances:
                return "No instances."
            lines = []
            for i in instances:
                ips = ", ".join((a.get("ip") or "") for a in i.get("ipAddresses", []) if a.get("ip"))
                lines.append(
                    f"- **{i.get('name', 'N/A')}** | id: `{i.get('id')}` | region: {i.get('region', 'N/A')} | "
                    f"flavor: {i.get('flavorId', 'N/A')} | status: {i.get('status', 'N/A')} | ips: {ips}"
                )
            return f"## Cloud Instances ({len(instances)})\n\n" + "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_cloud_instance(
        project_id: str = Field(..., description="Cloud project ID"),
        instance_id: str = Field(..., description="Instance ID"),
    ) -> str:
        """Get details about a single Public Cloud instance."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/cloud/project/{project_id}/instance/{instance_id}")
            err = _check(r)
            if err:
                return err
            return f"## Instance {instance_id}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_cloud_instance_action(
        project_id: str = Field(..., description="Cloud project ID"),
        instance_id: str = Field(..., description="Instance ID"),
        action: str = Field(..., description="Action: start, stop, reboot, shelve, unshelve"),
        reboot_type: Optional[str] = Field("soft", description="For reboot: 'soft' or 'hard'"),
    ) -> str:
        """Start, stop, reboot, shelve or unshelve a Public Cloud instance."""
        if not config.is_configured:
            return NOT_CONFIGURED
        action_lower = action.strip().lower()
        valid = {"start", "stop", "reboot", "shelve", "unshelve"}
        if action_lower not in valid:
            return f"Error: action must be one of {sorted(valid)}"
        try:
            body = None
            if action_lower == "reboot":
                body = {"type": (reboot_type or "soft").lower()}
            r = await _request(
                config,
                "POST",
                f"/cloud/project/{project_id}/instance/{instance_id}/{action_lower}",
                body=body,
            )
            err = _check(r)
            if err:
                return err
            return f"## {action_lower.title()} on instance {instance_id}\n\n```json\n{_fmt_full(r.json() if r.text else {})}\n```"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_delete_cloud_instance(
        project_id: str = Field(..., description="Cloud project ID"),
        instance_id: str = Field(..., description="Instance ID to permanently delete"),
    ) -> str:
        """Delete a Public Cloud instance. This is irreversible."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "DELETE", f"/cloud/project/{project_id}/instance/{instance_id}")
            err = _check(r)
            if err:
                return err
            return f"Instance {instance_id} deletion accepted."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_flavors(
        project_id: str = Field(..., description="Cloud project ID"),
        region: Optional[str] = Field(None, description="Filter by region"),
    ) -> str:
        """List instance flavors (sizes) available in the Public Cloud project."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"region": region} if region else None
            r = await _request(config, "GET", f"/cloud/project/{project_id}/flavor", params=params)
            err = _check(r)
            if err:
                return err
            flavors = r.json() or []
            if not flavors:
                return "No flavors."
            lines = []
            for f in flavors[:200]:
                lines.append(
                    f"- **{f.get('name', 'N/A')}** | region: {f.get('region', 'N/A')} | "
                    f"vcpus: {f.get('vcpus', 'N/A')} | ram: {f.get('ram', 'N/A')}MB | "
                    f"disk: {f.get('disk', 'N/A')}GB | type: {f.get('type', 'N/A')} | id: `{f.get('id')}`"
                )
            return f"## Cloud Flavors ({len(flavors)})\n\n" + "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_images(
        project_id: str = Field(..., description="Cloud project ID"),
        region: Optional[str] = Field(None, description="Filter by region"),
        os_type: Optional[str] = Field(None, description="Filter by OS type (e.g. linux, windows)"),
    ) -> str:
        """List OS images available for instances."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if region:
                params["region"] = region
            if os_type:
                params["osType"] = os_type
            r = await _request(config, "GET", f"/cloud/project/{project_id}/image", params=params or None)
            err = _check(r)
            if err:
                return err
            images = r.json() or []
            if not images:
                return "No images."
            lines = []
            for i in images[:200]:
                lines.append(
                    f"- **{i.get('name', 'N/A')}** | region: {i.get('region', 'N/A')} | "
                    f"type: {i.get('type', 'N/A')} | os: {i.get('flavorType', 'N/A')} | id: `{i.get('id')}`"
                )
            return f"## Cloud Images ({len(images)})\n\n" + "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_regions(
        project_id: str = Field(..., description="Cloud project ID"),
    ) -> str:
        """List regions available in a Public Cloud project."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/cloud/project/{project_id}/region")
            err = _check(r)
            if err:
                return err
            regions = r.json() or []
            if not regions:
                return "No regions."
            return f"## Regions ({len(regions)})\n\n" + "\n".join(f"- `{r}`" for r in regions)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_snapshots(
        project_id: str = Field(..., description="Cloud project ID"),
        region: Optional[str] = Field(None, description="Filter by region"),
    ) -> str:
        """List snapshots in a Public Cloud project."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"region": region} if region else None
            r = await _request(config, "GET", f"/cloud/project/{project_id}/snapshot", params=params)
            err = _check(r)
            if err:
                return err
            snaps = r.json() or []
            if not snaps:
                return "No snapshots."
            lines = []
            for s in snaps:
                lines.append(
                    f"- **{s.get('name', 'N/A')}** | region: {s.get('region', 'N/A')} | "
                    f"size: {s.get('size', 'N/A')}GB | status: {s.get('status', 'N/A')} | id: `{s.get('id')}`"
                )
            return f"## Snapshots ({len(snaps)})\n\n" + "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_volumes(
        project_id: str = Field(..., description="Cloud project ID"),
        region: Optional[str] = Field(None, description="Filter by region"),
    ) -> str:
        """List block storage volumes in a Public Cloud project."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"region": region} if region else None
            r = await _request(config, "GET", f"/cloud/project/{project_id}/volume", params=params)
            err = _check(r)
            if err:
                return err
            vols = r.json() or []
            if not vols:
                return "No volumes."
            lines = []
            for v in vols:
                attached = ", ".join(v.get("attachedTo", []) or [])
                lines.append(
                    f"- **{v.get('name', 'N/A')}** | region: {v.get('region', 'N/A')} | "
                    f"size: {v.get('size', 'N/A')}GB | type: {v.get('type', 'N/A')} | "
                    f"status: {v.get('status', 'N/A')} | attached: {attached or 'no'} | id: `{v.get('id')}`"
                )
            return f"## Volumes ({len(vols)})\n\n" + "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_ssh_keys(project_id: str = Field(..., description="Cloud project ID")) -> str:
        """List SSH keys registered in a Public Cloud project."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/cloud/project/{project_id}/sshkey")
            err = _check(r)
            if err:
                return err
            keys = r.json() or []
            if not keys:
                return "No SSH keys."
            return f"## SSH Keys ({len(keys)})\n\n" + "\n".join(
                f"- **{k.get('name')}** | regions: {', '.join(k.get('regions', []) or [])} | id: `{k.get('id')}`"
                for k in keys
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_cloud_kube_clusters(project_id: str = Field(..., description="Cloud project ID")) -> str:
        """List Managed Kubernetes (MKS) clusters in a Public Cloud project."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/cloud/project/{project_id}/kube")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No clusters."
            return f"## Kube Clusters ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # DOMAINS / DNS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_domains() -> str:
        """List domains owned by the account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/domain")
            err = _check(r)
            if err:
                return err
            domains = r.json() or []
            if not domains:
                return "No domains."
            return f"## Domains ({len(domains)})\n\n" + "\n".join(f"- {d}" for d in domains)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_domain(domain: str = Field(..., description="Domain name (e.g. example.com)")) -> str:
        """Get a domain's registration details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/domain/{domain}")
            err = _check(r)
            if err:
                return err
            return f"## Domain {domain}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_dns_zones() -> str:
        """List DNS zones."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/domain/zone")
            err = _check(r)
            if err:
                return err
            zones = r.json() or []
            if not zones:
                return "No zones."
            return f"## DNS Zones ({len(zones)})\n\n" + "\n".join(f"- {z}" for z in zones)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_zone_records(
        zone: str = Field(..., description="Zone name (e.g. example.com)"),
        field_type: Optional[str] = Field(None, description="Filter by record type (A, AAAA, CNAME, MX, TXT, ...)"),
        sub_domain: Optional[str] = Field(None, description="Filter by subdomain"),
    ) -> str:
        """List record IDs in a DNS zone."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if field_type:
                params["fieldType"] = field_type.upper()
            if sub_domain is not None:
                params["subDomain"] = sub_domain
            r = await _request(config, "GET", f"/domain/zone/{zone}/record", params=params or None)
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No records."
            return f"## Records in {zone} ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_zone_record(
        zone: str = Field(..., description="Zone name"),
        record_id: int = Field(..., description="Record ID"),
    ) -> str:
        """Get a specific DNS record's details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/domain/zone/{zone}/record/{record_id}")
            err = _check(r)
            if err:
                return err
            d = r.json()
            return (
                f"## Record {record_id} in {zone}\n\n"
                f"- Type: {d.get('fieldType', 'N/A')}\n"
                f"- SubDomain: {d.get('subDomain', '') or '(root)'}\n"
                f"- Target: {d.get('target', 'N/A')}\n"
                f"- TTL: {d.get('ttl', 'default')}\n"
                f"- Zone: {d.get('zone', zone)}\n"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_create_zone_record(
        zone: str = Field(..., description="Zone name"),
        field_type: str = Field(..., description="Record type (A, AAAA, CNAME, MX, TXT, etc)"),
        target: str = Field(..., description="Record value/target"),
        sub_domain: Optional[str] = Field(None, description="Subdomain (omit for apex)"),
        ttl: Optional[int] = Field(None, description="TTL in seconds (default zone TTL if omitted)"),
    ) -> str:
        """Create a DNS record in a zone. You must call ovh_refresh_zone afterwards to apply."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            body = {
                "fieldType": field_type.upper(),
                "target": target,
            }
            if sub_domain is not None:
                body["subDomain"] = sub_domain
            if ttl is not None:
                body["ttl"] = ttl
            r = await _request(config, "POST", f"/domain/zone/{zone}/record", body=body)
            err = _check(r)
            if err:
                return err
            d = r.json()
            return (
                f"Created record id `{d.get('id')}` in zone `{zone}`. "
                f"Call ovh_refresh_zone to apply.\n\n```json\n{_fmt_full(d)}\n```"
            )
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_update_zone_record(
        zone: str = Field(..., description="Zone name"),
        record_id: int = Field(..., description="Record ID"),
        target: Optional[str] = Field(None, description="New target value"),
        sub_domain: Optional[str] = Field(None, description="New subdomain"),
        ttl: Optional[int] = Field(None, description="New TTL"),
    ) -> str:
        """Update a DNS record. You must call ovh_refresh_zone to apply."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            body = {}
            if target is not None:
                body["target"] = target
            if sub_domain is not None:
                body["subDomain"] = sub_domain
            if ttl is not None:
                body["ttl"] = ttl
            if not body:
                return "Error: provide at least one of target, sub_domain, ttl."
            r = await _request(config, "PUT", f"/domain/zone/{zone}/record/{record_id}", body=body)
            err = _check(r)
            if err:
                return err
            return f"Updated record `{record_id}` in zone `{zone}`. Call ovh_refresh_zone to apply."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_delete_zone_record(
        zone: str = Field(..., description="Zone name"),
        record_id: int = Field(..., description="Record ID to delete"),
    ) -> str:
        """Delete a DNS record. You must call ovh_refresh_zone to apply."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "DELETE", f"/domain/zone/{zone}/record/{record_id}")
            err = _check(r)
            if err:
                return err
            return f"Deleted record `{record_id}` from zone `{zone}`. Call ovh_refresh_zone to apply."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    async def ovh_refresh_zone(zone: str = Field(..., description="Zone name to publish")) -> str:
        """Publish pending zone changes to the OVH DNS servers."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "POST", f"/domain/zone/{zone}/refresh")
            err = _check(r)
            if err:
                return err
            return f"Zone `{zone}` refresh scheduled."
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # IPs / vRack
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_ip_blocks(
        ip_type: Optional[str] = Field(None, description="Filter by type: dedicated, hosted_ssl, vps, etc"),
    ) -> str:
        """List IP blocks attached to the account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"type": ip_type} if ip_type else None
            r = await _request(config, "GET", "/ip", params=params)
            err = _check(r)
            if err:
                return err
            ips = r.json() or []
            if not ips:
                return "No IP blocks."
            return f"## IP Blocks ({len(ips)})\n\n" + "\n".join(f"- {i}" for i in ips[:200])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_ip_info(ip: str = Field(..., description="IP block (e.g. 1.2.3.0/24 or 1.2.3.4)")) -> str:
        """Get info about an IP / IP block."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            from urllib.parse import quote
            r = await _request(config, "GET", f"/ip/{quote(ip, safe='')}")
            err = _check(r)
            if err:
                return err
            return f"## IP {ip}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_vracks() -> str:
        """List vRack service names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/vrack")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No vRacks."
            return f"## vRacks ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_vrack(service_name: str = Field(..., description="vRack service name")) -> str:
        """Get details about a vRack."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/vrack/{service_name}")
            err = _check(r)
            if err:
                return err
            d = r.json()
            return (
                f"## vRack {service_name}\n\n"
                f"- Name: {d.get('name', 'N/A')}\n"
                f"- Description: {d.get('description', 'N/A')}\n"
            )
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # WEB HOSTING / EMAIL / TELEPHONY (read-only listings)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_web_hosting() -> str:
        """List web hosting service names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/hosting/web")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No web hosting services."
            return f"## Web Hosting ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_web_hosting(service_name: str = Field(..., description="Web hosting service name")) -> str:
        """Get details about a web hosting service."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/hosting/web/{service_name}")
            err = _check(r)
            if err:
                return err
            return f"## Web Hosting {service_name}\n\n```json\n{_fmt_full(r.json())}\n```"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_email_pro() -> str:
        """List Email Pro service names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/email/pro")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No Email Pro services."
            return f"## Email Pro ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_exchange_organizations() -> str:
        """List Microsoft Exchange organization names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/email/exchange")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No Exchange organizations."
            return f"## Exchange Organizations ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_telephony_billing_accounts() -> str:
        """List Telephony billing account names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/telephony")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No telephony accounts."
            return f"## Telephony Accounts ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_sms_services() -> str:
        """List SMS service names."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", "/sms")
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No SMS services."
            return f"## SMS Services ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # SUPPORT TICKETS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_list_support_tickets(
        status: Optional[str] = Field(None, description="Filter by status: open, closed, etc"),
    ) -> str:
        """List support ticket IDs."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"status": status} if status else None
            r = await _request(config, "GET", "/support/tickets", params=params)
            err = _check(r)
            if err:
                return err
            ids = r.json() or []
            if not ids:
                return "No tickets."
            return f"## Support Tickets ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids[:100])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_get_support_ticket(ticket_id: int = Field(..., description="Ticket ID")) -> str:
        """Get a support ticket's details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            r = await _request(config, "GET", f"/support/tickets/{ticket_id}")
            err = _check(r)
            if err:
                return err
            d = r.json()
            return (
                f"## Ticket {d.get('ticketId', ticket_id)}\n\n"
                f"- Subject: {d.get('subject', 'N/A')}\n"
                f"- State: {d.get('state', 'N/A')}\n"
                f"- Category: {d.get('category', 'N/A')}\n"
                f"- Severity: {d.get('severity', 'N/A')}\n"
                f"- Product: {d.get('product', 'N/A')}\n"
                f"- Service: {d.get('serviceName', 'N/A')}\n"
                f"- Last Updated: {d.get('lastUpdate', 'N/A')}\n"
                f"- Creation: {d.get('creationDate', 'N/A')}\n"
                f"- Score: {d.get('score', 'N/A')}\n"
            )
        except Exception as e:
            return f"Error: {e}"

    # =========================================================================
    # ESCAPE HATCH: arbitrary GET
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def ovh_raw_get(
        path: str = Field(..., description="API path under /1.0 (e.g. '/me/payment/transaction')"),
        params: Optional[dict] = Field(None, description="Optional query string parameters"),
    ) -> str:
        """Make an arbitrary GET request against any OVH API endpoint. Returns the JSON response."""
        if not config.is_configured:
            return NOT_CONFIGURED
        if not path.startswith("/"):
            path = "/" + path
        try:
            r = await _request(config, "GET", path, params=params)
            err = _check(r)
            if err:
                return err
            try:
                payload = r.json()
            except Exception:
                payload = r.text
            return f"## GET {path}\n\n```json\n{_fmt_full(payload)}\n```"
        except Exception as e:
            return f"Error: {e}"
