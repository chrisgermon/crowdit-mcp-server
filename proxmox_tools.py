"""
Proxmox VE Integration Tools for Crowd IT MCP Server

This module provides comprehensive Proxmox Virtual Environment management capabilities
using the Proxmox API via httpx.

Capabilities:
- Cluster status and resource overview
- Node management (status, services, syslog, network)
- VM (QEMU) management (list, start, stop, restart, status, config, clone, delete)
- Container (LXC) management (list, start, stop, restart, status, config, clone, delete)
- Storage management (list, content)
- Snapshot management (create, list, delete, rollback)
- Task management (list, status)
- Backup management (list, create)
- Pool and template management

Authentication: Uses API token (PVEAPIToken) or username/password.

Environment Variables:
    PROXMOX_HOST: Proxmox VE server hostname or IP (e.g., 192.168.1.100)
    PROXMOX_PORT: Proxmox VE API port (default: 8006)
    PROXMOX_TOKEN_ID: API token ID (e.g., user@pam!tokenname)
    PROXMOX_TOKEN_SECRET: API token secret (UUID)
    PROXMOX_VERIFY_SSL: Whether to verify SSL certificates (default: false)
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

class ProxmoxConfig:
    """Proxmox VE API configuration using API token authentication."""

    def __init__(self):
        self._host: Optional[str] = None
        self._port: Optional[str] = None
        self._token_id: Optional[str] = None
        self._token_secret: Optional[str] = None
        self._verify_ssl: Optional[bool] = None

    def _load_secret(self, secret_name: str, env_var: str, default: str = "") -> str:
        """Load a secret from Secret Manager or environment variable."""
        try:
            from app.core.config import get_secret_sync
            secret = get_secret_sync(secret_name)
            if secret:
                return secret
        except Exception:
            pass
        return os.getenv(env_var, default)

    @property
    def host(self) -> str:
        if self._host is None:
            self._host = self._load_secret("PROXMOX_HOST", "PROXMOX_HOST")
        return self._host

    @property
    def port(self) -> str:
        if self._port is None:
            self._port = self._load_secret("PROXMOX_PORT", "PROXMOX_PORT", "8006")
        return self._port

    @property
    def token_id(self) -> str:
        if self._token_id is None:
            self._token_id = self._load_secret("PROXMOX_TOKEN_ID", "PROXMOX_TOKEN_ID")
        return self._token_id

    @property
    def token_secret(self) -> str:
        if self._token_secret is None:
            self._token_secret = self._load_secret("PROXMOX_TOKEN_SECRET", "PROXMOX_TOKEN_SECRET")
        return self._token_secret

    @property
    def verify_ssl(self) -> bool:
        if self._verify_ssl is None:
            val = self._load_secret("PROXMOX_VERIFY_SSL", "PROXMOX_VERIFY_SSL", "false")
            self._verify_ssl = val.lower() in ("true", "1", "yes")
        return self._verify_ssl

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}/api2/json"

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.token_id and self.token_secret)

    @property
    def not_configured_error(self) -> str:
        return "Error: Proxmox VE not configured. Set PROXMOX_HOST, PROXMOX_TOKEN_ID, and PROXMOX_TOKEN_SECRET."

    async def do_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_body: dict = None,
        timeout: float = 30.0,
    ) -> Any:
        """Make a Proxmox API request with token authentication."""
        import httpx

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"PVEAPIToken={self.token_id}={self.token_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(verify=self.verify_ssl, timeout=timeout) as client:
            for attempt in range(3):
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_body,
                )

                if response.status_code == 429:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        raise Exception("Rate limited by Proxmox API.")

                if response.status_code >= 400:
                    try:
                        error_data = response.json()
                        errors = error_data.get("errors", {})
                        error_msg = json.dumps(errors) if errors else response.text
                        raise Exception(
                            f"Proxmox API error ({response.status_code}): {error_msg}"
                        )
                    except (json.JSONDecodeError, KeyError):
                        response.raise_for_status()

                if response.status_code == 204:
                    return {"status": "success"}

                data = response.json()
                # Proxmox wraps responses in {"data": ...}
                return data.get("data", data)


# =============================================================================
# Tool Registration
# =============================================================================

def register_proxmox_tools(mcp, config: ProxmoxConfig):
    """Register all Proxmox VE tools with the MCP server."""

    from pydantic import Field

    def _check_config() -> Optional[str]:
        if not config.is_configured:
            return config.not_configured_error
        return None

    # =========================================================================
    # Cluster & Node Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_cluster_status() -> str:
        """Get Proxmox cluster status including all nodes and their health."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", "/cluster/status")
            if not data:
                return "No cluster status data available."
            lines = []
            for item in data:
                item_type = item.get("type", "unknown")
                name = item.get("name", "unknown")
                if item_type == "cluster":
                    quorate = "Yes" if item.get("quorate") else "No"
                    lines.append(f"Cluster: {name} | Quorate: {quorate} | Nodes: {item.get('nodes', 'N/A')} | Version: {item.get('version', 'N/A')}")
                elif item_type == "node":
                    online = "Online" if item.get("online") else "Offline"
                    lines.append(f"  Node: {name} | Status: {online} | ID: {item.get('nodeid', 'N/A')} | IP: {item.get('ip', 'N/A')}")
            return "\n".join(lines) if lines else json.dumps(data, indent=2)
        except Exception as e:
            return f"Error getting cluster status: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_cluster_resources(
        resource_type: Optional[str] = Field(None, description="Filter by type: vm, storage, node, sdn, pool")
    ) -> str:
        """Get all cluster resources (VMs, containers, storage, nodes) with status and usage."""
        err = _check_config()
        if err:
            return err
        try:
            params = {}
            if resource_type:
                params["type"] = resource_type
            data = await config.do_request("GET", "/cluster/resources", params=params)
            if not data:
                return "No resources found."
            lines = []
            for r in data:
                rtype = r.get("type", "unknown")
                name = r.get("name", r.get("storage", "unknown"))
                status = r.get("status", "unknown")
                node = r.get("node", "")
                vmid = r.get("vmid", "")
                cpu = r.get("cpu", 0)
                maxcpu = r.get("maxcpu", 0)
                mem = r.get("mem", 0)
                maxmem = r.get("maxmem", 0)
                mem_gb = mem / (1024**3) if mem else 0
                maxmem_gb = maxmem / (1024**3) if maxmem else 0
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"

                if rtype in ("qemu", "lxc"):
                    lines.append(f"  [{rtype.upper()}] {name} (VMID: {vmid}) | Node: {node} | Status: {status} | CPU: {cpu_pct} | RAM: {mem_gb:.1f}/{maxmem_gb:.1f} GB")
                elif rtype == "node":
                    lines.append(f"  [NODE] {name} | Status: {status} | CPU: {cpu_pct} | RAM: {mem_gb:.1f}/{maxmem_gb:.1f} GB")
                elif rtype == "storage":
                    disk = r.get("disk", 0)
                    maxdisk = r.get("maxdisk", 0)
                    disk_gb = disk / (1024**3) if disk else 0
                    maxdisk_gb = maxdisk / (1024**3) if maxdisk else 0
                    lines.append(f"  [STORAGE] {name} | Node: {node} | Status: {status} | Used: {disk_gb:.1f}/{maxdisk_gb:.1f} GB")
                else:
                    lines.append(f"  [{rtype.upper()}] {name} | Node: {node} | Status: {status}")
            header = f"Found {len(data)} resources"
            if resource_type:
                header += f" (type: {resource_type})"
            return header + ":\n" + "\n".join(lines)
        except Exception as e:
            return f"Error getting cluster resources: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_nodes() -> str:
        """List all nodes in the Proxmox cluster with status, uptime, CPU, and memory usage."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", "/nodes")
            if not data:
                return "No nodes found."
            lines = [f"Found {len(data)} nodes:"]
            for node in sorted(data, key=lambda x: x.get("node", "")):
                name = node.get("node", "unknown")
                status = node.get("status", "unknown")
                cpu = node.get("cpu", 0)
                maxcpu = node.get("maxcpu", 0)
                mem = node.get("mem", 0)
                maxmem = node.get("maxmem", 0)
                uptime = node.get("uptime", 0)
                uptime_h = uptime / 3600 if uptime else 0
                mem_gb = mem / (1024**3) if mem else 0
                maxmem_gb = maxmem / (1024**3) if maxmem else 0
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"
                lines.append(f"  {name} | Status: {status} | CPU: {cpu_pct} ({maxcpu} cores) | RAM: {mem_gb:.1f}/{maxmem_gb:.1f} GB | Uptime: {uptime_h:.1f}h")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing nodes: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_node_status(
        node: str = Field(..., description="Node name (e.g., 'pve', 'node1')")
    ) -> str:
        """Get detailed status of a specific Proxmox node including CPU, memory, disk, and kernel info."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/status")
            if not data:
                return f"No status data for node '{node}'."
            cpu = data.get("cpu", 0)
            maxcpu = data.get("cpuinfo", {}).get("cpus", 0)
            model = data.get("cpuinfo", {}).get("model", "N/A")
            mem = data.get("memory", {})
            mem_used = mem.get("used", 0) / (1024**3)
            mem_total = mem.get("total", 0) / (1024**3)
            swap = data.get("swap", {})
            swap_used = swap.get("used", 0) / (1024**3)
            swap_total = swap.get("total", 0) / (1024**3)
            rootfs = data.get("rootfs", {})
            disk_used = rootfs.get("used", 0) / (1024**3)
            disk_total = rootfs.get("total", 0) / (1024**3)
            uptime = data.get("uptime", 0)
            uptime_d = uptime / 86400 if uptime else 0
            kernel = data.get("kversion", "N/A")
            pveversion = data.get("pveversion", "N/A")
            loadavg = data.get("loadavg", ["N/A", "N/A", "N/A"])

            return (
                f"Node: {node}\n"
                f"  PVE Version: {pveversion}\n"
                f"  Kernel: {kernel}\n"
                f"  CPU: {model} ({maxcpu} cores) | Usage: {cpu * 100:.1f}%\n"
                f"  Load Average: {', '.join(str(l) for l in loadavg)}\n"
                f"  Memory: {mem_used:.1f}/{mem_total:.1f} GB ({mem_used/mem_total*100:.0f}% used)\n"
                f"  Swap: {swap_used:.1f}/{swap_total:.1f} GB\n"
                f"  Root Disk: {disk_used:.1f}/{disk_total:.1f} GB ({disk_used/disk_total*100:.0f}% used)\n"
                f"  Uptime: {uptime_d:.1f} days"
            )
        except Exception as e:
            return f"Error getting node status: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_node_network(
        node: str = Field(..., description="Node name")
    ) -> str:
        """List network interfaces on a Proxmox node."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/network")
            if not data:
                return f"No network interfaces found on node '{node}'."
            lines = [f"Network interfaces on {node}:"]
            for iface in sorted(data, key=lambda x: x.get("iface", "")):
                name = iface.get("iface", "unknown")
                itype = iface.get("type", "unknown")
                cidr = iface.get("cidr", "N/A")
                address = iface.get("address", "N/A")
                active = "Active" if iface.get("active") else "Inactive"
                lines.append(f"  {name} | Type: {itype} | Address: {address} | CIDR: {cidr} | {active}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting network interfaces: {str(e)}"

    # =========================================================================
    # VM (QEMU) Management Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_vms(
        node: Optional[str] = Field(None, description="Node name. If not specified, lists VMs from all nodes.")
    ) -> str:
        """List all QEMU virtual machines with status, CPU, and memory usage."""
        err = _check_config()
        if err:
            return err
        try:
            if node:
                nodes = [node]
            else:
                node_data = await config.do_request("GET", "/nodes")
                nodes = [n["node"] for n in node_data if n.get("status") == "online"]

            all_vms = []
            for n in nodes:
                try:
                    vms = await config.do_request("GET", f"/nodes/{n}/qemu")
                    for vm in (vms or []):
                        vm["_node"] = n
                        all_vms.append(vm)
                except Exception:
                    pass

            if not all_vms:
                return "No VMs found."

            lines = [f"Found {len(all_vms)} VMs:"]
            for vm in sorted(all_vms, key=lambda x: x.get("vmid", 0)):
                vmid = vm.get("vmid", "?")
                name = vm.get("name", "unnamed")
                status = vm.get("status", "unknown")
                cpu = vm.get("cpu", 0)
                cpus = vm.get("cpus", 0)
                mem = vm.get("mem", 0) / (1024**3) if vm.get("mem") else 0
                maxmem = vm.get("maxmem", 0) / (1024**3) if vm.get("maxmem") else 0
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"
                lines.append(f"  {vmid}: {name} | Node: {vm['_node']} | Status: {status} | CPU: {cpu_pct} ({cpus} cores) | RAM: {mem:.1f}/{maxmem:.1f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing VMs: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_vm_status(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Get detailed status of a specific VM including CPU, memory, disk, and network usage."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/qemu/{vmid}/status/current")
            if not data:
                return f"No status data for VM {vmid}."
            name = data.get("name", "unnamed")
            status = data.get("status", "unknown")
            qmpstatus = data.get("qmpstatus", "unknown")
            cpu = data.get("cpu", 0)
            cpus = data.get("cpus", 0)
            mem = data.get("mem", 0) / (1024**3) if data.get("mem") else 0
            maxmem = data.get("maxmem", 0) / (1024**3) if data.get("maxmem") else 0
            disk = data.get("disk", 0) / (1024**3) if data.get("disk") else 0
            maxdisk = data.get("maxdisk", 0) / (1024**3) if data.get("maxdisk") else 0
            netin = data.get("netin", 0) / (1024**2) if data.get("netin") else 0
            netout = data.get("netout", 0) / (1024**2) if data.get("netout") else 0
            uptime = data.get("uptime", 0)
            uptime_h = uptime / 3600 if uptime else 0
            pid = data.get("pid", "N/A")

            return (
                f"VM {vmid}: {name}\n"
                f"  Status: {status} | QMP: {qmpstatus} | PID: {pid}\n"
                f"  CPU: {cpu * 100:.1f}% ({cpus} cores)\n"
                f"  Memory: {mem:.1f}/{maxmem:.1f} GB ({mem/maxmem*100:.0f}% used)\n"
                f"  Disk: {disk:.1f}/{maxdisk:.1f} GB\n"
                f"  Network In: {netin:.1f} MB | Out: {netout:.1f} MB\n"
                f"  Uptime: {uptime_h:.1f} hours"
            )
        except Exception as e:
            return f"Error getting VM status: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_vm_config(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Get the configuration of a specific VM (CPU, memory, disks, network, boot order, etc.)."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/qemu/{vmid}/config")
            if not data:
                return f"No config data for VM {vmid}."
            # Format key config items
            lines = [f"VM {vmid} Configuration:"]
            important_keys = ["name", "memory", "cores", "sockets", "cpu", "ostype",
                            "boot", "scsihw", "machine", "bios", "agent", "onboot",
                            "numa", "balloon", "hotplug", "tags"]
            for key in important_keys:
                if key in data:
                    lines.append(f"  {key}: {data[key]}")
            # Show disks
            for key in sorted(data.keys()):
                if any(key.startswith(p) for p in ("scsi", "virtio", "ide", "sata", "efidisk", "tpmstate")):
                    lines.append(f"  {key}: {data[key]}")
            # Show network
            for key in sorted(data.keys()):
                if key.startswith("net"):
                    lines.append(f"  {key}: {data[key]}")
            # Show other non-standard config
            shown = set(important_keys)
            for key in sorted(data.keys()):
                if key not in shown and not any(key.startswith(p) for p in ("scsi", "virtio", "ide", "sata", "efidisk", "tpmstate", "net", "unused", "digest")):
                    lines.append(f"  {key}: {data[key]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting VM config: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_start(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Start a stopped VM."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/start")
            return f"VM {vmid} start initiated. Task: {data}"
        except Exception as e:
            return f"Error starting VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_stop(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Force stop a VM (like pulling the power cord). Use shutdown for graceful stop."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/stop")
            return f"VM {vmid} stop initiated. Task: {data}"
        except Exception as e:
            return f"Error stopping VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_shutdown(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        force_stop_after: Optional[int] = Field(None, description="Seconds to wait before force stopping if graceful shutdown fails")
    ) -> str:
        """Gracefully shut down a VM via ACPI. Requires QEMU guest agent or ACPI support in the guest OS."""
        err = _check_config()
        if err:
            return err
        try:
            body = {}
            if force_stop_after is not None:
                body["forceStop"] = 1
                body["timeout"] = force_stop_after
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/shutdown", json_body=body if body else None)
            return f"VM {vmid} shutdown initiated. Task: {data}"
        except Exception as e:
            return f"Error shutting down VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_reboot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Reboot a VM via ACPI (graceful restart)."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/reboot")
            return f"VM {vmid} reboot initiated. Task: {data}"
        except Exception as e:
            return f"Error rebooting VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_reset(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Hard reset a VM (like pressing the reset button). Use reboot for graceful restart."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/reset")
            return f"VM {vmid} reset initiated. Task: {data}"
        except Exception as e:
            return f"Error resetting VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_suspend(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        to_disk: bool = Field(False, description="If true, suspend to disk (hibernate) instead of RAM")
    ) -> str:
        """Suspend a VM to RAM or disk (hibernate)."""
        err = _check_config()
        if err:
            return err
        try:
            body = {}
            if to_disk:
                body["todisk"] = 1
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/suspend", json_body=body if body else None)
            return f"VM {vmid} suspend initiated. Task: {data}"
        except Exception as e:
            return f"Error suspending VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_resume(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID")
    ) -> str:
        """Resume a suspended VM."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/status/resume")
            return f"VM {vmid} resume initiated. Task: {data}"
        except Exception as e:
            return f"Error resuming VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_clone(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Source VM ID"),
        newid: int = Field(..., description="New VM ID for the clone"),
        name: Optional[str] = Field(None, description="Name for the cloned VM"),
        full: bool = Field(True, description="Full clone (true) or linked clone (false)"),
        target_node: Optional[str] = Field(None, description="Target node for the clone (for cross-node cloning)"),
        target_storage: Optional[str] = Field(None, description="Target storage for the clone")
    ) -> str:
        """Clone a VM. Creates a full or linked clone with a new VMID."""
        err = _check_config()
        if err:
            return err
        try:
            body = {"newid": newid}
            if name:
                body["name"] = name
            if full:
                body["full"] = 1
            if target_node:
                body["target"] = target_node
            if target_storage:
                body["storage"] = target_storage
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/clone", json_body=body)
            return f"VM {vmid} clone to {newid} initiated. Task: {data}"
        except Exception as e:
            return f"Error cloning VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_delete(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID to delete"),
        purge: bool = Field(False, description="Remove VM from all related configurations (backup jobs, HA, replication, etc.)")
    ) -> str:
        """Delete a VM. The VM must be stopped first."""
        err = _check_config()
        if err:
            return err
        try:
            params = {}
            if purge:
                params["purge"] = 1
            data = await config.do_request("DELETE", f"/nodes/{node}/qemu/{vmid}", params=params)
            return f"VM {vmid} deletion initiated. Task: {data}"
        except Exception as e:
            return f"Error deleting VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_migrate(
        node: str = Field(..., description="Source node name"),
        vmid: int = Field(..., description="VM ID"),
        target: str = Field(..., description="Target node name"),
        online: bool = Field(True, description="Use online/live migration (true) or offline (false)")
    ) -> str:
        """Migrate a VM to another node. Supports live migration for running VMs."""
        err = _check_config()
        if err:
            return err
        try:
            body = {"target": target}
            if online:
                body["online"] = 1
            data = await config.do_request("POST", f"/nodes/{node}/qemu/{vmid}/migrate", json_body=body)
            return f"VM {vmid} migration to {target} initiated. Task: {data}"
        except Exception as e:
            return f"Error migrating VM {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_resize_disk(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        disk: str = Field(..., description="Disk name (e.g., 'scsi0', 'virtio0')"),
        size: str = Field(..., description="New size or size increment (e.g., '+10G', '50G')")
    ) -> str:
        """Resize a VM disk. Use '+' prefix to add to current size (e.g., '+10G')."""
        err = _check_config()
        if err:
            return err
        try:
            body = {"disk": disk, "size": size}
            await config.do_request("PUT", f"/nodes/{node}/qemu/{vmid}/resize", json_body=body)
            return f"VM {vmid} disk {disk} resized to {size}."
        except Exception as e:
            return f"Error resizing disk: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_vm_update_config(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM ID"),
        settings: str = Field(..., description="JSON object with settings to update. Common: {\"memory\": 4096, \"cores\": 2, \"onboot\": 1, \"description\": \"text\", \"tags\": \"tag1;tag2\"}")
    ) -> str:
        """Update VM configuration (memory, CPU, boot order, description, tags, etc.). Some changes require a reboot."""
        err = _check_config()
        if err:
            return err
        try:
            body = json.loads(settings)
            await config.do_request("PUT", f"/nodes/{node}/qemu/{vmid}/config", json_body=body)
            return f"VM {vmid} configuration updated: {list(body.keys())}"
        except json.JSONDecodeError:
            return "Error: 'settings' must be a valid JSON object."
        except Exception as e:
            return f"Error updating VM config: {str(e)}"

    # =========================================================================
    # Container (LXC) Management Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_containers(
        node: Optional[str] = Field(None, description="Node name. If not specified, lists containers from all nodes.")
    ) -> str:
        """List all LXC containers with status, CPU, and memory usage."""
        err = _check_config()
        if err:
            return err
        try:
            if node:
                nodes = [node]
            else:
                node_data = await config.do_request("GET", "/nodes")
                nodes = [n["node"] for n in node_data if n.get("status") == "online"]

            all_cts = []
            for n in nodes:
                try:
                    cts = await config.do_request("GET", f"/nodes/{n}/lxc")
                    for ct in (cts or []):
                        ct["_node"] = n
                        all_cts.append(ct)
                except Exception:
                    pass

            if not all_cts:
                return "No containers found."

            lines = [f"Found {len(all_cts)} containers:"]
            for ct in sorted(all_cts, key=lambda x: x.get("vmid", 0)):
                vmid = ct.get("vmid", "?")
                name = ct.get("name", "unnamed")
                status = ct.get("status", "unknown")
                cpu = ct.get("cpu", 0)
                cpus = ct.get("cpus", 0)
                mem = ct.get("mem", 0) / (1024**3) if ct.get("mem") else 0
                maxmem = ct.get("maxmem", 0) / (1024**3) if ct.get("maxmem") else 0
                cpu_pct = f"{cpu * 100:.1f}%" if cpu else "N/A"
                lines.append(f"  {vmid}: {name} | Node: {ct['_node']} | Status: {status} | CPU: {cpu_pct} ({cpus} cores) | RAM: {mem:.1f}/{maxmem:.1f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing containers: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_container_status(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Get detailed status of a specific LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/lxc/{vmid}/status/current")
            if not data:
                return f"No status data for container {vmid}."
            name = data.get("name", "unnamed")
            status = data.get("status", "unknown")
            cpu = data.get("cpu", 0)
            cpus = data.get("cpus", 0)
            mem = data.get("mem", 0) / (1024**3) if data.get("mem") else 0
            maxmem = data.get("maxmem", 0) / (1024**3) if data.get("maxmem") else 0
            disk = data.get("disk", 0) / (1024**3) if data.get("disk") else 0
            maxdisk = data.get("maxdisk", 0) / (1024**3) if data.get("maxdisk") else 0
            netin = data.get("netin", 0) / (1024**2) if data.get("netin") else 0
            netout = data.get("netout", 0) / (1024**2) if data.get("netout") else 0
            uptime = data.get("uptime", 0)
            uptime_h = uptime / 3600 if uptime else 0

            return (
                f"Container {vmid}: {name}\n"
                f"  Status: {status}\n"
                f"  CPU: {cpu * 100:.1f}% ({cpus} cores)\n"
                f"  Memory: {mem:.1f}/{maxmem:.1f} GB ({mem/maxmem*100:.0f}% used)\n"
                f"  Disk: {disk:.1f}/{maxdisk:.1f} GB\n"
                f"  Network In: {netin:.1f} MB | Out: {netout:.1f} MB\n"
                f"  Uptime: {uptime_h:.1f} hours"
            )
        except Exception as e:
            return f"Error getting container status: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_container_config(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Get the configuration of a specific LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/lxc/{vmid}/config")
            if not data:
                return f"No config data for container {vmid}."
            lines = [f"Container {vmid} Configuration:"]
            important_keys = ["hostname", "memory", "swap", "cores", "ostype",
                            "arch", "onboot", "unprivileged", "tags", "description"]
            for key in important_keys:
                if key in data:
                    lines.append(f"  {key}: {data[key]}")
            # Show rootfs and mount points
            for key in sorted(data.keys()):
                if key in ("rootfs",) or key.startswith("mp"):
                    lines.append(f"  {key}: {data[key]}")
            # Show network
            for key in sorted(data.keys()):
                if key.startswith("net"):
                    lines.append(f"  {key}: {data[key]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting container config: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_start(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Start a stopped LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/start")
            return f"Container {vmid} start initiated. Task: {data}"
        except Exception as e:
            return f"Error starting container {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_stop(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Force stop an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/stop")
            return f"Container {vmid} stop initiated. Task: {data}"
        except Exception as e:
            return f"Error stopping container {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_shutdown(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID"),
        force_stop_after: Optional[int] = Field(None, description="Seconds to wait before force stopping")
    ) -> str:
        """Gracefully shut down an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            body = {}
            if force_stop_after is not None:
                body["forceStop"] = 1
                body["timeout"] = force_stop_after
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/shutdown", json_body=body if body else None)
            return f"Container {vmid} shutdown initiated. Task: {data}"
        except Exception as e:
            return f"Error shutting down container {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_reboot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID")
    ) -> str:
        """Reboot an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/status/reboot")
            return f"Container {vmid} reboot initiated. Task: {data}"
        except Exception as e:
            return f"Error rebooting container {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_clone(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Source container ID"),
        newid: int = Field(..., description="New container ID for the clone"),
        hostname: Optional[str] = Field(None, description="Hostname for the cloned container"),
        full: bool = Field(True, description="Full clone (true) or linked clone (false)"),
        target_node: Optional[str] = Field(None, description="Target node for the clone"),
        target_storage: Optional[str] = Field(None, description="Target storage for the clone")
    ) -> str:
        """Clone an LXC container."""
        err = _check_config()
        if err:
            return err
        try:
            body = {"newid": newid}
            if hostname:
                body["hostname"] = hostname
            if full:
                body["full"] = 1
            if target_node:
                body["target"] = target_node
            if target_storage:
                body["storage"] = target_storage
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/clone", json_body=body)
            return f"Container {vmid} clone to {newid} initiated. Task: {data}"
        except Exception as e:
            return f"Error cloning container {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_delete(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="Container ID to delete"),
        purge: bool = Field(False, description="Remove from all related configurations")
    ) -> str:
        """Delete an LXC container. The container must be stopped first."""
        err = _check_config()
        if err:
            return err
        try:
            params = {}
            if purge:
                params["purge"] = 1
            data = await config.do_request("DELETE", f"/nodes/{node}/lxc/{vmid}", params=params)
            return f"Container {vmid} deletion initiated. Task: {data}"
        except Exception as e:
            return f"Error deleting container {vmid}: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_container_migrate(
        node: str = Field(..., description="Source node name"),
        vmid: int = Field(..., description="Container ID"),
        target: str = Field(..., description="Target node name"),
        restart: bool = Field(False, description="Restart the container after migration (for running containers)")
    ) -> str:
        """Migrate an LXC container to another node."""
        err = _check_config()
        if err:
            return err
        try:
            body = {"target": target}
            if restart:
                body["restart"] = 1
            data = await config.do_request("POST", f"/nodes/{node}/lxc/{vmid}/migrate", json_body=body)
            return f"Container {vmid} migration to {target} initiated. Task: {data}"
        except Exception as e:
            return f"Error migrating container {vmid}: {str(e)}"

    # =========================================================================
    # Storage Management Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_storage(
        node: Optional[str] = Field(None, description="Node name. If not specified, lists storage from all nodes.")
    ) -> str:
        """List all storage pools with type, usage, and status."""
        err = _check_config()
        if err:
            return err
        try:
            if node:
                data = await config.do_request("GET", f"/nodes/{node}/storage")
            else:
                data = await config.do_request("GET", "/storage")
            if not data:
                return "No storage found."
            lines = [f"Found {len(data)} storage pools:"]
            for s in sorted(data, key=lambda x: x.get("storage", "")):
                name = s.get("storage", "unknown")
                stype = s.get("type", "unknown")
                content = s.get("content", "N/A")
                active = "Active" if s.get("active", s.get("enabled")) else "Inactive"
                total = s.get("total", 0) / (1024**3) if s.get("total") else 0
                used = s.get("used", 0) / (1024**3) if s.get("used") else 0
                avail = s.get("avail", 0) / (1024**3) if s.get("avail") else 0
                if total > 0:
                    pct = f"{used/total*100:.0f}%"
                    lines.append(f"  {name} | Type: {stype} | Content: {content} | {active} | Used: {used:.1f}/{total:.1f} GB ({pct})")
                else:
                    lines.append(f"  {name} | Type: {stype} | Content: {content} | {active}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing storage: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_storage_content(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name"),
        content_type: Optional[str] = Field(None, description="Filter by content type: images, rootdir, vztmpl, backup, iso, snippets")
    ) -> str:
        """List contents of a storage pool (ISOs, backups, disk images, templates)."""
        err = _check_config()
        if err:
            return err
        try:
            params = {}
            if content_type:
                params["content"] = content_type
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params=params)
            if not data:
                return f"No content found in storage '{storage}'."
            lines = [f"Found {len(data)} items in '{storage}':"]
            for item in sorted(data, key=lambda x: x.get("volid", "")):
                volid = item.get("volid", "unknown")
                fmt = item.get("format", "unknown")
                size = item.get("size", 0) / (1024**3) if item.get("size") else 0
                ctype = item.get("content", "unknown")
                lines.append(f"  {volid} | Type: {ctype} | Format: {fmt} | Size: {size:.2f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing storage content: {str(e)}"

    # =========================================================================
    # Snapshot Management Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_snapshots(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        vm_type: str = Field("qemu", description="Type: 'qemu' for VMs, 'lxc' for containers")
    ) -> str:
        """List all snapshots for a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/{vm_type}/{vmid}/snapshot")
            if not data:
                return f"No snapshots found for {vm_type}/{vmid}."
            lines = [f"Snapshots for {vm_type}/{vmid}:"]
            for snap in data:
                name = snap.get("name", "unknown")
                desc = snap.get("description", "")
                snaptime = snap.get("snaptime", "")
                parent = snap.get("parent", "")
                if name == "current":
                    lines.append(f"  [current] You are here (parent: {parent})")
                else:
                    from datetime import datetime
                    time_str = datetime.fromtimestamp(snaptime).strftime("%Y-%m-%d %H:%M:%S") if snaptime else "N/A"
                    lines.append(f"  {name} | Created: {time_str} | Description: {desc or 'N/A'}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing snapshots: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_create_snapshot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        snapname: str = Field(..., description="Snapshot name (alphanumeric, no spaces)"),
        description: Optional[str] = Field(None, description="Snapshot description"),
        vm_type: str = Field("qemu", description="Type: 'qemu' for VMs, 'lxc' for containers"),
        vmstate: bool = Field(False, description="Include VM RAM state in snapshot (QEMU only, VM must be running)")
    ) -> str:
        """Create a snapshot of a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            body = {"snapname": snapname}
            if description:
                body["description"] = description
            if vmstate and vm_type == "qemu":
                body["vmstate"] = 1
            data = await config.do_request("POST", f"/nodes/{node}/{vm_type}/{vmid}/snapshot", json_body=body)
            return f"Snapshot '{snapname}' creation initiated for {vm_type}/{vmid}. Task: {data}"
        except Exception as e:
            return f"Error creating snapshot: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_delete_snapshot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        snapname: str = Field(..., description="Snapshot name to delete"),
        vm_type: str = Field("qemu", description="Type: 'qemu' for VMs, 'lxc' for containers")
    ) -> str:
        """Delete a snapshot from a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("DELETE", f"/nodes/{node}/{vm_type}/{vmid}/snapshot/{snapname}")
            return f"Snapshot '{snapname}' deletion initiated. Task: {data}"
        except Exception as e:
            return f"Error deleting snapshot: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_rollback_snapshot(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID"),
        snapname: str = Field(..., description="Snapshot name to rollback to"),
        vm_type: str = Field("qemu", description="Type: 'qemu' for VMs, 'lxc' for containers")
    ) -> str:
        """Rollback a VM or container to a previous snapshot. WARNING: This will overwrite the current state."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("POST", f"/nodes/{node}/{vm_type}/{vmid}/snapshot/{snapname}/rollback")
            return f"Rollback to snapshot '{snapname}' initiated. Task: {data}"
        except Exception as e:
            return f"Error rolling back snapshot: {str(e)}"

    # =========================================================================
    # Task Management Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_tasks(
        node: Optional[str] = Field(None, description="Node name. If not specified, lists cluster-wide tasks."),
        limit: int = Field(20, description="Maximum number of tasks to return (1-100)"),
        vmid: Optional[int] = Field(None, description="Filter by VM/container ID"),
        status_filter: Optional[str] = Field(None, description="Filter by status: 'running', 'ok', 'error'")
    ) -> str:
        """List recent tasks (backups, migrations, clones, etc.) with their status."""
        err = _check_config()
        if err:
            return err
        try:
            if node:
                endpoint = f"/nodes/{node}/tasks"
            else:
                endpoint = "/cluster/tasks"
            params = {"limit": min(limit, 100)}
            if vmid is not None:
                params["vmid"] = vmid
            data = await config.do_request("GET", endpoint, params=params)
            if not data:
                return "No tasks found."
            lines = [f"Found {len(data)} tasks:"]
            for task in data:
                upid = task.get("upid", "")
                task_type = task.get("type", "unknown")
                task_status = task.get("status", "running")
                task_node = task.get("node", "")
                starttime = task.get("starttime", 0)
                endtime = task.get("endtime", 0)
                user = task.get("user", "")
                task_vmid = task.get("id", "")
                from datetime import datetime
                start_str = datetime.fromtimestamp(starttime).strftime("%Y-%m-%d %H:%M:%S") if starttime else "N/A"
                if status_filter and task_status != status_filter:
                    continue
                duration = ""
                if endtime and starttime:
                    dur = endtime - starttime
                    duration = f" | Duration: {dur}s"
                lines.append(f"  [{task_status}] {task_type} | Node: {task_node} | VMID: {task_vmid} | User: {user} | Started: {start_str}{duration}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing tasks: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_task_status(
        node: str = Field(..., description="Node name"),
        upid: str = Field(..., description="Task UPID (unique process ID)")
    ) -> str:
        """Get the status and log output of a specific task."""
        err = _check_config()
        if err:
            return err
        try:
            status = await config.do_request("GET", f"/nodes/{node}/tasks/{upid}/status")
            log_data = await config.do_request("GET", f"/nodes/{node}/tasks/{upid}/log", params={"limit": 50})
            lines = [f"Task Status:"]
            lines.append(f"  Type: {status.get('type', 'N/A')}")
            lines.append(f"  Status: {status.get('status', 'N/A')}")
            lines.append(f"  Exit Status: {status.get('exitstatus', 'N/A')}")
            lines.append(f"  Node: {status.get('node', 'N/A')}")
            lines.append(f"  User: {status.get('user', 'N/A')}")
            if log_data:
                lines.append(f"\nTask Log (last {len(log_data)} lines):")
                for entry in log_data:
                    lines.append(f"  {entry.get('t', '')}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting task status: {str(e)}"

    # =========================================================================
    # Backup Management Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_backups(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name (must be a backup-enabled storage)"),
        vmid: Optional[int] = Field(None, description="Filter backups by VM/container ID")
    ) -> str:
        """List available backups on a storage pool."""
        err = _check_config()
        if err:
            return err
        try:
            params = {"content": "backup"}
            if vmid is not None:
                params["vmid"] = vmid
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params=params)
            if not data:
                return f"No backups found on storage '{storage}'."
            lines = [f"Found {len(data)} backups on '{storage}':"]
            for item in sorted(data, key=lambda x: x.get("ctime", 0), reverse=True):
                volid = item.get("volid", "unknown")
                size = item.get("size", 0) / (1024**3) if item.get("size") else 0
                fmt = item.get("format", "unknown")
                ctime = item.get("ctime", 0)
                from datetime import datetime
                time_str = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S") if ctime else "N/A"
                notes = item.get("notes", "")
                lines.append(f"  {volid} | Size: {size:.2f} GB | Format: {fmt} | Created: {time_str}" + (f" | Notes: {notes}" if notes else ""))
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing backups: {str(e)}"

    @mcp.tool(annotations={"destructiveHint": True})
    async def proxmox_create_backup(
        node: str = Field(..., description="Node name"),
        vmid: int = Field(..., description="VM or container ID to backup"),
        storage: str = Field(..., description="Target storage pool for the backup"),
        mode: str = Field("snapshot", description="Backup mode: 'snapshot' (default, no downtime), 'suspend' (brief pause), 'stop' (VM stopped during backup)"),
        compress: str = Field("zstd", description="Compression: 'zstd' (recommended), 'lzo', 'gzip', 'none'"),
        notes: Optional[str] = Field(None, description="Notes/description for the backup")
    ) -> str:
        """Create a backup of a VM or container."""
        err = _check_config()
        if err:
            return err
        try:
            body = {
                "vmid": str(vmid),
                "storage": storage,
                "mode": mode,
                "compress": compress,
            }
            if notes:
                body["notes-template"] = notes
            data = await config.do_request("POST", f"/nodes/{node}/vzdump", json_body=body)
            return f"Backup of {vmid} initiated on storage '{storage}'. Task: {data}"
        except Exception as e:
            return f"Error creating backup: {str(e)}"

    # =========================================================================
    # Template & Pool Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_pools() -> str:
        """List all resource pools."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", "/pools")
            if not data:
                return "No pools found."
            lines = [f"Found {len(data)} pools:"]
            for pool in sorted(data, key=lambda x: x.get("poolid", "")):
                poolid = pool.get("poolid", "unknown")
                comment = pool.get("comment", "")
                lines.append(f"  {poolid}" + (f" - {comment}" if comment else ""))
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing pools: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_pool_members(
        poolid: str = Field(..., description="Pool ID to get members of")
    ) -> str:
        """Get members (VMs, containers, storage) of a resource pool."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/pools/{poolid}")
            if not data:
                return f"No data for pool '{poolid}'."
            members = data.get("members", [])
            comment = data.get("comment", "")
            lines = [f"Pool: {poolid}" + (f" ({comment})" if comment else "")]
            lines.append(f"Members ({len(members)}):")
            for m in members:
                mtype = m.get("type", "unknown")
                name = m.get("name", m.get("storage", "unknown"))
                vmid = m.get("vmid", "")
                node = m.get("node", "")
                status = m.get("status", "")
                if vmid:
                    lines.append(f"  [{mtype}] {name} (VMID: {vmid}) | Node: {node} | Status: {status}")
                else:
                    lines.append(f"  [{mtype}] {name} | Node: {node}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting pool members: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_templates(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name")
    ) -> str:
        """List available container templates (for creating new LXC containers)."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params={"content": "vztmpl"})
            if not data:
                return f"No templates found on storage '{storage}'."
            lines = [f"Found {len(data)} templates on '{storage}':"]
            for t in sorted(data, key=lambda x: x.get("volid", "")):
                volid = t.get("volid", "unknown")
                size = t.get("size", 0) / (1024**2) if t.get("size") else 0
                lines.append(f"  {volid} | Size: {size:.1f} MB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing templates: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_list_isos(
        node: str = Field(..., description="Node name"),
        storage: str = Field(..., description="Storage pool name")
    ) -> str:
        """List available ISO images for VM installation."""
        err = _check_config()
        if err:
            return err
        try:
            data = await config.do_request("GET", f"/nodes/{node}/storage/{storage}/content", params={"content": "iso"})
            if not data:
                return f"No ISOs found on storage '{storage}'."
            lines = [f"Found {len(data)} ISOs on '{storage}':"]
            for iso in sorted(data, key=lambda x: x.get("volid", "")):
                volid = iso.get("volid", "unknown")
                size = iso.get("size", 0) / (1024**3) if iso.get("size") else 0
                lines.append(f"  {volid} | Size: {size:.2f} GB")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing ISOs: {str(e)}"

    # =========================================================================
    # Firewall Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_firewall_rules(
        node: Optional[str] = Field(None, description="Node name. Omit for cluster-level firewall rules."),
        vmid: Optional[int] = Field(None, description="VM/container ID for VM-level firewall rules"),
        vm_type: str = Field("qemu", description="Type: 'qemu' for VMs, 'lxc' for containers (only used with vmid)")
    ) -> str:
        """List firewall rules at cluster, node, or VM/container level."""
        err = _check_config()
        if err:
            return err
        try:
            if vmid and node:
                endpoint = f"/nodes/{node}/{vm_type}/{vmid}/firewall/rules"
            elif node:
                endpoint = f"/nodes/{node}/firewall/rules"
            else:
                endpoint = "/cluster/firewall/rules"
            data = await config.do_request("GET", endpoint)
            if not data:
                return "No firewall rules found."
            lines = [f"Found {len(data)} firewall rules:"]
            for rule in data:
                pos = rule.get("pos", "?")
                action = rule.get("action", "?")
                rtype = rule.get("type", "?")
                enabled = "Enabled" if rule.get("enable") else "Disabled"
                source = rule.get("source", "any")
                dest = rule.get("dest", "any")
                proto = rule.get("proto", "any")
                dport = rule.get("dport", "any")
                comment = rule.get("comment", "")
                lines.append(f"  #{pos} [{enabled}] {action} {rtype} | Proto: {proto} | Src: {source} | Dst: {dest} | Port: {dport}" + (f" | {comment}" if comment else ""))
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing firewall rules: {str(e)}"

    # =========================================================================
    # HA (High Availability) Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def proxmox_ha_status() -> str:
        """Get High Availability (HA) status and managed resources."""
        err = _check_config()
        if err:
            return err
        try:
            status = await config.do_request("GET", "/cluster/ha/status/current")
            resources = await config.do_request("GET", "/cluster/ha/resources")
            lines = ["HA Manager Status:"]
            if isinstance(status, list):
                for item in status:
                    lines.append(f"  {item.get('id', 'N/A')}: {item.get('status', 'N/A')} (type: {item.get('type', 'N/A')})")
            lines.append(f"\nHA Resources ({len(resources) if resources else 0}):")
            if resources:
                for r in resources:
                    sid = r.get("sid", "unknown")
                    state = r.get("state", "unknown")
                    group = r.get("group", "none")
                    max_restart = r.get("max_restart", 1)
                    max_relocate = r.get("max_relocate", 1)
                    lines.append(f"  {sid} | State: {state} | Group: {group} | Max Restart: {max_restart} | Max Relocate: {max_relocate}")
            else:
                lines.append("  No HA resources configured.")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting HA status: {str(e)}"

    logger.info("Proxmox VE tools registered successfully")
