"""
DigitalOcean Integration Tools for Crowd IT MCP Server

This module provides comprehensive DigitalOcean cloud management capabilities
using the DigitalOcean API v2.

Capabilities:
- Droplet management (create, list, power actions, resize, rebuild, snapshots)
- Domain & DNS record management
- Firewall management (rules, droplet assignment)
- Block Storage (volumes) management
- Kubernetes cluster management
- Load Balancer management
- Managed Database management
- Project management & resource assignment
- SSH Key, Snapshot, Image, VPC management
- Reserved IP management
- Tag management
- Certificate and CDN management
- Container Registry management
- App Platform management
- Monitoring & alert policies
- Uptime check management
- Account info, regions, and sizes listing

Authentication: Uses Personal Access Token (Bearer token).

Environment Variables:
    DIGITALOCEAN_TOKEN: DigitalOcean API personal access token
"""

import os
import json
import logging
import asyncio
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration and Authentication
# =============================================================================

class DigitalOceanConfig:
    """DigitalOcean API v2 configuration using Bearer token authentication."""

    BASE_URL = "https://api.digitalocean.com/v2"

    def __init__(self):
        self._token: Optional[str] = None

    @property
    def token(self) -> str:
        if self._token:
            return self._token

        # Try Secret Manager first
        try:
            from app.core.config import get_secret_sync
            secret = get_secret_sync("DIGITALOCEAN_TOKEN")
            if secret:
                self._token = secret
                return secret
        except Exception:
            pass

        self._token = os.getenv("DIGITALOCEAN_TOKEN", "")
        return self._token

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    async def do_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_body: dict = None,
        timeout: float = 30.0,
    ) -> Any:
        """Make a DigitalOcean API v2 request with rate-limit retry and error parsing."""
        import httpx

        url = f"{self.BASE_URL}{endpoint}"

        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(3):
                response = await client.request(
                    method=method,
                    url=url,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    params=params,
                    json=json_body,
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    if attempt < 2:
                        await asyncio.sleep(min(retry_after, 30))
                        continue
                    else:
                        raise Exception(
                            f"Rate limited by DigitalOcean API. Retry after {retry_after}s."
                        )

                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        error_id = error_data.get("id", "unknown_error")
                        error_msg = error_data.get("message", response.text)
                        request_id = error_data.get("request_id", "")
                        raise Exception(
                            f"DigitalOcean API error ({response.status_code}, "
                            f"{error_id}): {error_msg}"
                            + (f" [request_id: {request_id}]" if request_id else "")
                        )
                    except (json.JSONDecodeError, KeyError):
                        response.raise_for_status()

                if response.status_code == 204:
                    return {"status": "success"}

                return response.json()

    async def do_paginated_request(
        self,
        endpoint: str,
        result_key: str,
        params: dict = None,
        per_page: int = 100,
        max_pages: int = 10,
    ) -> List[dict]:
        """Make a paginated GET request and collect all results."""
        all_results = []
        page = 1
        params = dict(params or {})

        while page <= max_pages:
            params["page"] = page
            params["per_page"] = per_page
            data = await self.do_request("GET", endpoint, params=params)

            items = data.get(result_key, [])
            all_results.extend(items)

            meta = data.get("meta", {})
            total = meta.get("total", 0)
            links = data.get("links", {})
            pages_info = links.get("pages", {})

            if not pages_info.get("next") or len(all_results) >= total:
                break

            page += 1

        return all_results


# =============================================================================
# Helper / Formatter Functions
# =============================================================================

def format_droplet_summary(droplet: dict) -> dict:
    """Format a DigitalOcean droplet for clean display."""
    networks = droplet.get("networks", {})
    public_ipv4 = ""
    private_ipv4 = ""
    for net in networks.get("v4", []):
        if net.get("type") == "public":
            public_ipv4 = net.get("ip_address", "")
        elif net.get("type") == "private":
            private_ipv4 = net.get("ip_address", "")

    return {
        "id": droplet.get("id"),
        "name": droplet.get("name", ""),
        "status": droplet.get("status", ""),
        "region": droplet.get("region", {}).get("slug", ""),
        "region_name": droplet.get("region", {}).get("name", ""),
        "size": droplet.get("size_slug", ""),
        "vcpus": droplet.get("vcpus"),
        "memory_mb": droplet.get("memory"),
        "disk_gb": droplet.get("disk"),
        "public_ipv4": public_ipv4,
        "private_ipv4": private_ipv4,
        "image": droplet.get("image", {}).get("slug",
                 droplet.get("image", {}).get("name", "")),
        "tags": droplet.get("tags", []),
        "vpc_uuid": droplet.get("vpc_uuid", ""),
        "created_at": droplet.get("created_at", ""),
    }


def format_database_summary(db: dict) -> dict:
    """Format a DigitalOcean managed database cluster for display."""
    return {
        "id": db.get("id", ""),
        "name": db.get("name", ""),
        "engine": db.get("engine", ""),
        "version": db.get("version", ""),
        "status": db.get("status", ""),
        "region": db.get("region", ""),
        "size": db.get("size", ""),
        "num_nodes": db.get("num_nodes"),
        "host": db.get("connection", {}).get("host", ""),
        "port": db.get("connection", {}).get("port"),
        "database": db.get("connection", {}).get("database", ""),
        "created_at": db.get("created_at", ""),
        "tags": db.get("tags", []),
    }


def format_kubernetes_summary(cluster: dict) -> dict:
    """Format a DigitalOcean Kubernetes cluster for display."""
    return {
        "id": cluster.get("id", ""),
        "name": cluster.get("name", ""),
        "region": cluster.get("region", ""),
        "version": cluster.get("version", ""),
        "status": cluster.get("status", {}).get("state", ""),
        "endpoint": cluster.get("endpoint", ""),
        "node_pools": [
            {
                "id": np.get("id", ""),
                "name": np.get("name", ""),
                "size": np.get("size", ""),
                "count": np.get("count"),
                "auto_scale": np.get("auto_scale", False),
                "min_nodes": np.get("min_nodes"),
                "max_nodes": np.get("max_nodes"),
            }
            for np in cluster.get("node_pools", [])
        ],
        "vpc_uuid": cluster.get("vpc_uuid", ""),
        "created_at": cluster.get("created_at", ""),
        "tags": cluster.get("tags", []),
    }




# =============================================================================
# Tool Registration
# =============================================================================

def register_digitalocean_tools(mcp, do_config: 'DigitalOceanConfig'):
    """Register all DigitalOcean tools with the MCP server."""

    # =========================================================================
    # ACCOUNT
    # =========================================================================

    @mcp.tool(
        name="digitalocean_get_account",
        annotations={
            "title": "Get DigitalOcean Account Info",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_get_account() -> str:
        """Get DigitalOcean account information including email, droplet limit, and status."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/account")
            acct = data.get("account", {})
            return json.dumps({
                "email": acct.get("email", ""),
                "uuid": acct.get("uuid", ""),
                "droplet_limit": acct.get("droplet_limit"),
                "floating_ip_limit": acct.get("floating_ip_limit"),
                "volume_limit": acct.get("volume_limit"),
                "status": acct.get("status", ""),
                "team": acct.get("team", {}).get("name", ""),
            }, indent=2)
        except Exception as e:
            return f"Error getting DigitalOcean account: {str(e)}"

    # =========================================================================
    # REGIONS & SIZES
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_regions",
        annotations={
            "title": "List DigitalOcean Regions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_list_regions() -> str:
        """List all available DigitalOcean datacenter regions with features and sizes."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/regions", params={"per_page": 200})
            regions = []
            for r in data.get("regions", []):
                if r.get("available", False):
                    regions.append({
                        "slug": r.get("slug", ""),
                        "name": r.get("name", ""),
                        "features": r.get("features", []),
                        "sizes": r.get("sizes", [])[:5],
                    })
            return json.dumps({"regions": regions}, indent=2)
        except Exception as e:
            return f"Error listing regions: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_sizes",
        annotations={
            "title": "List DigitalOcean Sizes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_list_sizes() -> str:
        """List all available DigitalOcean droplet sizes (plans) with pricing."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/sizes", params={"per_page": 200})
            sizes = []
            for s in data.get("sizes", []):
                if s.get("available", False):
                    sizes.append({
                        "slug": s.get("slug", ""),
                        "description": s.get("description", ""),
                        "vcpus": s.get("vcpus"),
                        "memory_mb": s.get("memory"),
                        "disk_gb": s.get("disk"),
                        "transfer_tb": s.get("transfer"),
                        "price_monthly": s.get("price_monthly"),
                        "price_hourly": s.get("price_hourly"),
                        "regions": s.get("regions", []),
                    })
            return json.dumps({"sizes": sizes}, indent=2)
        except Exception as e:
            return f"Error listing sizes: {str(e)}"

    # =========================================================================
    # DROPLETS
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_droplets",
        annotations={
            "title": "List DigitalOcean Droplets",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_list_droplets(
        tag_name: str = "",
        per_page: int = 50,
        page: int = 1,
    ) -> str:
        """List all DigitalOcean droplets with status, region, size, and IP info.

        Args:
            tag_name: Filter droplets by tag name (optional)
            per_page: Results per page, max 200 (default 50)
            page: Page number for pagination (default 1)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            params = {"per_page": min(per_page, 200), "page": page}
            if tag_name:
                params["tag_name"] = tag_name
            data = await do_config.do_request("GET", "/droplets", params=params)
            droplets = [format_droplet_summary(d) for d in data.get("droplets", [])]
            meta = data.get("meta", {})
            return json.dumps({
                "total": meta.get("total", len(droplets)),
                "page": page,
                "droplets": droplets,
            }, indent=2)
        except Exception as e:
            return f"Error listing droplets: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_droplet",
        annotations={
            "title": "Get DigitalOcean Droplet Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_get_droplet(droplet_id: int) -> str:
        """Get detailed information about a specific DigitalOcean droplet.

        Args:
            droplet_id: Numeric droplet ID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/droplets/{droplet_id}")
            d = data.get("droplet", {})
            result = format_droplet_summary(d)
            result["features"] = d.get("features", [])
            result["backup_ids"] = d.get("backup_ids", [])
            result["snapshot_ids"] = d.get("snapshot_ids", [])
            result["volume_ids"] = d.get("volume_ids", [])
            result["kernel"] = d.get("kernel")
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error getting droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_droplet",
        annotations={
            "title": "Create DigitalOcean Droplet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def digitalocean_create_droplet(
        name: str,
        region: str,
        size: str,
        image: str,
        ssh_keys: str = "",
        backups: bool = False,
        monitoring: bool = True,
        vpc_uuid: str = "",
        tags: str = "",
        user_data: str = "",
    ) -> str:
        """Create a new DigitalOcean droplet (virtual machine).

        Args:
            name: Hostname for the droplet (e.g., 'web-server-01')
            region: Region slug (e.g., 'syd1', 'sgp1', 'nyc3'). Use digitalocean_list_regions.
            size: Size slug (e.g., 's-1vcpu-1gb'). Use digitalocean_list_sizes.
            image: Image slug or ID (e.g., 'ubuntu-24-04-x64'). Use digitalocean_list_images.
            ssh_keys: Comma-separated SSH key IDs or fingerprints (optional)
            backups: Enable weekly backups (default false)
            monitoring: Enable monitoring agent (default true, recommended)
            vpc_uuid: VPC UUID to place droplet in (optional)
            tags: Comma-separated tags (e.g., 'web,production')
            user_data: Cloud-init user data script for first boot (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {
                "name": name, "region": region, "size": size,
                "image": image, "backups": backups, "monitoring": monitoring,
            }
            if ssh_keys:
                keys = []
                for k in ssh_keys.split(","):
                    k = k.strip()
                    if k:
                        try:
                            keys.append(int(k))
                        except ValueError:
                            keys.append(k)
                body["ssh_keys"] = keys
            if vpc_uuid:
                body["vpc_uuid"] = vpc_uuid
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            if user_data:
                body["user_data"] = user_data

            data = await do_config.do_request("POST", "/droplets", json_body=body)
            droplet = data.get("droplet", {})
            return json.dumps({
                "id": droplet.get("id"),
                "name": droplet.get("name"),
                "status": droplet.get("status"),
                "region": droplet.get("region", {}).get("slug", region),
                "size": droplet.get("size_slug", size),
                "message": f"Droplet '{name}' creation initiated. Use digitalocean_get_droplet to check status.",
            }, indent=2)
        except Exception as e:
            return f"Error creating droplet: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_droplet",
        annotations={
            "title": "Delete DigitalOcean Droplet",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_delete_droplet(droplet_id: int) -> str:
        """Permanently delete a DigitalOcean droplet. This action is irreversible.

        Args:
            droplet_id: Numeric droplet ID to delete
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/droplets/{droplet_id}")
            return json.dumps({"status": "success", "message": f"Droplet {droplet_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_droplet_action",
        annotations={
            "title": "Droplet Power Action",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_droplet_action(droplet_id: int, action: str) -> str:
        """Perform a power or configuration action on a droplet.

        Args:
            droplet_id: Numeric droplet ID
            action: One of: 'power_on', 'power_off', 'shutdown' (graceful), 'reboot',
                    'power_cycle', 'enable_backups', 'disable_backups', 'enable_ipv6',
                    'enable_private_networking'
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        valid = ["power_on", "power_off", "shutdown", "reboot", "power_cycle",
                 "enable_backups", "disable_backups", "enable_ipv6", "enable_private_networking"]
        if action not in valid:
            return f"Error: Invalid action '{action}'. Valid: {', '.join(valid)}"
        try:
            data = await do_config.do_request("POST", f"/droplets/{droplet_id}/actions", json_body={"type": action})
            act = data.get("action", {})
            return json.dumps({
                "action_id": act.get("id"), "type": act.get("type"),
                "status": act.get("status"), "started_at": act.get("started_at"),
                "droplet_id": droplet_id,
            }, indent=2)
        except Exception as e:
            return f"Error performing {action} on droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_resize_droplet",
        annotations={
            "title": "Resize DigitalOcean Droplet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_resize_droplet(droplet_id: int, size: str, disk: bool = True) -> str:
        """Resize a droplet to a different plan. Droplet must be powered off first.

        Args:
            droplet_id: Numeric droplet ID
            size: New size slug (e.g., 's-2vcpu-4gb'). Use digitalocean_list_sizes.
            disk: If true, disk is permanently resized (cannot downsize later). Default true.
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", f"/droplets/{droplet_id}/actions",
                json_body={"type": "resize", "size": size, "disk": disk})
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": f"Resize to {size} initiated. Droplet must be off."}, indent=2)
        except Exception as e:
            return f"Error resizing droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_rebuild_droplet",
        annotations={
            "title": "Rebuild DigitalOcean Droplet",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_rebuild_droplet(droplet_id: int, image: str) -> str:
        """Rebuild a droplet with a new image. WARNING: All data on the droplet will be destroyed.

        Args:
            droplet_id: Numeric droplet ID
            image: Image slug or ID to rebuild with (e.g., 'ubuntu-24-04-x64')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", f"/droplets/{droplet_id}/actions",
                json_body={"type": "rebuild", "image": image})
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": f"Rebuild with {image} initiated."}, indent=2)
        except Exception as e:
            return f"Error rebuilding droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_rename_droplet",
        annotations={
            "title": "Rename DigitalOcean Droplet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_rename_droplet(droplet_id: int, name: str) -> str:
        """Rename a DigitalOcean droplet.

        Args:
            droplet_id: Numeric droplet ID
            name: New hostname for the droplet
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", f"/droplets/{droplet_id}/actions",
                json_body={"type": "rename", "name": name})
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": f"Droplet renamed to '{name}'."}, indent=2)
        except Exception as e:
            return f"Error renaming droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_snapshot_droplet",
        annotations={
            "title": "Snapshot DigitalOcean Droplet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def digitalocean_snapshot_droplet(droplet_id: int, name: str = "") -> str:
        """Create a snapshot of a droplet. Droplet should be powered off for consistency.

        Args:
            droplet_id: Numeric droplet ID
            name: Snapshot name (optional, defaults to date-based name)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"type": "snapshot"}
            if name:
                body["name"] = name
            data = await do_config.do_request("POST", f"/droplets/{droplet_id}/actions", json_body=body)
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": "Snapshot creation initiated."}, indent=2)
        except Exception as e:
            return f"Error snapshotting droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_droplet_snapshots",
        annotations={
            "title": "List Droplet Snapshots",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_list_droplet_snapshots(droplet_id: int) -> str:
        """List all snapshots for a specific droplet.

        Args:
            droplet_id: Numeric droplet ID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/droplets/{droplet_id}/snapshots", params={"per_page": 100})
            snapshots = [{"id": s.get("id"), "name": s.get("name", ""), "created_at": s.get("created_at", ""),
                "size_gigabytes": s.get("size_gigabytes"), "min_disk_size": s.get("min_disk_size"),
                "regions": s.get("regions", [])} for s in data.get("snapshots", [])]
            return json.dumps({"snapshots": snapshots}, indent=2)
        except Exception as e:
            return f"Error listing snapshots for droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_droplet_backups",
        annotations={
            "title": "List Droplet Backups",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_list_droplet_backups(droplet_id: int) -> str:
        """List all backups for a specific droplet.

        Args:
            droplet_id: Numeric droplet ID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/droplets/{droplet_id}/backups", params={"per_page": 100})
            backups = [{"id": s.get("id"), "name": s.get("name", ""), "created_at": s.get("created_at", ""),
                "size_gigabytes": s.get("size_gigabytes"), "min_disk_size": s.get("min_disk_size")}
                for s in data.get("backups", [])]
            return json.dumps({"backups": backups}, indent=2)
        except Exception as e:
            return f"Error listing backups for droplet {droplet_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_droplet_neighbors",
        annotations={
            "title": "List Droplet Neighbors",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def digitalocean_list_droplet_neighbors(droplet_id: int) -> str:
        """List droplets running on the same physical hardware as this droplet.

        Args:
            droplet_id: Numeric droplet ID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/droplets/{droplet_id}/neighbors")
            neighbors = [format_droplet_summary(d) for d in data.get("droplets", [])]
            return json.dumps({"neighbors": neighbors}, indent=2)
        except Exception as e:
            return f"Error listing neighbors for droplet {droplet_id}: {str(e)}"

    # =========================================================================
    # DOMAINS & DNS
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_domains",
        annotations={"title": "List Domains", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_domains() -> str:
        """List all domains managed in DigitalOcean DNS."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/domains", params={"per_page": 200})
            domains = [{"name": d.get("name", ""), "ttl": d.get("ttl"), "zone_file": d.get("zone_file", "")[:200]}
                for d in data.get("domains", [])]
            return json.dumps({"total": data.get("meta", {}).get("total", len(domains)), "domains": domains}, indent=2)
        except Exception as e:
            return f"Error listing domains: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_domain",
        annotations={"title": "Get Domain Details", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_get_domain(domain_name: str) -> str:
        """Get details for a specific domain.

        Args:
            domain_name: The domain name (e.g., 'example.com')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/domains/{domain_name}")
            d = data.get("domain", {})
            return json.dumps({"name": d.get("name", ""), "ttl": d.get("ttl"), "zone_file": d.get("zone_file", "")}, indent=2)
        except Exception as e:
            return f"Error getting domain {domain_name}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_domain",
        annotations={"title": "Create Domain", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_domain(name: str, ip_address: str = "") -> str:
        """Add a domain to DigitalOcean DNS management.

        Args:
            name: Domain name to add (e.g., 'example.com')
            ip_address: Optional IP to create an A record for the domain root
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name}
            if ip_address:
                body["ip_address"] = ip_address
            data = await do_config.do_request("POST", "/domains", json_body=body)
            d = data.get("domain", {})
            return json.dumps({"name": d.get("name", ""), "ttl": d.get("ttl"),
                "message": f"Domain '{name}' added. Update your registrar NS records to point to DigitalOcean."}, indent=2)
        except Exception as e:
            return f"Error creating domain {name}: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_domain",
        annotations={"title": "Delete Domain", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_domain(domain_name: str) -> str:
        """Remove a domain from DigitalOcean DNS. This deletes all associated DNS records.

        Args:
            domain_name: Domain name to delete (e.g., 'example.com')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/domains/{domain_name}")
            return json.dumps({"status": "success", "message": f"Domain '{domain_name}' and all records deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting domain {domain_name}: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_domain_records",
        annotations={"title": "List DNS Records", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_domain_records(domain_name: str, record_type: str = "") -> str:
        """List all DNS records for a domain.

        Args:
            domain_name: Domain name (e.g., 'example.com')
            record_type: Filter by type: A, AAAA, CNAME, MX, TXT, NS, SRV, CAA (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            params = {"per_page": 200}
            if record_type:
                params["type"] = record_type.upper()
            data = await do_config.do_request("GET", f"/domains/{domain_name}/records", params=params)
            records = [{"id": r.get("id"), "type": r.get("type", ""), "name": r.get("name", ""),
                "data": r.get("data", ""), "priority": r.get("priority"), "port": r.get("port"),
                "ttl": r.get("ttl"), "weight": r.get("weight"), "flags": r.get("flags"),
                "tag": r.get("tag")} for r in data.get("domain_records", [])]
            return json.dumps({"total": len(records), "records": records}, indent=2)
        except Exception as e:
            return f"Error listing DNS records for {domain_name}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_domain_record",
        annotations={"title": "Create DNS Record", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_domain_record(
        domain_name: str, record_type: str, name: str, data: str,
        priority: int = 0, port: int = 0, ttl: int = 1800, weight: int = 0,
    ) -> str:
        """Create a DNS record for a domain.

        Args:
            domain_name: Domain name (e.g., 'example.com')
            record_type: Record type: A, AAAA, CNAME, MX, TXT, NS, SRV, CAA
            name: Record name (e.g., 'www', '@' for root, '*' for wildcard)
            data: Record value (e.g., IP address, hostname, text value)
            priority: Priority for MX/SRV records (default 0)
            port: Port for SRV records (default 0)
            ttl: Time to live in seconds (default 1800)
            weight: Weight for SRV records (default 0)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"type": record_type.upper(), "name": name, "data": data, "ttl": ttl}
            if record_type.upper() in ("MX", "SRV"):
                body["priority"] = priority
            if record_type.upper() == "SRV":
                body["port"] = port
                body["weight"] = weight
            result = await do_config.do_request("POST", f"/domains/{domain_name}/records", json_body=body)
            rec = result.get("domain_record", {})
            return json.dumps({"id": rec.get("id"), "type": rec.get("type"), "name": rec.get("name"),
                "data": rec.get("data"), "ttl": rec.get("ttl"), "message": "DNS record created."}, indent=2)
        except Exception as e:
            return f"Error creating DNS record for {domain_name}: {str(e)}"

    @mcp.tool(
        name="digitalocean_update_domain_record",
        annotations={"title": "Update DNS Record", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_update_domain_record(
        domain_name: str, record_id: int, record_type: str = "", name: str = "",
        data: str = "", priority: int = -1, ttl: int = -1,
    ) -> str:
        """Update an existing DNS record.

        Args:
            domain_name: Domain name (e.g., 'example.com')
            record_id: Record ID to update (from digitalocean_list_domain_records)
            record_type: New record type (optional, usually not changed)
            name: New record name (optional)
            data: New record value (optional)
            priority: New priority for MX/SRV (use -1 to skip)
            ttl: New TTL in seconds (use -1 to skip)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {}
            if record_type:
                body["type"] = record_type.upper()
            if name:
                body["name"] = name
            if data:
                body["data"] = data
            if priority >= 0:
                body["priority"] = priority
            if ttl >= 0:
                body["ttl"] = ttl
            if not body:
                return "Error: No fields to update. Provide at least one of: record_type, name, data, priority, ttl."
            result = await do_config.do_request("PUT", f"/domains/{domain_name}/records/{record_id}", json_body=body)
            rec = result.get("domain_record", {})
            return json.dumps({"id": rec.get("id"), "type": rec.get("type"), "name": rec.get("name"),
                "data": rec.get("data"), "ttl": rec.get("ttl"), "message": "DNS record updated."}, indent=2)
        except Exception as e:
            return f"Error updating DNS record {record_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_domain_record",
        annotations={"title": "Delete DNS Record", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_domain_record(domain_name: str, record_id: int) -> str:
        """Delete a DNS record.

        Args:
            domain_name: Domain name (e.g., 'example.com')
            record_id: Record ID to delete (from digitalocean_list_domain_records)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/domains/{domain_name}/records/{record_id}")
            return json.dumps({"status": "success", "message": f"DNS record {record_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting DNS record {record_id}: {str(e)}"

    # =========================================================================
    # FIREWALLS
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_firewalls",
        annotations={"title": "List Firewalls", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_firewalls() -> str:
        """List all DigitalOcean cloud firewalls."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/firewalls", params={"per_page": 200})
            firewalls = []
            for fw in data.get("firewalls", []):
                firewalls.append({
                    "id": fw.get("id", ""), "name": fw.get("name", ""), "status": fw.get("status", ""),
                    "droplet_ids": fw.get("droplet_ids", []), "tags": fw.get("tags", []),
                    "inbound_rules_count": len(fw.get("inbound_rules", [])),
                    "outbound_rules_count": len(fw.get("outbound_rules", [])),
                    "created_at": fw.get("created_at", ""),
                })
            return json.dumps({"firewalls": firewalls}, indent=2)
        except Exception as e:
            return f"Error listing firewalls: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_firewall",
        annotations={"title": "Get Firewall Details", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_get_firewall(firewall_id: str) -> str:
        """Get detailed information about a firewall including all rules.

        Args:
            firewall_id: Firewall UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/firewalls/{firewall_id}")
            fw = data.get("firewall", {})
            return json.dumps({
                "id": fw.get("id", ""), "name": fw.get("name", ""), "status": fw.get("status", ""),
                "droplet_ids": fw.get("droplet_ids", []), "tags": fw.get("tags", []),
                "inbound_rules": fw.get("inbound_rules", []),
                "outbound_rules": fw.get("outbound_rules", []),
                "created_at": fw.get("created_at", ""),
                "pending_changes": fw.get("pending_changes", []),
            }, indent=2)
        except Exception as e:
            return f"Error getting firewall {firewall_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_firewall",
        annotations={"title": "Create Firewall", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_firewall(
        name: str, inbound_rules: str = "", outbound_rules: str = "",
        droplet_ids: str = "", tags: str = "",
    ) -> str:
        """Create a new DigitalOcean cloud firewall.

        Args:
            name: Firewall name
            inbound_rules: JSON array of inbound rules. Each rule:
                {"protocol":"tcp","ports":"22","sources":{"addresses":["0.0.0.0/0"]}}
                Protocol: tcp, udp, icmp. Ports: '22', '80,443', '8000-9000', or 'all'.
                Sources: addresses (CIDRs), droplet_ids, load_balancer_uids, kubernetes_ids, tags.
            outbound_rules: JSON array of outbound rules. Same format but 'destinations' instead of 'sources'.
            droplet_ids: Comma-separated droplet IDs to protect
            tags: Comma-separated tags - firewall applies to droplets with these tags

        Example inbound_rules:
        [{"protocol":"tcp","ports":"22","sources":{"addresses":["10.0.0.0/8"]}},
         {"protocol":"tcp","ports":"80,443","sources":{"addresses":["0.0.0.0/0","::/0"]}},
         {"protocol":"icmp","sources":{"addresses":["0.0.0.0/0","::/0"]}}]
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name}
            if inbound_rules:
                body["inbound_rules"] = json.loads(inbound_rules)
            if outbound_rules:
                body["outbound_rules"] = json.loads(outbound_rules)
            if droplet_ids:
                body["droplet_ids"] = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            data = await do_config.do_request("POST", "/firewalls", json_body=body)
            fw = data.get("firewall", {})
            return json.dumps({"id": fw.get("id"), "name": fw.get("name"), "status": fw.get("status"),
                "message": "Firewall created."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in rules: {str(e)}"
        except Exception as e:
            return f"Error creating firewall: {str(e)}"

    @mcp.tool(
        name="digitalocean_update_firewall",
        annotations={"title": "Update Firewall", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_update_firewall(
        firewall_id: str, name: str, inbound_rules: str = "[]", outbound_rules: str = "[]",
        droplet_ids: str = "", tags: str = "",
    ) -> str:
        """Update a firewall. Replaces the entire firewall configuration.

        Args:
            firewall_id: Firewall UUID
            name: Firewall name (required for update)
            inbound_rules: JSON array of ALL inbound rules (replaces existing)
            outbound_rules: JSON array of ALL outbound rules (replaces existing)
            droplet_ids: Comma-separated droplet IDs
            tags: Comma-separated tags
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "inbound_rules": json.loads(inbound_rules), "outbound_rules": json.loads(outbound_rules)}
            if droplet_ids:
                body["droplet_ids"] = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            data = await do_config.do_request("PUT", f"/firewalls/{firewall_id}", json_body=body)
            fw = data.get("firewall", {})
            return json.dumps({"id": fw.get("id"), "name": fw.get("name"), "status": fw.get("status"),
                "message": "Firewall updated."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in rules: {str(e)}"
        except Exception as e:
            return f"Error updating firewall {firewall_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_firewall",
        annotations={"title": "Delete Firewall", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_firewall(firewall_id: str) -> str:
        """Delete a DigitalOcean firewall.

        Args:
            firewall_id: Firewall UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/firewalls/{firewall_id}")
            return json.dumps({"status": "success", "message": f"Firewall {firewall_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting firewall {firewall_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_add_firewall_droplets",
        annotations={"title": "Add Droplets to Firewall", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_add_firewall_droplets(firewall_id: str, droplet_ids: str) -> str:
        """Add droplets to a firewall.

        Args:
            firewall_id: Firewall UUID
            droplet_ids: Comma-separated droplet IDs to add
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            ids = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            await do_config.do_request("POST", f"/firewalls/{firewall_id}/droplets", json_body={"droplet_ids": ids})
            return json.dumps({"status": "success", "message": f"Added {len(ids)} droplet(s) to firewall."}, indent=2)
        except Exception as e:
            return f"Error adding droplets to firewall: {str(e)}"

    @mcp.tool(
        name="digitalocean_remove_firewall_droplets",
        annotations={"title": "Remove Droplets from Firewall", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_remove_firewall_droplets(firewall_id: str, droplet_ids: str) -> str:
        """Remove droplets from a firewall.

        Args:
            firewall_id: Firewall UUID
            droplet_ids: Comma-separated droplet IDs to remove
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            ids = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            await do_config.do_request("DELETE", f"/firewalls/{firewall_id}/droplets", json_body={"droplet_ids": ids})
            return json.dumps({"status": "success", "message": f"Removed {len(ids)} droplet(s) from firewall."}, indent=2)
        except Exception as e:
            return f"Error removing droplets from firewall: {str(e)}"

    # =========================================================================
    # VOLUMES (BLOCK STORAGE)
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_volumes",
        annotations={"title": "List Volumes", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_volumes(region: str = "") -> str:
        """List all block storage volumes.

        Args:
            region: Filter by region slug (optional, e.g., 'syd1')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            params = {"per_page": 200}
            if region:
                params["region"] = region
            data = await do_config.do_request("GET", "/volumes", params=params)
            volumes = [{"id": v.get("id", ""), "name": v.get("name", ""), "size_gigabytes": v.get("size_gigabytes"),
                "region": v.get("region", {}).get("slug", ""), "description": v.get("description", ""),
                "droplet_ids": v.get("droplet_ids", []), "filesystem_type": v.get("filesystem_type", ""),
                "filesystem_label": v.get("filesystem_label", ""), "created_at": v.get("created_at", ""),
                "tags": v.get("tags", [])} for v in data.get("volumes", [])]
            return json.dumps({"volumes": volumes}, indent=2)
        except Exception as e:
            return f"Error listing volumes: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_volume",
        annotations={"title": "Get Volume Details", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_get_volume(volume_id: str) -> str:
        """Get detailed information about a block storage volume.

        Args:
            volume_id: Volume UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/volumes/{volume_id}")
            v = data.get("volume", {})
            return json.dumps({"id": v.get("id", ""), "name": v.get("name", ""),
                "size_gigabytes": v.get("size_gigabytes"), "region": v.get("region", {}).get("slug", ""),
                "description": v.get("description", ""), "droplet_ids": v.get("droplet_ids", []),
                "filesystem_type": v.get("filesystem_type", ""), "filesystem_label": v.get("filesystem_label", ""),
                "created_at": v.get("created_at", ""), "tags": v.get("tags", [])}, indent=2)
        except Exception as e:
            return f"Error getting volume {volume_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_volume",
        annotations={"title": "Create Volume", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_volume(
        name: str, size_gigabytes: int, region: str, description: str = "",
        filesystem_type: str = "ext4", tags: str = "",
    ) -> str:
        """Create a new block storage volume.

        Args:
            name: Volume name (must be unique per region)
            size_gigabytes: Volume size in GB (min 1, max 16384)
            region: Region slug (e.g., 'syd1')
            description: Volume description (optional)
            filesystem_type: Filesystem type: 'ext4' or 'xfs' (default 'ext4')
            tags: Comma-separated tags (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "size_gigabytes": size_gigabytes, "region": region, "filesystem_type": filesystem_type}
            if description:
                body["description"] = description
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            data = await do_config.do_request("POST", "/volumes", json_body=body)
            v = data.get("volume", {})
            return json.dumps({"id": v.get("id"), "name": v.get("name"),
                "size_gigabytes": v.get("size_gigabytes"), "region": region,
                "message": "Volume created. Use digitalocean_attach_volume to attach to a droplet."}, indent=2)
        except Exception as e:
            return f"Error creating volume: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_volume",
        annotations={"title": "Delete Volume", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_volume(volume_id: str) -> str:
        """Delete a block storage volume. Volume must be detached from all droplets first.

        Args:
            volume_id: Volume UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/volumes/{volume_id}")
            return json.dumps({"status": "success", "message": f"Volume {volume_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting volume {volume_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_attach_volume",
        annotations={"title": "Attach Volume to Droplet", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_attach_volume(volume_id: str, droplet_id: int, region: str = "") -> str:
        """Attach a block storage volume to a droplet. Both must be in the same region.

        Args:
            volume_id: Volume UUID
            droplet_id: Droplet ID to attach to
            region: Region slug (optional, inferred from volume)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"type": "attach", "volume_id": volume_id, "droplet_id": droplet_id}
            if region:
                body["region"] = region
            data = await do_config.do_request("POST", "/volumes/actions", json_body=body)
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": f"Volume attached to droplet {droplet_id}."}, indent=2)
        except Exception as e:
            return f"Error attaching volume: {str(e)}"

    @mcp.tool(
        name="digitalocean_detach_volume",
        annotations={"title": "Detach Volume from Droplet", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_detach_volume(volume_id: str, droplet_id: int, region: str = "") -> str:
        """Detach a block storage volume from a droplet.

        Args:
            volume_id: Volume UUID
            droplet_id: Droplet ID to detach from
            region: Region slug (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"type": "detach", "volume_id": volume_id, "droplet_id": droplet_id}
            if region:
                body["region"] = region
            data = await do_config.do_request("POST", "/volumes/actions", json_body=body)
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": f"Volume detached from droplet {droplet_id}."}, indent=2)
        except Exception as e:
            return f"Error detaching volume: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_volume_snapshots",
        annotations={"title": "List Volume Snapshots", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_volume_snapshots(volume_id: str) -> str:
        """List snapshots for a block storage volume.

        Args:
            volume_id: Volume UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/volumes/{volume_id}/snapshots", params={"per_page": 100})
            snapshots = [{"id": s.get("id"), "name": s.get("name", ""), "size_gigabytes": s.get("size_gigabytes"),
                "created_at": s.get("created_at", ""), "min_disk_size": s.get("min_disk_size"),
                "regions": s.get("regions", [])} for s in data.get("snapshots", [])]
            return json.dumps({"snapshots": snapshots}, indent=2)
        except Exception as e:
            return f"Error listing volume snapshots: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_volume_snapshot",
        annotations={"title": "Create Volume Snapshot", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_volume_snapshot(volume_id: str, name: str, tags: str = "") -> str:
        """Create a snapshot of a block storage volume.

        Args:
            volume_id: Volume UUID
            name: Snapshot name
            tags: Comma-separated tags (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name}
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            data = await do_config.do_request("POST", f"/volumes/{volume_id}/snapshots", json_body=body)
            s = data.get("snapshot", {})
            return json.dumps({"id": s.get("id"), "name": s.get("name"),
                "size_gigabytes": s.get("size_gigabytes"), "message": "Volume snapshot created."}, indent=2)
        except Exception as e:
            return f"Error creating volume snapshot: {str(e)}"

    # =========================================================================
    # KUBERNETES
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_kubernetes_clusters",
        annotations={"title": "List Kubernetes Clusters", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_kubernetes_clusters() -> str:
        """List all DigitalOcean Kubernetes (DOKS) clusters."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/kubernetes/clusters", params={"per_page": 100})
            clusters = [format_kubernetes_summary(c) for c in data.get("kubernetes_clusters", [])]
            return json.dumps({"clusters": clusters}, indent=2)
        except Exception as e:
            return f"Error listing Kubernetes clusters: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_kubernetes_cluster",
        annotations={"title": "Get Kubernetes Cluster", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_get_kubernetes_cluster(cluster_id: str) -> str:
        """Get detailed information about a Kubernetes cluster.

        Args:
            cluster_id: Kubernetes cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/kubernetes/clusters/{cluster_id}")
            return json.dumps(format_kubernetes_summary(data.get("kubernetes_cluster", {})), indent=2)
        except Exception as e:
            return f"Error getting cluster {cluster_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_kubernetes_cluster",
        annotations={"title": "Create Kubernetes Cluster", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_kubernetes_cluster(
        name: str, region: str, version: str, node_pool_name: str, node_pool_size: str,
        node_pool_count: int = 3, auto_scale: bool = False, min_nodes: int = 1,
        max_nodes: int = 5, vpc_uuid: str = "", tags: str = "",
    ) -> str:
        """Create a new DigitalOcean Kubernetes cluster.

        Args:
            name: Cluster name
            region: Region slug (e.g., 'syd1')
            version: Kubernetes version slug (e.g., '1.29.1-do.0')
            node_pool_name: Name for the default node pool
            node_pool_size: Droplet size for nodes (e.g., 's-2vcpu-4gb')
            node_pool_count: Number of nodes (default 3)
            auto_scale: Enable auto-scaling (default false)
            min_nodes: Min nodes if auto-scaling enabled (default 1)
            max_nodes: Max nodes if auto-scaling enabled (default 5)
            vpc_uuid: VPC UUID (optional)
            tags: Comma-separated tags (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            node_pool = {"name": node_pool_name, "size": node_pool_size, "count": node_pool_count}
            if auto_scale:
                node_pool["auto_scale"] = True
                node_pool["min_nodes"] = min_nodes
                node_pool["max_nodes"] = max_nodes
            body = {"name": name, "region": region, "version": version, "node_pools": [node_pool]}
            if vpc_uuid:
                body["vpc_uuid"] = vpc_uuid
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            data = await do_config.do_request("POST", "/kubernetes/clusters", json_body=body)
            c = data.get("kubernetes_cluster", {})
            return json.dumps({"id": c.get("id"), "name": c.get("name"), "status": c.get("status", {}).get("state", ""),
                "message": "Kubernetes cluster creation initiated. This may take several minutes."}, indent=2)
        except Exception as e:
            return f"Error creating Kubernetes cluster: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_kubernetes_cluster",
        annotations={"title": "Delete Kubernetes Cluster", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_kubernetes_cluster(cluster_id: str) -> str:
        """Delete a Kubernetes cluster and all its resources.

        Args:
            cluster_id: Kubernetes cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/kubernetes/clusters/{cluster_id}")
            return json.dumps({"status": "success", "message": f"Kubernetes cluster {cluster_id} deletion initiated."}, indent=2)
        except Exception as e:
            return f"Error deleting cluster {cluster_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_kubernetes_node_pools",
        annotations={"title": "List K8s Node Pools", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_kubernetes_node_pools(cluster_id: str) -> str:
        """List node pools in a Kubernetes cluster.

        Args:
            cluster_id: Kubernetes cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/kubernetes/clusters/{cluster_id}/node_pools")
            pools = [{"id": np.get("id", ""), "name": np.get("name", ""), "size": np.get("size", ""),
                "count": np.get("count"), "auto_scale": np.get("auto_scale", False),
                "min_nodes": np.get("min_nodes"), "max_nodes": np.get("max_nodes"),
                "nodes": [{"id": n.get("id", ""), "name": n.get("name", ""), "status": n.get("status", {}).get("state", ""),
                    "droplet_id": n.get("droplet_id")} for n in np.get("nodes", [])],
                "tags": np.get("tags", [])} for np in data.get("node_pools", [])]
            return json.dumps({"node_pools": pools}, indent=2)
        except Exception as e:
            return f"Error listing node pools: {str(e)}"

    @mcp.tool(
        name="digitalocean_add_kubernetes_node_pool",
        annotations={"title": "Add K8s Node Pool", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_add_kubernetes_node_pool(
        cluster_id: str, name: str, size: str, count: int = 3,
        auto_scale: bool = False, min_nodes: int = 1, max_nodes: int = 5, tags: str = "",
    ) -> str:
        """Add a new node pool to a Kubernetes cluster.

        Args:
            cluster_id: Kubernetes cluster UUID
            name: Node pool name
            size: Droplet size for nodes (e.g., 's-2vcpu-4gb')
            count: Number of nodes (default 3)
            auto_scale: Enable auto-scaling (default false)
            min_nodes: Min nodes if auto-scaling (default 1)
            max_nodes: Max nodes if auto-scaling (default 5)
            tags: Comma-separated tags (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "size": size, "count": count}
            if auto_scale:
                body["auto_scale"] = True
                body["min_nodes"] = min_nodes
                body["max_nodes"] = max_nodes
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            data = await do_config.do_request("POST", f"/kubernetes/clusters/{cluster_id}/node_pools", json_body=body)
            np = data.get("node_pool", {})
            return json.dumps({"id": np.get("id"), "name": np.get("name"), "size": np.get("size"),
                "count": np.get("count"), "message": "Node pool added."}, indent=2)
        except Exception as e:
            return f"Error adding node pool: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_kubernetes_node_pool",
        annotations={"title": "Delete K8s Node Pool", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_kubernetes_node_pool(cluster_id: str, node_pool_id: str) -> str:
        """Delete a node pool from a Kubernetes cluster.

        Args:
            cluster_id: Kubernetes cluster UUID
            node_pool_id: Node pool UUID to delete
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/kubernetes/clusters/{cluster_id}/node_pools/{node_pool_id}")
            return json.dumps({"status": "success", "message": f"Node pool {node_pool_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting node pool: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_kubernetes_kubeconfig",
        annotations={"title": "Get Kubeconfig", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_get_kubernetes_kubeconfig(cluster_id: str) -> str:
        """Get the kubeconfig YAML for a Kubernetes cluster.

        Args:
            cluster_id: Kubernetes cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            import httpx
            url = f"{do_config.BASE_URL}/kubernetes/clusters/{cluster_id}/kubeconfig"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers={
                    "Authorization": f"Bearer {do_config.token}", "Accept": "application/yaml"})
                response.raise_for_status()
                return json.dumps({"kubeconfig": response.text, "message": "Save this as ~/.kube/config to use with kubectl."}, indent=2)
        except Exception as e:
            return f"Error getting kubeconfig: {str(e)}"

    # =========================================================================
    # LOAD BALANCERS
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_load_balancers",
        annotations={"title": "List Load Balancers", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_load_balancers() -> str:
        """List all DigitalOcean load balancers."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/load_balancers", params={"per_page": 100})
            lbs = [{"id": lb.get("id", ""), "name": lb.get("name", ""), "ip": lb.get("ip", ""),
                "status": lb.get("status", ""), "region": lb.get("region", {}).get("slug", ""),
                "size": lb.get("size", ""), "size_unit": lb.get("size_unit", ""),
                "droplet_ids": lb.get("droplet_ids", []), "tag": lb.get("tag", ""),
                "forwarding_rules": lb.get("forwarding_rules", []),
                "health_check": lb.get("health_check", {}),
                "created_at": lb.get("created_at", "")} for lb in data.get("load_balancers", [])]
            return json.dumps({"load_balancers": lbs}, indent=2)
        except Exception as e:
            return f"Error listing load balancers: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_load_balancer",
        annotations={"title": "Get Load Balancer", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_get_load_balancer(lb_id: str) -> str:
        """Get detailed information about a load balancer.

        Args:
            lb_id: Load balancer UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/load_balancers/{lb_id}")
            lb = data.get("load_balancer", {})
            return json.dumps({"id": lb.get("id", ""), "name": lb.get("name", ""), "ip": lb.get("ip", ""),
                "status": lb.get("status", ""), "region": lb.get("region", {}).get("slug", ""),
                "size": lb.get("size", ""), "algorithm": lb.get("algorithm", ""),
                "droplet_ids": lb.get("droplet_ids", []), "tag": lb.get("tag", ""),
                "forwarding_rules": lb.get("forwarding_rules", []),
                "health_check": lb.get("health_check", {}),
                "sticky_sessions": lb.get("sticky_sessions", {}),
                "redirect_http_to_https": lb.get("redirect_http_to_https", False),
                "vpc_uuid": lb.get("vpc_uuid", ""), "created_at": lb.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting load balancer {lb_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_load_balancer",
        annotations={"title": "Create Load Balancer", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_load_balancer(
        name: str, region: str, forwarding_rules: str, droplet_ids: str = "",
        tag: str = "", redirect_http_to_https: bool = False,
        health_check_protocol: str = "http", health_check_port: int = 80,
        health_check_path: str = "/", vpc_uuid: str = "",
    ) -> str:
        """Create a new load balancer.

        Args:
            name: Load balancer name
            region: Region slug (e.g., 'syd1')
            forwarding_rules: JSON array of forwarding rules. Each rule:
                {"entry_protocol":"http","entry_port":80,"target_protocol":"http","target_port":80}
                Protocols: http, https, http2, http3, tcp, udp
            droplet_ids: Comma-separated droplet IDs to add (optional)
            tag: Tag name - LB applies to all droplets with this tag (optional)
            redirect_http_to_https: Redirect HTTP to HTTPS (default false)
            health_check_protocol: Health check protocol: http, https, tcp (default 'http')
            health_check_port: Health check port (default 80)
            health_check_path: Health check path (default '/')
            vpc_uuid: VPC UUID (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {
                "name": name, "region": region,
                "forwarding_rules": json.loads(forwarding_rules),
                "redirect_http_to_https": redirect_http_to_https,
                "health_check": {"protocol": health_check_protocol, "port": health_check_port, "path": health_check_path},
            }
            if droplet_ids:
                body["droplet_ids"] = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            if tag:
                body["tag"] = tag
            if vpc_uuid:
                body["vpc_uuid"] = vpc_uuid
            data = await do_config.do_request("POST", "/load_balancers", json_body=body)
            lb = data.get("load_balancer", {})
            return json.dumps({"id": lb.get("id"), "name": lb.get("name"), "ip": lb.get("ip", "pending"),
                "status": lb.get("status"), "message": "Load balancer creation initiated."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in forwarding_rules: {str(e)}"
        except Exception as e:
            return f"Error creating load balancer: {str(e)}"

    @mcp.tool(
        name="digitalocean_update_load_balancer",
        annotations={"title": "Update Load Balancer", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_update_load_balancer(
        lb_id: str, name: str, region: str, forwarding_rules: str,
        droplet_ids: str = "", tag: str = "", redirect_http_to_https: bool = False,
    ) -> str:
        """Update a load balancer. Replaces the entire configuration.

        Args:
            lb_id: Load balancer UUID
            name: Load balancer name
            region: Region slug
            forwarding_rules: JSON array of ALL forwarding rules (replaces existing)
            droplet_ids: Comma-separated droplet IDs (optional)
            tag: Tag name (optional)
            redirect_http_to_https: Redirect HTTP to HTTPS (default false)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "region": region, "forwarding_rules": json.loads(forwarding_rules),
                "redirect_http_to_https": redirect_http_to_https}
            if droplet_ids:
                body["droplet_ids"] = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            if tag:
                body["tag"] = tag
            data = await do_config.do_request("PUT", f"/load_balancers/{lb_id}", json_body=body)
            lb = data.get("load_balancer", {})
            return json.dumps({"id": lb.get("id"), "name": lb.get("name"),
                "status": lb.get("status"), "message": "Load balancer updated."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in forwarding_rules: {str(e)}"
        except Exception as e:
            return f"Error updating load balancer {lb_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_load_balancer",
        annotations={"title": "Delete Load Balancer", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_load_balancer(lb_id: str) -> str:
        """Delete a load balancer.

        Args:
            lb_id: Load balancer UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/load_balancers/{lb_id}")
            return json.dumps({"status": "success", "message": f"Load balancer {lb_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting load balancer {lb_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_add_load_balancer_droplets",
        annotations={"title": "Add Droplets to LB", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_add_load_balancer_droplets(lb_id: str, droplet_ids: str) -> str:
        """Add droplets to a load balancer.

        Args:
            lb_id: Load balancer UUID
            droplet_ids: Comma-separated droplet IDs
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            ids = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            await do_config.do_request("POST", f"/load_balancers/{lb_id}/droplets", json_body={"droplet_ids": ids})
            return json.dumps({"status": "success", "message": f"Added {len(ids)} droplet(s) to load balancer."}, indent=2)
        except Exception as e:
            return f"Error adding droplets to load balancer: {str(e)}"

    @mcp.tool(
        name="digitalocean_remove_load_balancer_droplets",
        annotations={"title": "Remove Droplets from LB", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_remove_load_balancer_droplets(lb_id: str, droplet_ids: str) -> str:
        """Remove droplets from a load balancer.

        Args:
            lb_id: Load balancer UUID
            droplet_ids: Comma-separated droplet IDs
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            ids = [int(x.strip()) for x in droplet_ids.split(",") if x.strip()]
            await do_config.do_request("DELETE", f"/load_balancers/{lb_id}/droplets", json_body={"droplet_ids": ids})
            return json.dumps({"status": "success", "message": f"Removed {len(ids)} droplet(s) from load balancer."}, indent=2)
        except Exception as e:
            return f"Error removing droplets from load balancer: {str(e)}"

    # =========================================================================
    # DATABASES
    # =========================================================================

    @mcp.tool(
        name="digitalocean_list_database_clusters",
        annotations={"title": "List Database Clusters", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_database_clusters() -> str:
        """List all managed database clusters (PostgreSQL, MySQL, Redis, MongoDB, Kafka)."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/databases", params={"per_page": 100})
            clusters = [format_database_summary(db) for db in data.get("databases", [])]
            return json.dumps({"database_clusters": clusters}, indent=2)
        except Exception as e:
            return f"Error listing database clusters: {str(e)}"

    @mcp.tool(
        name="digitalocean_get_database_cluster",
        annotations={"title": "Get Database Cluster", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_get_database_cluster(db_id: str) -> str:
        """Get detailed information about a managed database cluster.

        Args:
            db_id: Database cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/databases/{db_id}")
            db = data.get("database", {})
            result = format_database_summary(db)
            result["connection"] = db.get("connection", {})
            result["private_connection"] = db.get("private_connection", {})
            result["maintenance_window"] = db.get("maintenance_window", {})
            result["db_names"] = db.get("db_names", [])
            result["users"] = [{"name": u.get("name", "")} for u in db.get("users", [])]
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error getting database cluster {db_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_create_database_cluster",
        annotations={"title": "Create Database Cluster", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_create_database_cluster(
        name: str, engine: str, region: str, size: str, num_nodes: int = 1,
        version: str = "", vpc_uuid: str = "", tags: str = "",
    ) -> str:
        """Create a new managed database cluster.

        Args:
            name: Database cluster name
            engine: Database engine: 'pg' (PostgreSQL), 'mysql', 'redis', 'mongodb', 'kafka'
            region: Region slug (e.g., 'syd1')
            size: Size slug (e.g., 'db-s-1vcpu-1gb')
            num_nodes: Number of nodes (1 for standalone, 3 for HA) (default 1)
            version: Engine version (optional, uses latest if empty)
            vpc_uuid: VPC UUID (optional)
            tags: Comma-separated tags (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "engine": engine, "region": region, "size": size, "num_nodes": num_nodes}
            if version:
                body["version"] = version
            if vpc_uuid:
                body["vpc_uuid"] = vpc_uuid
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            data = await do_config.do_request("POST", "/databases", json_body=body)
            db = data.get("database", {})
            return json.dumps({"id": db.get("id"), "name": db.get("name"), "engine": db.get("engine"),
                "status": db.get("status"), "message": "Database cluster creation initiated. This may take several minutes."}, indent=2)
        except Exception as e:
            return f"Error creating database cluster: {str(e)}"

    @mcp.tool(
        name="digitalocean_delete_database_cluster",
        annotations={"title": "Delete Database Cluster", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_delete_database_cluster(db_id: str) -> str:
        """Delete a managed database cluster. WARNING: All data will be destroyed.

        Args:
            db_id: Database cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/databases/{db_id}")
            return json.dumps({"status": "success", "message": f"Database cluster {db_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting database cluster {db_id}: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_databases",
        annotations={"title": "List Databases in Cluster", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_databases(db_id: str) -> str:
        """List all databases within a managed database cluster.

        Args:
            db_id: Database cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/databases/{db_id}/dbs")
            dbs = [{"name": d.get("name", "")} for d in data.get("dbs", [])]
            return json.dumps({"databases": dbs}, indent=2)
        except Exception as e:
            return f"Error listing databases: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_database_users",
        annotations={"title": "List Database Users", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_database_users(db_id: str) -> str:
        """List all users for a managed database cluster.

        Args:
            db_id: Database cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/databases/{db_id}/users")
            users = [{"name": u.get("name", ""), "role": u.get("role", "")} for u in data.get("users", [])]
            return json.dumps({"users": users}, indent=2)
        except Exception as e:
            return f"Error listing database users: {str(e)}"

    @mcp.tool(
        name="digitalocean_add_database_user",
        annotations={"title": "Add Database User", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def digitalocean_add_database_user(db_id: str, name: str) -> str:
        """Add a new user to a managed database cluster.

        Args:
            db_id: Database cluster UUID
            name: Username for the new database user
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", f"/databases/{db_id}/users", json_body={"name": name})
            user = data.get("user", {})
            return json.dumps({"name": user.get("name"), "role": user.get("role"),
                "password": user.get("password", "See connection details"),
                "message": "Database user created."}, indent=2)
        except Exception as e:
            return f"Error adding database user: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_database_pools",
        annotations={"title": "List Connection Pools", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_database_pools(db_id: str) -> str:
        """List connection pools for a PostgreSQL database cluster.

        Args:
            db_id: Database cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/databases/{db_id}/pools")
            pools = [{"name": p.get("name", ""), "mode": p.get("mode", ""), "size": p.get("size"),
                "db": p.get("db", ""), "user": p.get("user", ""),
                "connection": p.get("connection", {})} for p in data.get("pools", [])]
            return json.dumps({"pools": pools}, indent=2)
        except Exception as e:
            return f"Error listing connection pools: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_database_replicas",
        annotations={"title": "List Database Replicas", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_database_replicas(db_id: str) -> str:
        """List read replicas for a database cluster.

        Args:
            db_id: Database cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/databases/{db_id}/replicas")
            replicas = [{"name": r.get("name", ""), "region": r.get("region", ""), "size": r.get("size", ""),
                "status": r.get("status", ""), "connection": r.get("connection", {}),
                "created_at": r.get("created_at", "")} for r in data.get("replicas", [])]
            return json.dumps({"replicas": replicas}, indent=2)
        except Exception as e:
            return f"Error listing database replicas: {str(e)}"

    @mcp.tool(
        name="digitalocean_list_database_firewall_rules",
        annotations={"title": "List DB Firewall Rules", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_list_database_firewall_rules(db_id: str) -> str:
        """List firewall (trusted sources) rules for a database cluster.

        Args:
            db_id: Database cluster UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/databases/{db_id}/firewall")
            rules = [{"uuid": r.get("uuid", ""), "type": r.get("type", ""), "value": r.get("value", ""),
                "created_at": r.get("created_at", "")} for r in data.get("rules", [])]
            return json.dumps({"rules": rules}, indent=2)
        except Exception as e:
            return f"Error listing database firewall rules: {str(e)}"

    @mcp.tool(
        name="digitalocean_update_database_firewall",
        annotations={"title": "Update DB Firewall Rules", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_update_database_firewall(db_id: str, rules: str) -> str:
        """Update firewall (trusted sources) rules for a database cluster. Replaces all existing rules.

        Args:
            db_id: Database cluster UUID
            rules: JSON array of rules. Each rule: {"type":"ip_addr","value":"1.2.3.4"}
                Type can be: 'ip_addr', 'droplet', 'k8s', 'tag', 'app'
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("PUT", f"/databases/{db_id}/firewall",
                json_body={"rules": json.loads(rules)})
            return json.dumps({"status": "success", "message": "Database firewall rules updated."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in rules: {str(e)}"
        except Exception as e:
            return f"Error updating database firewall: {str(e)}"

    @mcp.tool(
        name="digitalocean_resize_database_cluster",
        annotations={"title": "Resize Database Cluster", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def digitalocean_resize_database_cluster(db_id: str, size: str, num_nodes: int = 0) -> str:
        """Resize a managed database cluster.

        Args:
            db_id: Database cluster UUID
            size: New size slug (e.g., 'db-s-2vcpu-4gb')
            num_nodes: New number of nodes (0 to keep current)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"size": size}
            if num_nodes > 0:
                body["num_nodes"] = num_nodes
            await do_config.do_request("PUT", f"/databases/{db_id}/resize", json_body=body)
            return json.dumps({"status": "success", "message": f"Database cluster resize to {size} initiated."}, indent=2)
        except Exception as e:
            return f"Error resizing database cluster: {str(e)}"

    # =========================================================================
    # PROJECTS
    # =========================================================================

    @mcp.tool(name="digitalocean_list_projects", annotations={"title": "List Projects", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_projects() -> str:
        """List all DigitalOcean projects."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/projects", params={"per_page": 100})
            projects = [{"id": p.get("id", ""), "name": p.get("name", ""), "description": p.get("description", ""),
                "purpose": p.get("purpose", ""), "environment": p.get("environment", ""),
                "is_default": p.get("is_default", False), "created_at": p.get("created_at", "")}
                for p in data.get("projects", [])]
            return json.dumps({"projects": projects}, indent=2)
        except Exception as e:
            return f"Error listing projects: {str(e)}"

    @mcp.tool(name="digitalocean_get_project", annotations={"title": "Get Project", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_project(project_id: str) -> str:
        """Get details for a specific project.

        Args:
            project_id: Project UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/projects/{project_id}")
            p = data.get("project", {})
            return json.dumps({"id": p.get("id", ""), "name": p.get("name", ""), "description": p.get("description", ""),
                "purpose": p.get("purpose", ""), "environment": p.get("environment", ""),
                "is_default": p.get("is_default", False), "created_at": p.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting project {project_id}: {str(e)}"

    @mcp.tool(name="digitalocean_create_project", annotations={"title": "Create Project", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_project(name: str, purpose: str, description: str = "", environment: str = "Development") -> str:
        """Create a new project for organizing resources.

        Args:
            name: Project name
            purpose: Project purpose (e.g., 'Website or blog', 'Service or API', 'Operational / Developer tooling')
            description: Project description (optional)
            environment: Environment: 'Development', 'Staging', 'Production' (default 'Development')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "purpose": purpose, "description": description, "environment": environment}
            data = await do_config.do_request("POST", "/projects", json_body=body)
            p = data.get("project", {})
            return json.dumps({"id": p.get("id"), "name": p.get("name"), "message": "Project created."}, indent=2)
        except Exception as e:
            return f"Error creating project: {str(e)}"

    @mcp.tool(name="digitalocean_update_project", annotations={"title": "Update Project", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_update_project(project_id: str, name: str = "", description: str = "", purpose: str = "", environment: str = "", is_default: bool = False) -> str:
        """Update a project's details.

        Args:
            project_id: Project UUID
            name: New name (optional)
            description: New description (optional)
            purpose: New purpose (optional)
            environment: New environment (optional)
            is_default: Set as default project (default false)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {}
            if name: body["name"] = name
            if description: body["description"] = description
            if purpose: body["purpose"] = purpose
            if environment: body["environment"] = environment
            if is_default: body["is_default"] = True
            data = await do_config.do_request("PATCH", f"/projects/{project_id}", json_body=body)
            p = data.get("project", {})
            return json.dumps({"id": p.get("id"), "name": p.get("name"), "message": "Project updated."}, indent=2)
        except Exception as e:
            return f"Error updating project {project_id}: {str(e)}"

    @mcp.tool(name="digitalocean_list_project_resources", annotations={"title": "List Project Resources", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_project_resources(project_id: str) -> str:
        """List all resources assigned to a project.

        Args:
            project_id: Project UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/projects/{project_id}/resources", params={"per_page": 200})
            resources = [{"urn": r.get("urn", ""), "assigned_at": r.get("assigned_at", ""),
                "status": r.get("status", "")} for r in data.get("resources", [])]
            return json.dumps({"resources": resources}, indent=2)
        except Exception as e:
            return f"Error listing project resources: {str(e)}"

    @mcp.tool(name="digitalocean_assign_project_resources", annotations={"title": "Assign Resources to Project", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_assign_project_resources(project_id: str, urns: str) -> str:
        """Assign resources to a project using URNs.

        Args:
            project_id: Project UUID
            urns: Comma-separated URNs (e.g., 'do:droplet:123,do:volume:abc-123,do:domain:example.com')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            urn_list = [u.strip() for u in urns.split(",") if u.strip()]
            data = await do_config.do_request("POST", f"/projects/{project_id}/resources",
                json_body={"resources": urn_list})
            return json.dumps({"status": "success", "message": f"Assigned {len(urn_list)} resource(s) to project."}, indent=2)
        except Exception as e:
            return f"Error assigning resources: {str(e)}"

    # =========================================================================
    # SSH KEYS
    # =========================================================================

    @mcp.tool(name="digitalocean_list_ssh_keys", annotations={"title": "List SSH Keys", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_ssh_keys() -> str:
        """List all SSH keys on the account."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/account/keys", params={"per_page": 200})
            keys = [{"id": k.get("id"), "name": k.get("name", ""), "fingerprint": k.get("fingerprint", ""),
                "public_key": k.get("public_key", "")[:80] + "..."} for k in data.get("ssh_keys", [])]
            return json.dumps({"ssh_keys": keys}, indent=2)
        except Exception as e:
            return f"Error listing SSH keys: {str(e)}"

    @mcp.tool(name="digitalocean_get_ssh_key", annotations={"title": "Get SSH Key", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_ssh_key(key_id: str) -> str:
        """Get details of an SSH key by ID or fingerprint.

        Args:
            key_id: SSH key ID (numeric) or fingerprint
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/account/keys/{key_id}")
            k = data.get("ssh_key", {})
            return json.dumps({"id": k.get("id"), "name": k.get("name", ""), "fingerprint": k.get("fingerprint", ""),
                "public_key": k.get("public_key", "")}, indent=2)
        except Exception as e:
            return f"Error getting SSH key: {str(e)}"

    @mcp.tool(name="digitalocean_create_ssh_key", annotations={"title": "Create SSH Key", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_ssh_key(name: str, public_key: str) -> str:
        """Add an SSH public key to the account.

        Args:
            name: Name for the SSH key
            public_key: Full SSH public key string (e.g., 'ssh-rsa AAAA... user@host')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", "/account/keys", json_body={"name": name, "public_key": public_key})
            k = data.get("ssh_key", {})
            return json.dumps({"id": k.get("id"), "name": k.get("name"), "fingerprint": k.get("fingerprint"),
                "message": "SSH key added."}, indent=2)
        except Exception as e:
            return f"Error creating SSH key: {str(e)}"

    @mcp.tool(name="digitalocean_delete_ssh_key", annotations={"title": "Delete SSH Key", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_ssh_key(key_id: str) -> str:
        """Delete an SSH key from the account.

        Args:
            key_id: SSH key ID or fingerprint
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/account/keys/{key_id}")
            return json.dumps({"status": "success", "message": f"SSH key {key_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting SSH key: {str(e)}"

    # =========================================================================
    # SNAPSHOTS
    # =========================================================================

    @mcp.tool(name="digitalocean_list_snapshots", annotations={"title": "List Snapshots", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_snapshots(resource_type: str = "") -> str:
        """List all snapshots (droplet and volume snapshots).

        Args:
            resource_type: Filter by type: 'droplet' or 'volume' (optional, empty=all)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            params = {"per_page": 200}
            if resource_type:
                params["resource_type"] = resource_type
            data = await do_config.do_request("GET", "/snapshots", params=params)
            snapshots = [{"id": s.get("id"), "name": s.get("name", ""), "resource_type": s.get("resource_type", ""),
                "resource_id": s.get("resource_id", ""), "size_gigabytes": s.get("size_gigabytes"),
                "min_disk_size": s.get("min_disk_size"), "regions": s.get("regions", []),
                "created_at": s.get("created_at", "")} for s in data.get("snapshots", [])]
            return json.dumps({"snapshots": snapshots}, indent=2)
        except Exception as e:
            return f"Error listing snapshots: {str(e)}"

    @mcp.tool(name="digitalocean_get_snapshot", annotations={"title": "Get Snapshot", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_snapshot(snapshot_id: str) -> str:
        """Get details of a specific snapshot.

        Args:
            snapshot_id: Snapshot ID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/snapshots/{snapshot_id}")
            s = data.get("snapshot", {})
            return json.dumps({"id": s.get("id"), "name": s.get("name", ""), "resource_type": s.get("resource_type", ""),
                "resource_id": s.get("resource_id", ""), "size_gigabytes": s.get("size_gigabytes"),
                "min_disk_size": s.get("min_disk_size"), "regions": s.get("regions", []),
                "created_at": s.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting snapshot {snapshot_id}: {str(e)}"

    @mcp.tool(name="digitalocean_delete_snapshot", annotations={"title": "Delete Snapshot", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_snapshot(snapshot_id: str) -> str:
        """Delete a snapshot permanently.

        Args:
            snapshot_id: Snapshot ID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/snapshots/{snapshot_id}")
            return json.dumps({"status": "success", "message": f"Snapshot {snapshot_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting snapshot {snapshot_id}: {str(e)}"

    # =========================================================================
    # VPCs
    # =========================================================================

    @mcp.tool(name="digitalocean_list_vpcs", annotations={"title": "List VPCs", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_vpcs() -> str:
        """List all VPCs (Virtual Private Clouds)."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/vpcs", params={"per_page": 200})
            vpcs = [{"id": v.get("id", ""), "name": v.get("name", ""), "description": v.get("description", ""),
                "region": v.get("region", ""), "ip_range": v.get("ip_range", ""),
                "default": v.get("default", False), "created_at": v.get("created_at", "")}
                for v in data.get("vpcs", [])]
            return json.dumps({"vpcs": vpcs}, indent=2)
        except Exception as e:
            return f"Error listing VPCs: {str(e)}"

    @mcp.tool(name="digitalocean_get_vpc", annotations={"title": "Get VPC", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_vpc(vpc_id: str) -> str:
        """Get details of a VPC.

        Args:
            vpc_id: VPC UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/vpcs/{vpc_id}")
            v = data.get("vpc", {})
            return json.dumps({"id": v.get("id", ""), "name": v.get("name", ""), "description": v.get("description", ""),
                "region": v.get("region", ""), "ip_range": v.get("ip_range", ""),
                "default": v.get("default", False), "created_at": v.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting VPC {vpc_id}: {str(e)}"

    @mcp.tool(name="digitalocean_create_vpc", annotations={"title": "Create VPC", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_vpc(name: str, region: str, description: str = "", ip_range: str = "") -> str:
        """Create a new VPC.

        Args:
            name: VPC name
            region: Region slug (e.g., 'syd1')
            description: VPC description (optional)
            ip_range: IP range in CIDR notation (e.g., '10.10.10.0/24'). Auto-assigned if empty.
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "region": region}
            if description: body["description"] = description
            if ip_range: body["ip_range"] = ip_range
            data = await do_config.do_request("POST", "/vpcs", json_body=body)
            v = data.get("vpc", {})
            return json.dumps({"id": v.get("id"), "name": v.get("name"), "ip_range": v.get("ip_range"),
                "message": "VPC created."}, indent=2)
        except Exception as e:
            return f"Error creating VPC: {str(e)}"

    @mcp.tool(name="digitalocean_update_vpc", annotations={"title": "Update VPC", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_update_vpc(vpc_id: str, name: str = "", description: str = "") -> str:
        """Update a VPC's name or description.

        Args:
            vpc_id: VPC UUID
            name: New name (optional)
            description: New description (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {}
            if name: body["name"] = name
            if description: body["description"] = description
            data = await do_config.do_request("PATCH", f"/vpcs/{vpc_id}", json_body=body)
            v = data.get("vpc", {})
            return json.dumps({"id": v.get("id"), "name": v.get("name"), "message": "VPC updated."}, indent=2)
        except Exception as e:
            return f"Error updating VPC {vpc_id}: {str(e)}"

    @mcp.tool(name="digitalocean_delete_vpc", annotations={"title": "Delete VPC", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_vpc(vpc_id: str) -> str:
        """Delete a VPC. All resources must be removed from the VPC first.

        Args:
            vpc_id: VPC UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/vpcs/{vpc_id}")
            return json.dumps({"status": "success", "message": f"VPC {vpc_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting VPC {vpc_id}: {str(e)}"

    @mcp.tool(name="digitalocean_list_vpc_members", annotations={"title": "List VPC Members", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_vpc_members(vpc_id: str, resource_type: str = "") -> str:
        """List all resources in a VPC.

        Args:
            vpc_id: VPC UUID
            resource_type: Filter by type: 'droplet', 'load_balancer', 'database' (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            params = {"per_page": 200}
            if resource_type:
                params["resource_type"] = resource_type
            data = await do_config.do_request("GET", f"/vpcs/{vpc_id}/members", params=params)
            members = [{"urn": m.get("urn", ""), "name": m.get("name", ""), "created_at": m.get("created_at", "")}
                for m in data.get("members", [])]
            return json.dumps({"members": members}, indent=2)
        except Exception as e:
            return f"Error listing VPC members: {str(e)}"

    # =========================================================================
    # IMAGES
    # =========================================================================

    @mcp.tool(name="digitalocean_list_images", annotations={"title": "List Images", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_images(image_type: str = "", private: bool = False, per_page: int = 50, page: int = 1) -> str:
        """List available images (OS distributions, snapshots, backups, custom images).

        Args:
            image_type: Filter: 'distribution', 'application', or '' for all (default '')
            private: Show only private/custom images (default false)
            per_page: Results per page (default 50)
            page: Page number (default 1)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            params = {"per_page": min(per_page, 200), "page": page}
            if image_type:
                params["type"] = image_type
            if private:
                params["private"] = "true"
            data = await do_config.do_request("GET", "/images", params=params)
            images = [{"id": i.get("id"), "name": i.get("name", ""), "slug": i.get("slug", ""),
                "distribution": i.get("distribution", ""), "type": i.get("type", ""),
                "public": i.get("public", False), "regions": i.get("regions", []),
                "min_disk_size": i.get("min_disk_size"), "size_gigabytes": i.get("size_gigabytes"),
                "status": i.get("status", ""), "created_at": i.get("created_at", "")}
                for i in data.get("images", [])]
            meta = data.get("meta", {})
            return json.dumps({"total": meta.get("total", len(images)), "page": page, "images": images}, indent=2)
        except Exception as e:
            return f"Error listing images: {str(e)}"

    @mcp.tool(name="digitalocean_get_image", annotations={"title": "Get Image", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_image(image_id: str) -> str:
        """Get details of an image by ID or slug.

        Args:
            image_id: Image ID (numeric) or slug (e.g., 'ubuntu-24-04-x64')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/images/{image_id}")
            i = data.get("image", {})
            return json.dumps({"id": i.get("id"), "name": i.get("name", ""), "slug": i.get("slug", ""),
                "distribution": i.get("distribution", ""), "type": i.get("type", ""),
                "public": i.get("public", False), "regions": i.get("regions", []),
                "min_disk_size": i.get("min_disk_size"), "size_gigabytes": i.get("size_gigabytes"),
                "description": i.get("description", ""), "status": i.get("status", ""),
                "created_at": i.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting image {image_id}: {str(e)}"

    @mcp.tool(name="digitalocean_update_image", annotations={"title": "Update Image", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_update_image(image_id: int, name: str = "", description: str = "", distribution: str = "") -> str:
        """Update a custom image's metadata.

        Args:
            image_id: Image ID (numeric)
            name: New name (optional)
            description: New description (optional)
            distribution: New distribution label (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {}
            if name: body["name"] = name
            if description: body["description"] = description
            if distribution: body["distribution"] = distribution
            data = await do_config.do_request("PUT", f"/images/{image_id}", json_body=body)
            i = data.get("image", {})
            return json.dumps({"id": i.get("id"), "name": i.get("name"), "message": "Image updated."}, indent=2)
        except Exception as e:
            return f"Error updating image {image_id}: {str(e)}"

    @mcp.tool(name="digitalocean_delete_image", annotations={"title": "Delete Image", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_image(image_id: int) -> str:
        """Delete a custom image.

        Args:
            image_id: Image ID (numeric)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/images/{image_id}")
            return json.dumps({"status": "success", "message": f"Image {image_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting image {image_id}: {str(e)}"

    # =========================================================================
    # RESERVED IPs
    # =========================================================================

    @mcp.tool(name="digitalocean_list_reserved_ips", annotations={"title": "List Reserved IPs", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_reserved_ips() -> str:
        """List all reserved (floating) IPs."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/reserved_ips", params={"per_page": 200})
            ips = [{"ip": r.get("ip", ""), "region": r.get("region", {}).get("slug", ""),
                "droplet": {"id": r.get("droplet", {}).get("id"), "name": r.get("droplet", {}).get("name", "")} if r.get("droplet") else None,
                "locked": r.get("locked", False)} for r in data.get("reserved_ips", [])]
            return json.dumps({"reserved_ips": ips}, indent=2)
        except Exception as e:
            return f"Error listing reserved IPs: {str(e)}"

    @mcp.tool(name="digitalocean_get_reserved_ip", annotations={"title": "Get Reserved IP", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_reserved_ip(ip: str) -> str:
        """Get details of a reserved IP.

        Args:
            ip: The reserved IP address
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/reserved_ips/{ip}")
            r = data.get("reserved_ip", {})
            return json.dumps({"ip": r.get("ip", ""), "region": r.get("region", {}).get("slug", ""),
                "droplet": r.get("droplet"), "locked": r.get("locked", False)}, indent=2)
        except Exception as e:
            return f"Error getting reserved IP {ip}: {str(e)}"

    @mcp.tool(name="digitalocean_create_reserved_ip", annotations={"title": "Create Reserved IP", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_reserved_ip(region: str = "", droplet_id: int = 0) -> str:
        """Create a new reserved IP. Provide either region OR droplet_id.

        Args:
            region: Region slug for unassigned IP (e.g., 'syd1')
            droplet_id: Droplet ID to assign immediately (0 = unassigned)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {}
            if droplet_id > 0:
                body["droplet_id"] = droplet_id
            elif region:
                body["region"] = region
            else:
                return "Error: Provide either region or droplet_id."
            data = await do_config.do_request("POST", "/reserved_ips", json_body=body)
            r = data.get("reserved_ip", {})
            return json.dumps({"ip": r.get("ip", ""), "region": r.get("region", {}).get("slug", ""),
                "message": "Reserved IP created."}, indent=2)
        except Exception as e:
            return f"Error creating reserved IP: {str(e)}"

    @mcp.tool(name="digitalocean_delete_reserved_ip", annotations={"title": "Delete Reserved IP", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_reserved_ip(ip: str) -> str:
        """Delete a reserved IP. Must be unassigned first.

        Args:
            ip: Reserved IP address to delete
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/reserved_ips/{ip}")
            return json.dumps({"status": "success", "message": f"Reserved IP {ip} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting reserved IP {ip}: {str(e)}"

    @mcp.tool(name="digitalocean_assign_reserved_ip", annotations={"title": "Assign Reserved IP", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_assign_reserved_ip(ip: str, droplet_id: int) -> str:
        """Assign a reserved IP to a droplet.

        Args:
            ip: Reserved IP address
            droplet_id: Droplet ID to assign to
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", f"/reserved_ips/{ip}/actions",
                json_body={"type": "assign", "droplet_id": droplet_id})
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": f"Reserved IP {ip} assigned to droplet {droplet_id}."}, indent=2)
        except Exception as e:
            return f"Error assigning reserved IP: {str(e)}"

    @mcp.tool(name="digitalocean_unassign_reserved_ip", annotations={"title": "Unassign Reserved IP", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_unassign_reserved_ip(ip: str) -> str:
        """Unassign a reserved IP from its current droplet.

        Args:
            ip: Reserved IP address
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", f"/reserved_ips/{ip}/actions",
                json_body={"type": "unassign"})
            act = data.get("action", {})
            return json.dumps({"action_id": act.get("id"), "status": act.get("status"),
                "message": f"Reserved IP {ip} unassigned."}, indent=2)
        except Exception as e:
            return f"Error unassigning reserved IP: {str(e)}"

    # =========================================================================
    # TAGS
    # =========================================================================

    @mcp.tool(name="digitalocean_list_tags", annotations={"title": "List Tags", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_tags() -> str:
        """List all tags with resource counts."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/tags", params={"per_page": 200})
            tags = [{"name": t.get("name", ""), "resources": t.get("resources", {}).get("count", 0)}
                for t in data.get("tags", [])]
            return json.dumps({"tags": tags}, indent=2)
        except Exception as e:
            return f"Error listing tags: {str(e)}"

    @mcp.tool(name="digitalocean_get_tag", annotations={"title": "Get Tag", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_tag(tag_name: str) -> str:
        """Get details of a tag including resource counts by type.

        Args:
            tag_name: Tag name
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/tags/{tag_name}")
            t = data.get("tag", {})
            return json.dumps({"name": t.get("name", ""), "resources": t.get("resources", {})}, indent=2)
        except Exception as e:
            return f"Error getting tag {tag_name}: {str(e)}"

    @mcp.tool(name="digitalocean_create_tag", annotations={"title": "Create Tag", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_create_tag(name: str) -> str:
        """Create a new tag.

        Args:
            name: Tag name (lowercase, alphanumeric, hyphens, colons)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", "/tags", json_body={"name": name})
            t = data.get("tag", {})
            return json.dumps({"name": t.get("name"), "message": "Tag created."}, indent=2)
        except Exception as e:
            return f"Error creating tag: {str(e)}"

    @mcp.tool(name="digitalocean_delete_tag", annotations={"title": "Delete Tag", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_tag(tag_name: str) -> str:
        """Delete a tag. Does not delete the tagged resources.

        Args:
            tag_name: Tag name to delete
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/tags/{tag_name}")
            return json.dumps({"status": "success", "message": f"Tag '{tag_name}' deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting tag {tag_name}: {str(e)}"

    @mcp.tool(name="digitalocean_tag_resources", annotations={"title": "Tag Resources", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_tag_resources(tag_name: str, resources: str) -> str:
        """Apply a tag to resources.

        Args:
            tag_name: Tag name
            resources: JSON array of resources: [{"resource_id":"123","resource_type":"droplet"}]
                resource_type can be: droplet, image, volume, volume_snapshot, database
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            res_list = json.loads(resources)
            await do_config.do_request("POST", f"/tags/{tag_name}/resources", json_body={"resources": res_list})
            return json.dumps({"status": "success", "message": f"Tagged {len(res_list)} resource(s) with '{tag_name}'."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in resources: {str(e)}"
        except Exception as e:
            return f"Error tagging resources: {str(e)}"

    @mcp.tool(name="digitalocean_untag_resources", annotations={"title": "Untag Resources", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_untag_resources(tag_name: str, resources: str) -> str:
        """Remove a tag from resources.

        Args:
            tag_name: Tag name
            resources: JSON array of resources: [{"resource_id":"123","resource_type":"droplet"}]
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            res_list = json.loads(resources)
            await do_config.do_request("DELETE", f"/tags/{tag_name}/resources", json_body={"resources": res_list})
            return json.dumps({"status": "success", "message": f"Untagged {len(res_list)} resource(s) from '{tag_name}'."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in resources: {str(e)}"
        except Exception as e:
            return f"Error untagging resources: {str(e)}"

    # =========================================================================
    # CERTIFICATES
    # =========================================================================

    @mcp.tool(name="digitalocean_list_certificates", annotations={"title": "List Certificates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_certificates() -> str:
        """List all SSL/TLS certificates."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/certificates", params={"per_page": 200})
            certs = [{"id": c.get("id", ""), "name": c.get("name", ""), "type": c.get("type", ""),
                "state": c.get("state", ""), "dns_names": c.get("dns_names", []),
                "not_after": c.get("not_after", ""), "created_at": c.get("created_at", "")}
                for c in data.get("certificates", [])]
            return json.dumps({"certificates": certs}, indent=2)
        except Exception as e:
            return f"Error listing certificates: {str(e)}"

    @mcp.tool(name="digitalocean_get_certificate", annotations={"title": "Get Certificate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_certificate(certificate_id: str) -> str:
        """Get details of a certificate.

        Args:
            certificate_id: Certificate UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/certificates/{certificate_id}")
            c = data.get("certificate", {})
            return json.dumps({"id": c.get("id", ""), "name": c.get("name", ""), "type": c.get("type", ""),
                "state": c.get("state", ""), "dns_names": c.get("dns_names", []),
                "sha1_fingerprint": c.get("sha1_fingerprint", ""),
                "not_after": c.get("not_after", ""), "created_at": c.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting certificate {certificate_id}: {str(e)}"

    @mcp.tool(name="digitalocean_create_certificate", annotations={"title": "Create Certificate", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_certificate(name: str, dns_names: str, cert_type: str = "lets_encrypt") -> str:
        """Create an SSL/TLS certificate (Let's Encrypt auto-managed or custom).

        Args:
            name: Certificate name
            dns_names: Comma-separated domain names (e.g., 'example.com,www.example.com')
            cert_type: Certificate type: 'lets_encrypt' (auto) or 'custom' (default 'lets_encrypt')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "type": cert_type,
                "dns_names": [d.strip() for d in dns_names.split(",") if d.strip()]}
            data = await do_config.do_request("POST", "/certificates", json_body=body)
            c = data.get("certificate", {})
            return json.dumps({"id": c.get("id"), "name": c.get("name"), "state": c.get("state"),
                "message": "Certificate creation initiated."}, indent=2)
        except Exception as e:
            return f"Error creating certificate: {str(e)}"

    @mcp.tool(name="digitalocean_delete_certificate", annotations={"title": "Delete Certificate", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_certificate(certificate_id: str) -> str:
        """Delete a certificate.

        Args:
            certificate_id: Certificate UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/certificates/{certificate_id}")
            return json.dumps({"status": "success", "message": f"Certificate {certificate_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting certificate {certificate_id}: {str(e)}"

    # =========================================================================
    # CDN ENDPOINTS
    # =========================================================================

    @mcp.tool(name="digitalocean_list_cdn_endpoints", annotations={"title": "List CDN Endpoints", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_cdn_endpoints() -> str:
        """List all CDN endpoints."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/cdn/endpoints", params={"per_page": 200})
            endpoints = [{"id": e.get("id", ""), "origin": e.get("origin", ""), "endpoint": e.get("endpoint", ""),
                "custom_domain": e.get("custom_domain", ""), "ttl": e.get("ttl"),
                "certificate_id": e.get("certificate_id", ""), "created_at": e.get("created_at", "")}
                for e in data.get("endpoints", [])]
            return json.dumps({"cdn_endpoints": endpoints}, indent=2)
        except Exception as e:
            return f"Error listing CDN endpoints: {str(e)}"

    @mcp.tool(name="digitalocean_get_cdn_endpoint", annotations={"title": "Get CDN Endpoint", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_cdn_endpoint(endpoint_id: str) -> str:
        """Get details of a CDN endpoint.

        Args:
            endpoint_id: CDN endpoint UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/cdn/endpoints/{endpoint_id}")
            e = data.get("endpoint", {})
            return json.dumps({"id": e.get("id", ""), "origin": e.get("origin", ""), "endpoint": e.get("endpoint", ""),
                "custom_domain": e.get("custom_domain", ""), "ttl": e.get("ttl"),
                "certificate_id": e.get("certificate_id", ""), "created_at": e.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting CDN endpoint {endpoint_id}: {str(e)}"

    @mcp.tool(name="digitalocean_create_cdn_endpoint", annotations={"title": "Create CDN Endpoint", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_cdn_endpoint(origin: str, ttl: int = 3600, custom_domain: str = "", certificate_id: str = "") -> str:
        """Create a new CDN endpoint.

        Args:
            origin: Origin server (Spaces bucket endpoint, e.g., 'my-space.syd1.digitaloceanspaces.com')
            ttl: Cache TTL in seconds (default 3600)
            custom_domain: Custom domain for CDN (optional)
            certificate_id: SSL certificate UUID for custom domain (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"origin": origin, "ttl": ttl}
            if custom_domain: body["custom_domain"] = custom_domain
            if certificate_id: body["certificate_id"] = certificate_id
            data = await do_config.do_request("POST", "/cdn/endpoints", json_body=body)
            e = data.get("endpoint", {})
            return json.dumps({"id": e.get("id"), "endpoint": e.get("endpoint"),
                "message": "CDN endpoint created."}, indent=2)
        except Exception as e:
            return f"Error creating CDN endpoint: {str(e)}"

    @mcp.tool(name="digitalocean_update_cdn_endpoint", annotations={"title": "Update CDN Endpoint", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_update_cdn_endpoint(endpoint_id: str, ttl: int = -1, custom_domain: str = "", certificate_id: str = "") -> str:
        """Update a CDN endpoint.

        Args:
            endpoint_id: CDN endpoint UUID
            ttl: New cache TTL in seconds (use -1 to skip)
            custom_domain: New custom domain (optional)
            certificate_id: New SSL certificate UUID (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {}
            if ttl >= 0: body["ttl"] = ttl
            if custom_domain: body["custom_domain"] = custom_domain
            if certificate_id: body["certificate_id"] = certificate_id
            data = await do_config.do_request("PUT", f"/cdn/endpoints/{endpoint_id}", json_body=body)
            e = data.get("endpoint", {})
            return json.dumps({"id": e.get("id"), "endpoint": e.get("endpoint"),
                "message": "CDN endpoint updated."}, indent=2)
        except Exception as e:
            return f"Error updating CDN endpoint {endpoint_id}: {str(e)}"

    @mcp.tool(name="digitalocean_delete_cdn_endpoint", annotations={"title": "Delete CDN Endpoint", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_cdn_endpoint(endpoint_id: str) -> str:
        """Delete a CDN endpoint.

        Args:
            endpoint_id: CDN endpoint UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/cdn/endpoints/{endpoint_id}")
            return json.dumps({"status": "success", "message": f"CDN endpoint {endpoint_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting CDN endpoint {endpoint_id}: {str(e)}"

    # =========================================================================
    # CONTAINER REGISTRY
    # =========================================================================

    @mcp.tool(name="digitalocean_get_registry", annotations={"title": "Get Container Registry", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_registry() -> str:
        """Get container registry information for the account."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/registry")
            r = data.get("registry", {})
            return json.dumps({"name": r.get("name", ""), "storage_usage_bytes": r.get("storage_usage_bytes"),
                "storage_usage_bytes_updated_at": r.get("storage_usage_bytes_updated_at", ""),
                "subscription_tier_slug": r.get("subscription", {}).get("tier", {}).get("slug", ""),
                "created_at": r.get("created_at", ""), "region": r.get("region", "")}, indent=2)
        except Exception as e:
            return f"Error getting registry: {str(e)}"

    @mcp.tool(name="digitalocean_list_registry_repositories", annotations={"title": "List Registry Repos", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_registry_repositories(registry_name: str) -> str:
        """List repositories in a container registry.

        Args:
            registry_name: Registry name
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/registry/{registry_name}/repositoriesV2", params={"per_page": 100})
            repos = [{"name": r.get("name", ""), "tag_count": r.get("tag_count"),
                "manifest_count": r.get("manifest_count"), "latest_manifest": r.get("latest_manifest", {}).get("digest", ""),
                "latest_tag": r.get("latest_manifest", {}).get("tags", [None])[0] if r.get("latest_manifest", {}).get("tags") else None}
                for r in data.get("repositories", [])]
            return json.dumps({"repositories": repos}, indent=2)
        except Exception as e:
            return f"Error listing repositories: {str(e)}"

    @mcp.tool(name="digitalocean_list_registry_tags", annotations={"title": "List Registry Tags", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_registry_tags(registry_name: str, repository: str) -> str:
        """List tags for a repository in the container registry.

        Args:
            registry_name: Registry name
            repository: Repository name
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/registry/{registry_name}/repositories/{repository}/tags",
                params={"per_page": 100})
            tags = [{"tag": t.get("tag", ""), "manifest_digest": t.get("manifest_digest", ""),
                "compressed_size": t.get("compressed_size"), "size_bytes": t.get("size_bytes"),
                "updated_at": t.get("updated_at", "")} for t in data.get("tags", [])]
            return json.dumps({"tags": tags}, indent=2)
        except Exception as e:
            return f"Error listing tags: {str(e)}"

    @mcp.tool(name="digitalocean_delete_registry_tag", annotations={"title": "Delete Registry Tag", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_registry_tag(registry_name: str, repository: str, tag: str) -> str:
        """Delete a tag from a container registry repository.

        Args:
            registry_name: Registry name
            repository: Repository name
            tag: Tag name to delete
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/registry/{registry_name}/repositories/{repository}/tags/{tag}")
            return json.dumps({"status": "success", "message": f"Tag '{tag}' deleted. Run garbage collection to free storage."}, indent=2)
        except Exception as e:
            return f"Error deleting tag: {str(e)}"

    @mcp.tool(name="digitalocean_run_registry_gc", annotations={"title": "Run Registry GC", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_run_registry_gc(registry_name: str) -> str:
        """Run garbage collection on the container registry to free unused storage.

        Args:
            registry_name: Registry name
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", f"/registry/{registry_name}/garbage-collection")
            gc = data.get("garbage_collection", {})
            return json.dumps({"uuid": gc.get("uuid", ""), "status": gc.get("status", ""),
                "type": gc.get("type", ""), "created_at": gc.get("created_at", ""),
                "message": "Garbage collection started."}, indent=2)
        except Exception as e:
            return f"Error running garbage collection: {str(e)}"

    # =========================================================================
    # APPS (APP PLATFORM)
    # =========================================================================

    @mcp.tool(name="digitalocean_list_apps", annotations={"title": "List Apps", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_apps() -> str:
        """List all App Platform apps."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/apps", params={"per_page": 100})
            apps = [{"id": a.get("id", ""), "name": a.get("spec", {}).get("name", ""),
                "default_ingress": a.get("default_ingress", ""),
                "live_url": a.get("live_url", ""),
                "active_deployment_phase": a.get("active_deployment", {}).get("phase", ""),
                "region": a.get("region", {}).get("slug", ""),
                "tier_slug": a.get("tier_slug", ""),
                "created_at": a.get("created_at", ""), "updated_at": a.get("updated_at", "")}
                for a in data.get("apps", [])]
            return json.dumps({"apps": apps}, indent=2)
        except Exception as e:
            return f"Error listing apps: {str(e)}"

    @mcp.tool(name="digitalocean_get_app", annotations={"title": "Get App Details", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_app(app_id: str) -> str:
        """Get detailed information about an App Platform app.

        Args:
            app_id: App UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/apps/{app_id}")
            a = data.get("app", {})
            spec = a.get("spec", {})
            return json.dumps({"id": a.get("id", ""), "name": spec.get("name", ""),
                "default_ingress": a.get("default_ingress", ""), "live_url": a.get("live_url", ""),
                "region": a.get("region", {}).get("slug", ""), "tier_slug": a.get("tier_slug", ""),
                "active_deployment": {"id": a.get("active_deployment", {}).get("id", ""),
                    "phase": a.get("active_deployment", {}).get("phase", ""),
                    "created_at": a.get("active_deployment", {}).get("created_at", "")},
                "services": [{"name": s.get("name", ""), "source": s.get("github", s.get("git", s.get("image", {})))}
                    for s in spec.get("services", [])],
                "static_sites": [{"name": s.get("name", "")} for s in spec.get("static_sites", [])],
                "workers": [{"name": w.get("name", "")} for w in spec.get("workers", [])],
                "databases": [{"name": d.get("name", ""), "engine": d.get("engine", "")} for d in spec.get("databases", [])],
                "created_at": a.get("created_at", "")}, indent=2)
        except Exception as e:
            return f"Error getting app {app_id}: {str(e)}"

    @mcp.tool(name="digitalocean_create_app", annotations={"title": "Create App", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_app(spec: str) -> str:
        """Create a new App Platform app from a spec.

        Args:
            spec: JSON app spec. See DigitalOcean App Platform documentation for format.
                Minimal example: {"name":"my-app","region":"syd","services":[{"name":"api",
                "github":{"repo":"user/repo","branch":"main"},"run_command":"npm start",
                "http_port":8080,"instance_size_slug":"apps-s-1vcpu-0.5gb","instance_count":1}]}
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("POST", "/apps", json_body={"spec": json.loads(spec)})
            a = data.get("app", {})
            return json.dumps({"id": a.get("id"), "name": a.get("spec", {}).get("name", ""),
                "live_url": a.get("live_url", ""), "message": "App creation initiated."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in spec: {str(e)}"
        except Exception as e:
            return f"Error creating app: {str(e)}"

    @mcp.tool(name="digitalocean_update_app", annotations={"title": "Update App", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_update_app(app_id: str, spec: str) -> str:
        """Update an App Platform app's spec (triggers redeployment).

        Args:
            app_id: App UUID
            spec: Updated JSON app spec (replaces existing)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("PUT", f"/apps/{app_id}", json_body={"spec": json.loads(spec)})
            a = data.get("app", {})
            return json.dumps({"id": a.get("id"), "name": a.get("spec", {}).get("name", ""),
                "message": "App updated. Redeployment triggered."}, indent=2)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in spec: {str(e)}"
        except Exception as e:
            return f"Error updating app {app_id}: {str(e)}"

    @mcp.tool(name="digitalocean_delete_app", annotations={"title": "Delete App", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_app(app_id: str) -> str:
        """Delete an App Platform app and all its resources.

        Args:
            app_id: App UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/apps/{app_id}")
            return json.dumps({"status": "success", "message": f"App {app_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting app {app_id}: {str(e)}"

    @mcp.tool(name="digitalocean_list_app_deployments", annotations={"title": "List App Deployments", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_app_deployments(app_id: str) -> str:
        """List deployments for an App Platform app.

        Args:
            app_id: App UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/apps/{app_id}/deployments", params={"per_page": 20})
            deployments = [{"id": d.get("id", ""), "phase": d.get("phase", ""),
                "cause": d.get("cause", ""), "progress": d.get("progress", {}).get("steps", []),
                "created_at": d.get("created_at", ""), "updated_at": d.get("updated_at", "")}
                for d in data.get("deployments", [])]
            return json.dumps({"deployments": deployments}, indent=2)
        except Exception as e:
            return f"Error listing deployments: {str(e)}"

    @mcp.tool(name="digitalocean_get_app_logs", annotations={"title": "Get App Logs", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_app_logs(app_id: str, deployment_id: str = "", component_name: str = "", log_type: str = "RUN") -> str:
        """Get logs for an App Platform app.

        Args:
            app_id: App UUID
            deployment_id: Deployment ID (optional, latest if empty)
            component_name: Component name (optional, all if empty)
            log_type: Log type: 'BUILD', 'DEPLOY', 'RUN', 'RUN_RESTARTED' (default 'RUN')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            endpoint = f"/apps/{app_id}"
            if deployment_id:
                endpoint += f"/deployments/{deployment_id}"
            if component_name:
                endpoint += f"/components/{component_name}"
            endpoint += "/logs"
            data = await do_config.do_request("GET", endpoint, params={"type": log_type, "follow": False})
            return json.dumps({"live_url": data.get("live_url", ""), "historic_urls": data.get("historic_urls", []),
                "message": "Use the URLs to stream or download logs."}, indent=2)
        except Exception as e:
            return f"Error getting app logs: {str(e)}"

    # =========================================================================
    # MONITORING
    # =========================================================================

    @mcp.tool(name="digitalocean_list_alert_policies", annotations={"title": "List Alert Policies", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_alert_policies() -> str:
        """List all monitoring alert policies."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/monitoring/alerts", params={"per_page": 200})
            policies = [{"uuid": p.get("uuid", ""), "type": p.get("type", ""), "description": p.get("description", ""),
                "compare": p.get("compare", ""), "value": p.get("value"), "window": p.get("window", ""),
                "entities": p.get("entities", []), "tags": p.get("tags", []),
                "enabled": p.get("enabled", True)} for p in data.get("policies", [])]
            return json.dumps({"policies": policies}, indent=2)
        except Exception as e:
            return f"Error listing alert policies: {str(e)}"

    @mcp.tool(name="digitalocean_get_alert_policy", annotations={"title": "Get Alert Policy", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_alert_policy(alert_id: str) -> str:
        """Get details of an alert policy.

        Args:
            alert_id: Alert policy UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/monitoring/alerts/{alert_id}")
            p = data.get("policy", {})
            return json.dumps({"uuid": p.get("uuid", ""), "type": p.get("type", ""),
                "description": p.get("description", ""), "compare": p.get("compare", ""),
                "value": p.get("value"), "window": p.get("window", ""),
                "entities": p.get("entities", []), "tags": p.get("tags", []),
                "alerts": p.get("alerts", {}), "enabled": p.get("enabled", True)}, indent=2)
        except Exception as e:
            return f"Error getting alert policy {alert_id}: {str(e)}"

    @mcp.tool(name="digitalocean_create_alert_policy", annotations={"title": "Create Alert Policy", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_alert_policy(
        alert_type: str, description: str, compare: str, value: float, window: str,
        entities: str = "", tags: str = "", emails: str = "", slack_webhooks: str = "",
    ) -> str:
        """Create a monitoring alert policy.

        Args:
            alert_type: Alert type (e.g., 'v1/insights/droplet/cpu', 'v1/insights/droplet/memory_utilization_percent',
                'v1/insights/droplet/disk_utilization_percent', 'v1/insights/droplet/load_1')
            description: Human-readable description
            compare: Comparison: 'GreaterThan' or 'LessThan'
            value: Threshold value (e.g., 90 for 90% CPU)
            window: Time window: '5m', '10m', '30m', '1h'
            entities: Comma-separated droplet IDs (optional, empty = all)
            tags: Comma-separated tags (optional)
            emails: Comma-separated email addresses for notifications (optional)
            slack_webhooks: Comma-separated Slack webhook URLs (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"type": alert_type, "description": description, "compare": compare,
                "value": value, "window": window, "enabled": True}
            if entities:
                body["entities"] = [e.strip() for e in entities.split(",") if e.strip()]
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            alerts = {}
            if emails:
                alerts["email"] = [e.strip() for e in emails.split(",") if e.strip()]
            if slack_webhooks:
                alerts["slack"] = [{"url": u.strip()} for u in slack_webhooks.split(",") if u.strip()]
            if alerts:
                body["alerts"] = alerts
            data = await do_config.do_request("POST", "/monitoring/alerts", json_body=body)
            p = data.get("policy", {})
            return json.dumps({"uuid": p.get("uuid"), "type": p.get("type"),
                "message": "Alert policy created."}, indent=2)
        except Exception as e:
            return f"Error creating alert policy: {str(e)}"

    @mcp.tool(name="digitalocean_update_alert_policy", annotations={"title": "Update Alert Policy", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_update_alert_policy(
        alert_id: str, alert_type: str, description: str, compare: str, value: float,
        window: str, enabled: bool = True, entities: str = "", tags: str = "",
        emails: str = "", slack_webhooks: str = "",
    ) -> str:
        """Update an alert policy. Replaces the entire policy.

        Args:
            alert_id: Alert policy UUID
            alert_type: Alert type
            description: Description
            compare: 'GreaterThan' or 'LessThan'
            value: Threshold value
            window: Time window: '5m', '10m', '30m', '1h'
            enabled: Enable/disable (default true)
            entities: Comma-separated droplet IDs (optional)
            tags: Comma-separated tags (optional)
            emails: Comma-separated notification emails (optional)
            slack_webhooks: Comma-separated Slack webhook URLs (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"type": alert_type, "description": description, "compare": compare,
                "value": value, "window": window, "enabled": enabled}
            if entities:
                body["entities"] = [e.strip() for e in entities.split(",") if e.strip()]
            if tags:
                body["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
            alerts = {}
            if emails:
                alerts["email"] = [e.strip() for e in emails.split(",") if e.strip()]
            if slack_webhooks:
                alerts["slack"] = [{"url": u.strip()} for u in slack_webhooks.split(",") if u.strip()]
            if alerts:
                body["alerts"] = alerts
            data = await do_config.do_request("PUT", f"/monitoring/alerts/{alert_id}", json_body=body)
            p = data.get("policy", {})
            return json.dumps({"uuid": p.get("uuid"), "message": "Alert policy updated."}, indent=2)
        except Exception as e:
            return f"Error updating alert policy {alert_id}: {str(e)}"

    @mcp.tool(name="digitalocean_delete_alert_policy", annotations={"title": "Delete Alert Policy", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_alert_policy(alert_id: str) -> str:
        """Delete an alert policy.

        Args:
            alert_id: Alert policy UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/monitoring/alerts/{alert_id}")
            return json.dumps({"status": "success", "message": f"Alert policy {alert_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting alert policy {alert_id}: {str(e)}"

    @mcp.tool(name="digitalocean_get_droplet_metrics", annotations={"title": "Get Droplet Metrics", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_droplet_metrics(
        host_id: str, metric_type: str, start: str = "", end: str = "",
        interface: str = "public", direction: str = "inbound",
    ) -> str:
        """Get monitoring metrics for a droplet. Requires monitoring agent installed.

        Args:
            host_id: Droplet ID
            metric_type: Metric type: 'bandwidth', 'cpu', 'memory_free', 'memory_available',
                'filesystem_free', 'filesystem_size', 'disk_read', 'disk_write',
                'load_1', 'load_5', 'load_15'
            start: Start time ISO 8601 (default: 1 hour ago)
            end: End time ISO 8601 (default: now)
            interface: For bandwidth: 'public' or 'private' (default 'public')
            direction: For bandwidth: 'inbound' or 'outbound' (default 'inbound')
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            if not end:
                end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            if not start:
                start = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

            params = {"host_id": host_id, "start": start, "end": end}
            if metric_type == "bandwidth":
                params["interface"] = interface
                params["direction"] = direction

            endpoint = f"/monitoring/metrics/droplet/{metric_type}"
            data = await do_config.do_request("GET", endpoint, params=params)
            result = data.get("data", {}).get("result", [])

            formatted = []
            for series in result:
                values = series.get("values", [])
                formatted.append({
                    "metric": series.get("metric", {}),
                    "data_points": len(values),
                    "latest_value": values[-1][1] if values else None,
                    "values_sample": values[-5:] if len(values) > 5 else values,
                })
            return json.dumps({"metric_type": metric_type, "host_id": host_id,
                "start": start, "end": end, "series": formatted}, indent=2)
        except Exception as e:
            return f"Error getting metrics: {str(e)}"

    # =========================================================================
    # UPTIME CHECKS
    # =========================================================================

    @mcp.tool(name="digitalocean_list_uptime_checks", annotations={"title": "List Uptime Checks", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_uptime_checks() -> str:
        """List all uptime checks."""
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", "/uptime/checks", params={"per_page": 200})
            checks = [{"id": c.get("id", ""), "name": c.get("name", ""), "type": c.get("type", ""),
                "target": c.get("target", ""), "enabled": c.get("enabled", True),
                "regions": c.get("regions", [])} for c in data.get("checks", [])]
            return json.dumps({"checks": checks}, indent=2)
        except Exception as e:
            return f"Error listing uptime checks: {str(e)}"

    @mcp.tool(name="digitalocean_get_uptime_check", annotations={"title": "Get Uptime Check", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_get_uptime_check(check_id: str) -> str:
        """Get details of an uptime check.

        Args:
            check_id: Uptime check UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/uptime/checks/{check_id}")
            c = data.get("check", {})
            return json.dumps({"id": c.get("id", ""), "name": c.get("name", ""), "type": c.get("type", ""),
                "target": c.get("target", ""), "enabled": c.get("enabled", True),
                "regions": c.get("regions", [])}, indent=2)
        except Exception as e:
            return f"Error getting uptime check {check_id}: {str(e)}"

    @mcp.tool(name="digitalocean_create_uptime_check", annotations={"title": "Create Uptime Check", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_uptime_check(
        name: str, target: str, check_type: str = "https", regions: str = "",
    ) -> str:
        """Create a new uptime check.

        Args:
            name: Check name
            target: URL or IP to monitor (e.g., 'https://example.com')
            check_type: Check type: 'ping', 'http', 'https', 'tcp' (default 'https')
            regions: Comma-separated regions: 'us_east', 'us_west', 'eu_west', 'se_asia' (optional, all if empty)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "target": target, "type": check_type, "enabled": True}
            if regions:
                body["regions"] = [r.strip() for r in regions.split(",") if r.strip()]
            data = await do_config.do_request("POST", "/uptime/checks", json_body=body)
            c = data.get("check", {})
            return json.dumps({"id": c.get("id"), "name": c.get("name"),
                "message": "Uptime check created."}, indent=2)
        except Exception as e:
            return f"Error creating uptime check: {str(e)}"

    @mcp.tool(name="digitalocean_update_uptime_check", annotations={"title": "Update Uptime Check", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_update_uptime_check(
        check_id: str, name: str = "", target: str = "", check_type: str = "",
        enabled: bool = True, regions: str = "",
    ) -> str:
        """Update an uptime check.

        Args:
            check_id: Uptime check UUID
            name: New name (optional)
            target: New target URL/IP (optional)
            check_type: New type: 'ping', 'http', 'https', 'tcp' (optional)
            enabled: Enable/disable (default true)
            regions: Comma-separated regions (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"enabled": enabled}
            if name: body["name"] = name
            if target: body["target"] = target
            if check_type: body["type"] = check_type
            if regions:
                body["regions"] = [r.strip() for r in regions.split(",") if r.strip()]
            data = await do_config.do_request("PUT", f"/uptime/checks/{check_id}", json_body=body)
            c = data.get("check", {})
            return json.dumps({"id": c.get("id"), "name": c.get("name"),
                "message": "Uptime check updated."}, indent=2)
        except Exception as e:
            return f"Error updating uptime check {check_id}: {str(e)}"

    @mcp.tool(name="digitalocean_delete_uptime_check", annotations={"title": "Delete Uptime Check", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_delete_uptime_check(check_id: str) -> str:
        """Delete an uptime check.

        Args:
            check_id: Uptime check UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            await do_config.do_request("DELETE", f"/uptime/checks/{check_id}")
            return json.dumps({"status": "success", "message": f"Uptime check {check_id} deleted."}, indent=2)
        except Exception as e:
            return f"Error deleting uptime check {check_id}: {str(e)}"

    @mcp.tool(name="digitalocean_list_uptime_check_alerts", annotations={"title": "List Uptime Alerts", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
    async def digitalocean_list_uptime_check_alerts(check_id: str) -> str:
        """List alert policies for an uptime check.

        Args:
            check_id: Uptime check UUID
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            data = await do_config.do_request("GET", f"/uptime/checks/{check_id}/alerts")
            alerts = [{"id": a.get("id", ""), "name": a.get("name", ""), "type": a.get("type", ""),
                "comparison": a.get("comparison", ""), "threshold": a.get("threshold"),
                "period": a.get("period", ""), "notifications": a.get("notifications", {})}
                for a in data.get("alerts", [])]
            return json.dumps({"alerts": alerts}, indent=2)
        except Exception as e:
            return f"Error listing uptime alerts: {str(e)}"

    @mcp.tool(name="digitalocean_create_uptime_check_alert", annotations={"title": "Create Uptime Alert", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
    async def digitalocean_create_uptime_check_alert(
        check_id: str, name: str, alert_type: str = "down",
        comparison: str = "greater_than", threshold: int = 1, period: str = "2m",
        emails: str = "", slack_webhooks: str = "",
    ) -> str:
        """Create an alert for an uptime check.

        Args:
            check_id: Uptime check UUID
            name: Alert name
            alert_type: Alert type: 'latency', 'down', 'down_global', 'ssl_expiry' (default 'down')
            comparison: Comparison: 'greater_than', 'less_than' (default 'greater_than')
            threshold: Threshold value (default 1)
            period: Check period: '2m', '3m', '5m', '10m', '15m', '30m' (default '2m')
            emails: Comma-separated email addresses (optional)
            slack_webhooks: Comma-separated Slack webhook URLs (optional)
        """
        if not do_config.is_configured:
            return "Error: DigitalOcean not configured. Set DIGITALOCEAN_TOKEN."
        try:
            body = {"name": name, "type": alert_type, "comparison": comparison,
                "threshold": threshold, "period": period}
            notifications = {}
            if emails:
                notifications["email"] = [e.strip() for e in emails.split(",") if e.strip()]
            if slack_webhooks:
                notifications["slack"] = [{"url": u.strip()} for u in slack_webhooks.split(",") if u.strip()]
            if notifications:
                body["notifications"] = notifications
            data = await do_config.do_request("POST", f"/uptime/checks/{check_id}/alerts", json_body=body)
            a = data.get("alert", {})
            return json.dumps({"id": a.get("id"), "name": a.get("name"),
                "message": "Uptime alert created."}, indent=2)
        except Exception as e:
            return f"Error creating uptime alert: {str(e)}"
