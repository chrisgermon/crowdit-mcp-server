"""
Azure Integration Tools for Crowd IT MCP Server

This module provides comprehensive Azure management capabilities including:
- Resource Management (resource groups, resources, tags)
- Networking (VNets, subnets, NSGs, peerings, VPN gateways)
- Compute (VMs, availability sets, disks)
- Storage (storage accounts, containers)
- Cost Management (usage, budgets, forecasts)
- Azure AD (basic directory operations)

Requirements:
    pip install azure-identity azure-mgmt-resource azure-mgmt-network
    pip install azure-mgmt-compute azure-mgmt-storage azure-mgmt-costmanagement
    pip install azure-mgmt-subscription

Environment Variables:
    AZURE_TENANT_ID: Azure AD tenant ID
    AZURE_CLIENT_ID: Service Principal client ID
    AZURE_CLIENT_SECRET: Service Principal client secret
    AZURE_SUBSCRIPTION_ID: Default subscription ID (optional)
"""

import os
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field, ConfigDict

# Azure SDK imports
from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.resource.subscriptions import SubscriptionClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.storage import StorageManagementClient
from azure.mgmt.costmanagement import CostManagementClient
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError


# =============================================================================
# Configuration and Authentication
# =============================================================================

def get_azure_credentials():
    """Get Azure credentials from environment variables."""
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    client_secret = os.environ.get("AZURE_CLIENT_SECRET")

    if not all([tenant_id, client_id, client_secret]):
        missing = []
        if not tenant_id:
            missing.append("AZURE_TENANT_ID")
        if not client_id:
            missing.append("AZURE_CLIENT_ID")
        if not client_secret:
            missing.append("AZURE_CLIENT_SECRET")
        raise ValueError(f"Missing Azure credentials: {', '.join(missing)}")

    return ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )

def get_default_subscription():
    """Get default subscription from environment."""
    return os.environ.get("AZURE_SUBSCRIPTION_ID")


# =============================================================================
# Response Formatting Helpers
# =============================================================================

def format_resource(resource: Any) -> Dict[str, Any]:
    """Format an Azure resource for display."""
    return {
        "id": resource.id,
        "name": resource.name,
        "type": resource.type,
        "location": getattr(resource, "location", None),
        "tags": getattr(resource, "tags", {}),
        "provisioning_state": getattr(resource, "provisioning_state", None),
    }

def format_vm(vm: Any) -> Dict[str, Any]:
    """Format a VM for display."""
    return {
        "id": vm.id,
        "name": vm.name,
        "location": vm.location,
        "vm_size": vm.hardware_profile.vm_size if vm.hardware_profile else None,
        "os_type": vm.storage_profile.os_disk.os_type if vm.storage_profile and vm.storage_profile.os_disk else None,
        "provisioning_state": vm.provisioning_state,
        "tags": vm.tags or {},
    }

def format_vnet(vnet: Any) -> Dict[str, Any]:
    """Format a VNet for display."""
    return {
        "id": vnet.id,
        "name": vnet.name,
        "location": vnet.location,
        "address_space": vnet.address_space.address_prefixes if vnet.address_space else [],
        "subnets": [{"name": s.name, "address_prefix": s.address_prefix} for s in (vnet.subnets or [])],
        "provisioning_state": vnet.provisioning_state,
        "tags": vnet.tags or {},
    }

def format_nsg(nsg: Any) -> Dict[str, Any]:
    """Format an NSG for display."""
    rules = []
    for rule in (nsg.security_rules or []):
        rules.append({
            "name": rule.name,
            "priority": rule.priority,
            "direction": rule.direction,
            "access": rule.access,
            "protocol": rule.protocol,
            "source": rule.source_address_prefix,
            "destination": rule.destination_address_prefix,
            "destination_port": rule.destination_port_range,
        })
    return {
        "id": nsg.id,
        "name": nsg.name,
        "location": nsg.location,
        "rules": rules,
        "tags": nsg.tags or {},
    }

def handle_azure_error(e: Exception) -> str:
    """Handle Azure API errors consistently."""
    if isinstance(e, ResourceNotFoundError):
        return f"Error: Resource not found. Please verify the resource exists and you have access."
    elif isinstance(e, HttpResponseError):
        return f"Error: Azure API error ({e.status_code}): {e.message}"
    elif isinstance(e, ValueError):
        return f"Error: {str(e)}"
    return f"Error: {type(e).__name__}: {str(e)}"


# =============================================================================
# Tool Registration Function (to be called from main server.py)
# =============================================================================

def register_azure_tools(mcp):
    """Register all Azure tools with the MCP server."""

    # =========================================================================
    # Subscription & Resource Group Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_subscriptions",
        annotations={
            "title": "List Azure Subscriptions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_subscriptions() -> str:
        """List all Azure subscriptions accessible to the service principal.

        Returns subscription IDs, names, and states for all subscriptions
        the authenticated service principal can access.
        """
        try:
            credential = get_azure_credentials()
            client = SubscriptionClient(credential)

            subscriptions = []
            for sub in client.subscriptions.list():
                subscriptions.append({
                    "id": sub.subscription_id,
                    "name": sub.display_name,
                    "state": sub.state,
                    "tenant_id": sub.tenant_id,
                })

            if not subscriptions:
                return "No subscriptions found. Verify the service principal has appropriate access."

            result = "# Azure Subscriptions\n\n"
            for sub in subscriptions:
                result += f"**{sub['name']}**\n"
                result += f"- ID: `{sub['id']}`\n"
                result += f"- State: {sub['state']}\n"
                result += f"- Tenant: {sub['tenant_id']}\n\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_list_resource_groups",
        annotations={
            "title": "List Resource Groups",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_resource_groups(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID (uses default if not provided)"),
        location_filter: Optional[str] = Field(default=None, description="Filter by location (e.g., 'australiaeast')")
    ) -> str:
        """List all resource groups in a subscription."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = ResourceManagementClient(credential, sub_id)

            groups = []
            for rg in client.resource_groups.list():
                if location_filter and rg.location != location_filter:
                    continue
                groups.append({
                    "name": rg.name,
                    "location": rg.location,
                    "provisioning_state": rg.properties.provisioning_state if rg.properties else None,
                    "tags": rg.tags or {},
                })

            if not groups:
                return f"No resource groups found in subscription {sub_id}"

            result = f"# Resource Groups ({sub_id})\n\n"
            result += f"| Name | Location | State | Tags |\n"
            result += f"|------|----------|-------|------|\n"
            for rg in groups:
                tags = ", ".join(f"{k}={v}" for k, v in (rg['tags'] or {}).items())[:50]
                result += f"| {rg['name']} | {rg['location']} | {rg['provisioning_state']} | {tags or '-'} |\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_create_resource_group",
        annotations={
            "title": "Create Resource Group",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_create_resource_group(
        name: str = Field(..., description="Resource group name", min_length=1, max_length=90),
        location: str = Field(..., description="Azure region (e.g., 'australiaeast', 'australiasoutheast')"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID (uses default if not provided)"),
        tags: Optional[str] = Field(default=None, description="Tags as JSON object, e.g., '{\"environment\": \"prod\", \"client\": \"vision\"}'")
    ) -> str:
        """Create a new resource group."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = ResourceManagementClient(credential, sub_id)

            rg_params = {"location": location}
            if tags:
                rg_params["tags"] = json.loads(tags)

            result = client.resource_groups.create_or_update(name, rg_params)

            return f"✅ Resource group created successfully\n\n" \
                   f"**Name:** {result.name}\n" \
                   f"**Location:** {result.location}\n" \
                   f"**ID:** `{result.id}`\n" \
                   f"**Tags:** {result.tags or 'None'}"

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_delete_resource_group",
        annotations={
            "title": "Delete Resource Group",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def azure_delete_resource_group(
        name: str = Field(..., description="Resource group name to delete"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        force: bool = Field(default=False, description="Skip confirmation (DANGEROUS)")
    ) -> str:
        """Delete a resource group and ALL resources within it.

        ⚠️ WARNING: This is a destructive operation that cannot be undone.
        """
        try:
            if not force:
                return f"⚠️ WARNING: This will delete resource group '{name}' and ALL resources within it.\n\n" \
                       f"To proceed, call this function again with force=True"

            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = ResourceManagementClient(credential, sub_id)
            poller = client.resource_groups.begin_delete(name)

            return f"🗑️ Resource group deletion initiated\n\n" \
                   f"**Resource Group:** {name}\n" \
                   f"**Status:** Deletion in progress\n\n" \
                   f"Note: Deletion may take several minutes."

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_list_resources",
        annotations={
            "title": "List Resources in Resource Group",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_resources(
        resource_group: str = Field(..., description="Resource group name"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_type: Optional[str] = Field(default=None, description="Filter by type (e.g., 'Microsoft.Compute/virtualMachines')")
    ) -> str:
        """List all resources in a resource group."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = ResourceManagementClient(credential, sub_id)
            filter_str = f"resourceType eq '{resource_type}'" if resource_type else None

            resources = []
            for res in client.resources.list_by_resource_group(resource_group, filter=filter_str):
                resources.append(format_resource(res))

            if not resources:
                return f"No resources found in resource group '{resource_group}'"

            result = f"# Resources in {resource_group}\n\n"
            result += f"| Type | Name | Location |\n"
            result += f"|------|------|----------|\n"
            for res in resources:
                short_type = res['type'].split('/')[-1] if res['type'] else 'Unknown'
                result += f"| {short_type} | {res['name']} | {res['location'] or '-'} |\n"

            result += f"\n**Total:** {len(resources)} resources"
            return result

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # Virtual Network Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_vnets",
        annotations={
            "title": "List Virtual Networks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_vnets(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_group: Optional[str] = Field(default=None, description="Filter by resource group")
    ) -> str:
        """List all virtual networks in a subscription or resource group."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            vnets = []
            if resource_group:
                for vnet in client.virtual_networks.list(resource_group):
                    vnets.append(format_vnet(vnet))
            else:
                for vnet in client.virtual_networks.list_all():
                    vnets.append(format_vnet(vnet))

            if not vnets:
                return "No virtual networks found"

            result = "# Virtual Networks\n\n"
            for vnet in vnets:
                result += f"## {vnet['name']}\n"
                result += f"- **Location:** {vnet['location']}\n"
                result += f"- **Address Space:** {', '.join(vnet['address_space'])}\n"
                result += f"- **Subnets:**\n"
                for subnet in vnet['subnets']:
                    result += f"  - {subnet['name']}: {subnet['address_prefix']}\n"
                result += "\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_create_vnet",
        annotations={
            "title": "Create Virtual Network",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_create_vnet(
        name: str = Field(..., description="VNet name"),
        resource_group: str = Field(..., description="Resource group name"),
        location: str = Field(..., description="Azure region"),
        address_space: str = Field(..., description="Address space CIDR (e.g., '10.0.0.0/16')"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        dns_servers: Optional[str] = Field(default=None, description="Comma-separated DNS servers"),
        tags: Optional[str] = Field(default=None, description="Tags as JSON")
    ) -> str:
        """Create a new virtual network."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            vnet_params = {
                "location": location,
                "address_space": {"address_prefixes": [address_space]}
            }

            if dns_servers:
                vnet_params["dhcp_options"] = {"dns_servers": [s.strip() for s in dns_servers.split(",")]}
            if tags:
                vnet_params["tags"] = json.loads(tags)

            poller = client.virtual_networks.begin_create_or_update(resource_group, name, vnet_params)
            result = poller.result()

            return f"✅ Virtual network created successfully\n\n" \
                   f"**Name:** {result.name}\n" \
                   f"**Location:** {result.location}\n" \
                   f"**Address Space:** {', '.join(result.address_space.address_prefixes)}\n" \
                   f"**ID:** `{result.id}`"

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_create_subnet",
        annotations={
            "title": "Create Subnet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_create_subnet(
        name: str = Field(..., description="Subnet name"),
        vnet_name: str = Field(..., description="Virtual network name"),
        resource_group: str = Field(..., description="Resource group name"),
        address_prefix: str = Field(..., description="Subnet CIDR (e.g., '10.0.1.0/24')"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        nsg_id: Optional[str] = Field(default=None, description="Network Security Group resource ID to associate")
    ) -> str:
        """Create a subnet within a virtual network."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            subnet_params = {"address_prefix": address_prefix}
            if nsg_id:
                subnet_params["network_security_group"] = {"id": nsg_id}

            poller = client.subnets.begin_create_or_update(resource_group, vnet_name, name, subnet_params)
            result = poller.result()

            return f"✅ Subnet created successfully\n\n" \
                   f"**Name:** {result.name}\n" \
                   f"**Address Prefix:** {result.address_prefix}\n" \
                   f"**ID:** `{result.id}`"

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # Network Security Group Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_nsgs",
        annotations={
            "title": "List Network Security Groups",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_nsgs(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_group: Optional[str] = Field(default=None, description="Filter by resource group")
    ) -> str:
        """List all Network Security Groups."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            nsgs = []
            if resource_group:
                for nsg in client.network_security_groups.list(resource_group):
                    nsgs.append(format_nsg(nsg))
            else:
                for nsg in client.network_security_groups.list_all():
                    nsgs.append(format_nsg(nsg))

            if not nsgs:
                return "No Network Security Groups found"

            result = "# Network Security Groups\n\n"
            for nsg in nsgs:
                result += f"## {nsg['name']} ({nsg['location']})\n\n"
                if nsg['rules']:
                    result += "| Priority | Name | Direction | Access | Protocol | Source | Dest Port |\n"
                    result += "|----------|------|-----------|--------|----------|--------|----------|\n"
                    for rule in sorted(nsg['rules'], key=lambda x: x['priority']):
                        result += f"| {rule['priority']} | {rule['name']} | {rule['direction']} | {rule['access']} | {rule['protocol']} | {rule['source']} | {rule['destination_port']} |\n"
                else:
                    result += "*No custom rules*\n"
                result += "\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_create_nsg",
        annotations={
            "title": "Create Network Security Group",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_create_nsg(
        name: str = Field(..., description="NSG name"),
        resource_group: str = Field(..., description="Resource group name"),
        location: str = Field(..., description="Azure region"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        tags: Optional[str] = Field(default=None, description="Tags as JSON")
    ) -> str:
        """Create a new Network Security Group."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            nsg_params = {"location": location}
            if tags:
                nsg_params["tags"] = json.loads(tags)

            poller = client.network_security_groups.begin_create_or_update(resource_group, name, nsg_params)
            result = poller.result()

            return f"✅ Network Security Group created\n\n" \
                   f"**Name:** {result.name}\n" \
                   f"**Location:** {result.location}\n" \
                   f"**ID:** `{result.id}`"

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_add_nsg_rule",
        annotations={
            "title": "Add NSG Security Rule",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_add_nsg_rule(
        nsg_name: str = Field(..., description="NSG name"),
        resource_group: str = Field(..., description="Resource group name"),
        rule_name: str = Field(..., description="Security rule name"),
        priority: int = Field(..., description="Rule priority (100-4096)", ge=100, le=4096),
        direction: str = Field(..., description="Direction: 'Inbound' or 'Outbound'"),
        access: str = Field(..., description="Access: 'Allow' or 'Deny'"),
        protocol: str = Field(default="*", description="Protocol: 'Tcp', 'Udp', 'Icmp', '*'"),
        source_address: str = Field(default="*", description="Source address prefix or '*'"),
        source_port: str = Field(default="*", description="Source port range or '*'"),
        destination_address: str = Field(default="*", description="Destination address prefix or '*'"),
        destination_port: str = Field(..., description="Destination port range (e.g., '443', '80-443', '*')"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        description: Optional[str] = Field(default=None, description="Rule description")
    ) -> str:
        """Add a security rule to a Network Security Group."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            rule_params = {
                "priority": priority,
                "direction": direction,
                "access": access,
                "protocol": protocol,
                "source_address_prefix": source_address,
                "source_port_range": source_port,
                "destination_address_prefix": destination_address,
                "destination_port_range": destination_port,
            }
            if description:
                rule_params["description"] = description

            poller = client.security_rules.begin_create_or_update(resource_group, nsg_name, rule_name, rule_params)
            result = poller.result()

            return f"✅ Security rule added\n\n" \
                   f"**Rule:** {result.name}\n" \
                   f"**Priority:** {result.priority}\n" \
                   f"**Direction:** {result.direction}\n" \
                   f"**Access:** {result.access}"

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # Virtual Machine Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_vms",
        annotations={
            "title": "List Virtual Machines",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_vms(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_group: Optional[str] = Field(default=None, description="Filter by resource group")
    ) -> str:
        """List all virtual machines."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = ComputeManagementClient(credential, sub_id)

            vms = []
            if resource_group:
                for vm in client.virtual_machines.list(resource_group):
                    vms.append(format_vm(vm))
            else:
                for vm in client.virtual_machines.list_all():
                    vms.append(format_vm(vm))

            if not vms:
                return "No virtual machines found"

            result = "# Virtual Machines\n\n"
            result += "| Name | Location | Size | OS | State |\n"
            result += "|------|----------|------|-------|-------|\n"
            for vm in vms:
                result += f"| {vm['name']} | {vm['location']} | {vm['vm_size']} | {vm['os_type']} | {vm['provisioning_state']} |\n"

            result += f"\n**Total:** {len(vms)} VMs"
            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_get_vm",
        annotations={
            "title": "Get VM Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_get_vm(
        vm_name: str = Field(..., description="Virtual machine name"),
        resource_group: str = Field(..., description="Resource group name"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        include_instance_view: bool = Field(default=True, description="Include power state and status")
    ) -> str:
        """Get detailed information about a virtual machine."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = ComputeManagementClient(credential, sub_id)
            expand = "instanceView" if include_instance_view else None
            vm = client.virtual_machines.get(resource_group, vm_name, expand=expand)

            result = f"# VM: {vm.name}\n\n"
            result += f"**Location:** {vm.location}\n"
            result += f"**Size:** {vm.hardware_profile.vm_size}\n"
            result += f"**Provisioning State:** {vm.provisioning_state}\n"

            if vm.instance_view:
                statuses = vm.instance_view.statuses or []
                for status in statuses:
                    if status.code.startswith("PowerState"):
                        result += f"**Power State:** {status.display_status}\n"

            if vm.storage_profile and vm.storage_profile.os_disk:
                od = vm.storage_profile.os_disk
                result += f"\n## OS Disk\n"
                result += f"- **Name:** {od.name}\n"
                result += f"- **OS Type:** {od.os_type}\n"
                if od.disk_size_gb:
                    result += f"- **Size:** {od.disk_size_gb} GB\n"

            if vm.storage_profile and vm.storage_profile.data_disks:
                result += f"\n## Data Disks\n"
                for disk in vm.storage_profile.data_disks:
                    result += f"- **{disk.name}:** {disk.disk_size_gb} GB (LUN {disk.lun})\n"

            if vm.tags:
                result += f"\n## Tags\n"
                for k, v in vm.tags.items():
                    result += f"- **{k}:** {v}\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_vm_power",
        annotations={
            "title": "VM Power Operations",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_vm_power(
        vm_name: str = Field(..., description="Virtual machine name"),
        resource_group: str = Field(..., description="Resource group name"),
        action: str = Field(..., description="Power action: 'start', 'stop', 'restart', 'deallocate'"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID")
    ) -> str:
        """Control VM power state."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = ComputeManagementClient(credential, sub_id)

            action_lower = action.lower()
            if action_lower == "start":
                poller = client.virtual_machines.begin_start(resource_group, vm_name)
                action_desc = "Starting"
            elif action_lower == "stop":
                poller = client.virtual_machines.begin_power_off(resource_group, vm_name)
                action_desc = "Stopping"
            elif action_lower == "restart":
                poller = client.virtual_machines.begin_restart(resource_group, vm_name)
                action_desc = "Restarting"
            elif action_lower == "deallocate":
                poller = client.virtual_machines.begin_deallocate(resource_group, vm_name)
                action_desc = "Deallocating"
            else:
                return f"Error: Invalid action '{action}'. Use: start, stop, restart, deallocate"

            return f"✅ {action_desc} VM '{vm_name}'\n\nOperation initiated. Use azure_get_vm to check status."

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # Storage Account Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_storage_accounts",
        annotations={
            "title": "List Storage Accounts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_storage_accounts(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_group: Optional[str] = Field(default=None, description="Filter by resource group")
    ) -> str:
        """List all storage accounts."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = StorageManagementClient(credential, sub_id)

            accounts = []
            if resource_group:
                for sa in client.storage_accounts.list_by_resource_group(resource_group):
                    accounts.append(sa)
            else:
                for sa in client.storage_accounts.list():
                    accounts.append(sa)

            if not accounts:
                return "No storage accounts found"

            result = "# Storage Accounts\n\n"
            result += "| Name | Location | SKU | Kind | Access Tier |\n"
            result += "|------|----------|-----|------|-------------|\n"
            for sa in accounts:
                sku = sa.sku.name if sa.sku else "-"
                tier = sa.access_tier if sa.access_tier else "-"
                result += f"| {sa.name} | {sa.location} | {sku} | {sa.kind} | {tier} |\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_create_storage_account",
        annotations={
            "title": "Create Storage Account",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_create_storage_account(
        name: str = Field(..., description="Storage account name (3-24 chars, lowercase and numbers only)", min_length=3, max_length=24),
        resource_group: str = Field(..., description="Resource group name"),
        location: str = Field(..., description="Azure region"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        sku: str = Field(default="Standard_LRS", description="SKU: Standard_LRS, Standard_GRS, Standard_RAGRS, Premium_LRS"),
        kind: str = Field(default="StorageV2", description="Kind: StorageV2, BlobStorage, FileStorage"),
        access_tier: str = Field(default="Hot", description="Access tier: Hot, Cool"),
        tags: Optional[str] = Field(default=None, description="Tags as JSON")
    ) -> str:
        """Create a new storage account."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = StorageManagementClient(credential, sub_id)

            sa_params = {
                "location": location,
                "sku": {"name": sku},
                "kind": kind,
                "access_tier": access_tier,
            }
            if tags:
                sa_params["tags"] = json.loads(tags)

            poller = client.storage_accounts.begin_create(resource_group, name, sa_params)
            result = poller.result()

            return f"✅ Storage account created\n\n" \
                   f"**Name:** {result.name}\n" \
                   f"**Location:** {result.location}\n" \
                   f"**SKU:** {result.sku.name}\n" \
                   f"**Kind:** {result.kind}"

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # VPN Gateway Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_vpn_gateways",
        annotations={
            "title": "List VPN Gateways",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_vpn_gateways(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_group: Optional[str] = Field(default=None, description="Filter by resource group")
    ) -> str:
        """List all VPN gateways."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            gateways = []
            if resource_group:
                for gw in client.virtual_network_gateways.list(resource_group):
                    gateways.append(gw)
            else:
                res_client = ResourceManagementClient(credential, sub_id)
                for rg in res_client.resource_groups.list():
                    try:
                        for gw in client.virtual_network_gateways.list(rg.name):
                            gateways.append(gw)
                    except:
                        continue

            if not gateways:
                return "No VPN gateways found"

            result = "# VPN Gateways\n\n"
            for gw in gateways:
                result += f"## {gw.name}\n"
                result += f"- **Location:** {gw.location}\n"
                result += f"- **Type:** {gw.gateway_type}\n"
                result += f"- **VPN Type:** {gw.vpn_type}\n"
                result += f"- **SKU:** {gw.sku.name if gw.sku else 'N/A'}\n"
                result += f"- **State:** {gw.provisioning_state}\n\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_list_vpn_connections",
        annotations={
            "title": "List VPN Connections",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_vpn_connections(
        resource_group: str = Field(..., description="Resource group name"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID")
    ) -> str:
        """List VPN connections in a resource group."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            connections = []
            for conn in client.virtual_network_gateway_connections.list(resource_group):
                connections.append(conn)

            if not connections:
                return f"No VPN connections found in '{resource_group}'"

            result = "# VPN Connections\n\n"
            result += "| Name | Type | Status | Egress | Ingress |\n"
            result += "|------|------|--------|--------|----------|\n"
            for conn in connections:
                egress = conn.egress_bytes_transferred or 0
                ingress = conn.ingress_bytes_transferred or 0
                result += f"| {conn.name} | {conn.connection_type} | {conn.connection_status or conn.provisioning_state} | {egress:,} | {ingress:,} |\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_list_local_network_gateways",
        annotations={
            "title": "List Local Network Gateways",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_local_network_gateways(
        resource_group: str = Field(..., description="Resource group name"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID")
    ) -> str:
        """List local network gateways (on-premises sites)."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            gateways = []
            for lgw in client.local_network_gateways.list(resource_group):
                gateways.append(lgw)

            if not gateways:
                return f"No local network gateways found in '{resource_group}'"

            result = "# Local Network Gateways\n\n"
            for lgw in gateways:
                result += f"## {lgw.name}\n"
                result += f"- **Gateway IP:** {lgw.gateway_ip_address}\n"
                if lgw.local_network_address_space:
                    result += f"- **Address Prefixes:** {', '.join(lgw.local_network_address_space.address_prefixes or [])}\n"
                if lgw.bgp_settings:
                    result += f"- **BGP ASN:** {lgw.bgp_settings.asn}\n"
                result += "\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # Cost Management
    # =========================================================================

    @mcp.tool(
        name="azure_get_cost_summary",
        annotations={
            "title": "Get Cost Summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_get_cost_summary(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_group: Optional[str] = Field(default=None, description="Filter by resource group"),
        days: int = Field(default=30, description="Number of days to analyze", ge=1, le=365)
    ) -> str:
        """Get cost summary for a subscription or resource group."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = CostManagementClient(credential)

            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days)

            if resource_group:
                scope = f"/subscriptions/{sub_id}/resourceGroups/{resource_group}"
            else:
                scope = f"/subscriptions/{sub_id}"

            query = {
                "type": "ActualCost",
                "timeframe": "Custom",
                "time_period": {
                    "from": start_date.strftime("%Y-%m-%dT00:00:00Z"),
                    "to": end_date.strftime("%Y-%m-%dT23:59:59Z")
                },
                "dataset": {
                    "granularity": "None",
                    "aggregation": {
                        "totalCost": {"name": "Cost", "function": "Sum"}
                    },
                    "grouping": [{"type": "Dimension", "name": "ServiceName"}]
                }
            }

            result_data = client.query.usage(scope, query)

            result = f"# Azure Cost Summary\n\n"
            result += f"**Period:** {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')} ({days} days)\n"
            result += f"**Scope:** {resource_group or 'Full Subscription'}\n\n"

            if result_data.rows:
                result += "| Service | Cost |\n|---------|------|\n"
                total = 0
                for row in sorted(result_data.rows, key=lambda x: float(x[0]), reverse=True):
                    cost = float(row[0])
                    service = row[1] if len(row) > 1 else "Unknown"
                    total += cost
                    result += f"| {service} | ${cost:,.2f} |\n"
                result += f"| **TOTAL** | **${total:,.2f}** |\n"
            else:
                result += "No cost data available."

            return result

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # Public IP Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_public_ips",
        annotations={
            "title": "List Public IP Addresses",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_public_ips(
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        resource_group: Optional[str] = Field(default=None, description="Filter by resource group")
    ) -> str:
        """List all public IP addresses."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            ips = []
            if resource_group:
                for ip in client.public_ip_addresses.list(resource_group):
                    ips.append(ip)
            else:
                for ip in client.public_ip_addresses.list_all():
                    ips.append(ip)

            if not ips:
                return "No public IP addresses found"

            result = "# Public IP Addresses\n\n"
            result += "| Name | IP Address | Location | Allocation | SKU |\n"
            result += "|------|------------|----------|------------|-----|\n"
            for ip in ips:
                addr = ip.ip_address or "Not assigned"
                sku = ip.sku.name if ip.sku else "-"
                result += f"| {ip.name} | {addr} | {ip.location} | {ip.public_ip_allocation_method} | {sku} |\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_create_public_ip",
        annotations={
            "title": "Create Public IP Address",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_create_public_ip(
        name: str = Field(..., description="Public IP name"),
        resource_group: str = Field(..., description="Resource group name"),
        location: str = Field(..., description="Azure region"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        allocation_method: str = Field(default="Static", description="Allocation: 'Static' or 'Dynamic'"),
        sku: str = Field(default="Standard", description="SKU: 'Basic' or 'Standard'"),
        dns_label: Optional[str] = Field(default=None, description="DNS label for the IP"),
        tags: Optional[str] = Field(default=None, description="Tags as JSON")
    ) -> str:
        """Create a new public IP address."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            ip_params = {
                "location": location,
                "public_ip_allocation_method": allocation_method,
                "sku": {"name": sku}
            }
            if dns_label:
                ip_params["dns_settings"] = {"domain_name_label": dns_label}
            if tags:
                ip_params["tags"] = json.loads(tags)

            poller = client.public_ip_addresses.begin_create_or_update(resource_group, name, ip_params)
            result = poller.result()

            return f"✅ Public IP created\n\n" \
                   f"**Name:** {result.name}\n" \
                   f"**IP:** {result.ip_address or 'Pending'}\n" \
                   f"**Allocation:** {result.public_ip_allocation_method}"

        except Exception as e:
            return handle_azure_error(e)

    # =========================================================================
    # VNet Peering Tools
    # =========================================================================

    @mcp.tool(
        name="azure_list_vnet_peerings",
        annotations={
            "title": "List VNet Peerings",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_list_vnet_peerings(
        vnet_name: str = Field(..., description="Virtual network name"),
        resource_group: str = Field(..., description="Resource group name"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID")
    ) -> str:
        """List all peerings for a virtual network."""
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            peerings = []
            for peering in client.virtual_network_peerings.list(resource_group, vnet_name):
                peerings.append(peering)

            if not peerings:
                return f"No peerings found for VNet '{vnet_name}'"

            result = f"# VNet Peerings for {vnet_name}\n\n"
            for p in peerings:
                result += f"## {p.name}\n"
                result += f"- **State:** {p.peering_state}\n"
                result += f"- **Remote VNet:** {p.remote_virtual_network.id.split('/')[-1] if p.remote_virtual_network else 'N/A'}\n"
                result += f"- **Allow VNet Access:** {p.allow_virtual_network_access}\n"
                result += f"- **Allow Forwarded Traffic:** {p.allow_forwarded_traffic}\n"
                result += f"- **Allow Gateway Transit:** {p.allow_gateway_transit}\n"
                result += f"- **Use Remote Gateways:** {p.use_remote_gateways}\n\n"

            return result

        except Exception as e:
            return handle_azure_error(e)

    @mcp.tool(
        name="azure_create_vnet_peering",
        annotations={
            "title": "Create VNet Peering",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def azure_create_vnet_peering(
        peering_name: str = Field(..., description="Name for the peering"),
        vnet_name: str = Field(..., description="Source VNet name"),
        resource_group: str = Field(..., description="Source VNet resource group"),
        remote_vnet_id: str = Field(..., description="Full resource ID of the remote VNet"),
        subscription_id: Optional[str] = Field(default=None, description="Subscription ID"),
        allow_vnet_access: bool = Field(default=True, description="Allow VNet access"),
        allow_forwarded_traffic: bool = Field(default=False, description="Allow forwarded traffic"),
        allow_gateway_transit: bool = Field(default=False, description="Allow gateway transit"),
        use_remote_gateways: bool = Field(default=False, description="Use remote gateways")
    ) -> str:
        """Create a VNet peering connection.

        Note: Create peering from both VNets for bidirectional connectivity.
        """
        try:
            credential = get_azure_credentials()
            sub_id = subscription_id or get_default_subscription()
            if not sub_id:
                return "Error: No subscription ID provided and no default configured."

            client = NetworkManagementClient(credential, sub_id)

            peering_params = {
                "remote_virtual_network": {"id": remote_vnet_id},
                "allow_virtual_network_access": allow_vnet_access,
                "allow_forwarded_traffic": allow_forwarded_traffic,
                "allow_gateway_transit": allow_gateway_transit,
                "use_remote_gateways": use_remote_gateways
            }

            poller = client.virtual_network_peerings.begin_create_or_update(
                resource_group, vnet_name, peering_name, peering_params
            )
            result = poller.result()

            return f"✅ VNet peering created\n\n" \
                   f"**Name:** {result.name}\n" \
                   f"**State:** {result.peering_state}\n\n" \
                   f"⚠️ Remember to create the reciprocal peering from the remote VNet."

        except Exception as e:
            return handle_azure_error(e)

    print("✅ Azure tools registered successfully")
