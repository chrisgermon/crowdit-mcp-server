"""
Crowd IT Unified MCP Server
Centralized MCP server for Cloud Run - HaloPSA, Xero, Front, SharePoint, Quoter, and Pax8 integration.
"""

import os
import asyncio
import logging
import json
from datetime import datetime, timedelta, date
from typing import Optional
import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cloud Run URL for OAuth callback
CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "https://crowdit-mcp-server-lypf4vkh4q-ts.a.run.app")

mcp = FastMCP(
    name="crowdit-mcp-server",
    instructions="Crowd IT Unified MCP Server - HaloPSA, Xero, Front, SharePoint, Quoter, and Pax8 integration for MSP operations.",
    stateless_http=True  # Required for Cloud Run - enables stateless sessions
)

# ============================================================================
# Secret Manager Helper
# ============================================================================

def update_secret_sync(secret_id: str, value: str) -> bool:
    """Update a secret in Google Secret Manager (sync version)."""
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", os.getenv("BIGQUERY_PROJECT_ID", "crowdmcp"))
        parent = f"projects/{project_id}/secrets/{secret_id}"
        
        client.add_secret_version(
            request={
                "parent": parent,
                "payload": {"data": value.encode("UTF-8")}
            }
        )
        logger.info(f"Updated secret: {secret_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to update secret {secret_id}: {e}")
        return False


# ============================================================================
# HaloPSA Integration
# ============================================================================

class HaloPSAConfig:
    def __init__(self):
        self.resource_server = os.getenv("HALOPSA_RESOURCE_SERVER", "").rstrip("/")
        self.auth_server = os.getenv("HALOPSA_AUTH_SERVER", "").rstrip("/")
        self.client_id = os.getenv("HALOPSA_CLIENT_ID", "")
        self.client_secret = os.getenv("HALOPSA_CLIENT_SECRET", "")
        self.tenant = os.getenv("HALOPSA_TENANT", "")
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
    
    @property
    def is_configured(self) -> bool:
        return all([self.resource_server, self.auth_server, self.client_id, self.client_secret])
    
    async def get_access_token(self) -> str:
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token
        
        token_url = f"{self.auth_server}/token"
        if self.tenant:
            token_url += f"?tenant={self.tenant}"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "all"
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            return self._access_token

halopsa_config = HaloPSAConfig()

@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def halopsa_search_tickets(
    status: Optional[str] = Field(None, description="Filter: 'open', 'closed', 'pending', 'in_progress', or 'all'"),
    client_name: Optional[str] = Field(None, description="Filter by client name (partial match)"),
    search_text: Optional[str] = Field(None, description="Search in ticket summary/details"),
    days_old: Optional[int] = Field(None, description="Tickets created within N days"),
    limit: int = Field(20, description="Max results (1-100)")
) -> str:
    """Search HaloPSA tickets with filters."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(max(1, limit), 100), "order": "dateoccurred", "orderdesc": "true"}
        
        status_map = {"open": "1", "closed": "2", "pending": "3", "in_progress": "4"}
        if status and status.lower() in status_map:
            params["status_id"] = status_map[status.lower()]
        if search_text:
            params["search"] = search_text
        if days_old:
            params["dateoccurred_start"] = (datetime.now() - timedelta(days=days_old)).strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Tickets", params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            tickets = response.json().get("tickets", [])
        
        if not tickets:
            return "No tickets found."
        
        results = []
        for t in tickets[:limit]:
            if client_name and client_name.lower() not in t.get("client_name", "").lower():
                continue
            results.append(f"**#{t.get('id')}** - {t.get('summary', 'No summary')}\n  Client: {t.get('client_name', 'N/A')} | Status: {t.get('status_name', 'N/A')} | Priority: {t.get('priority_name', 'N/A')}")
        
        return f"Found {len(results)} ticket(s):\n\n" + "\n\n".join(results) if results else "No tickets found."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def halopsa_get_ticket(ticket_id: int = Field(..., description="Ticket ID")) -> str:
    """Get full ticket details."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Tickets/{ticket_id}",
                params={"includedetails": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            t = response.json()
        
        return f"# Ticket #{t.get('id')} - {t.get('summary')}\n\nClient: {t.get('client_name')}\nStatus: {t.get('status_name')}\nPriority: {t.get('priority_name')}\nAgent: {t.get('agent_name', 'Unassigned')}\nCreated: {t.get('dateoccurred')}\n\n## Description\n{t.get('details', 'No description')}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
async def halopsa_update_ticket(
    ticket_id: int = Field(..., description="Ticket ID"),
    status: Optional[str] = Field(None, description="New status: 'open', 'closed', 'pending', 'in_progress'"),
    note: Optional[str] = Field(None, description="Note to add")
) -> str:
    """Update ticket status or add note."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        payload = [{"id": ticket_id}]
        
        if status:
            status_map = {"open": 1, "closed": 2, "pending": 3, "in_progress": 4}
            if status.lower() in status_map:
                payload[0]["status_id"] = status_map[status.lower()]
        if note:
            payload[0]["note"] = note
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{halopsa_config.resource_server}/Tickets", json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
        
        return f"✅ Ticket #{ticket_id} updated."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_clients(search: Optional[str] = Field(None, description="Search by name"), limit: int = Field(20, description="Max results")) -> str:
    """List HaloPSA clients."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}
        if search:
            params["search"] = search
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Client", params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            clients = response.json().get("clients", [])
        
        if not clients:
            return "No clients found."
        
        return "## Clients\n\n" + "\n".join([f"- **{c.get('name')}** (ID: {c.get('id')})" for c in clients])
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_client(client_id: int = Field(..., description="Client ID")) -> str:
    """Get detailed client information including contacts and sites."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Client/{client_id}",
                params={"includedetails": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            c = response.json()
        
        result = f"""# {c.get('name', 'Unknown Client')}

**ID:** {c.get('id')}
**Status:** {c.get('inactive', False) and 'Inactive' or 'Active'}
**Email:** {c.get('main_email', 'N/A')}
**Phone:** {c.get('main_phone', 'N/A')}
**Website:** {c.get('website', 'N/A')}
**Address:** {c.get('address', 'N/A')}
**Notes:** {c.get('notes', 'N/A')}
"""
        return result
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_create_ticket(
    summary: str = Field(..., description="Ticket summary/title"),
    details: str = Field(..., description="Ticket description"),
    client_id: int = Field(..., description="Client ID"),
    tickettype_id: int = Field(1, description="Ticket type ID (default: 1)"),
    priority_id: int = Field(3, description="Priority ID (1=Critical, 2=High, 3=Medium, 4=Low)"),
    user_id: Optional[int] = Field(None, description="Assign to user/agent ID")
) -> str:
    """Create a new ticket in HaloPSA."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        payload = [{
            "summary": summary,
            "details": details,
            "client_id": client_id,
            "tickettype_id": tickettype_id,
            "priority_id": priority_id,
        }]
        if user_id:
            payload[0]["user_id"] = user_id
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{halopsa_config.resource_server}/Tickets", json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            result = response.json()
        
        ticket_id = result.get("id") if isinstance(result, dict) else result[0].get("id") if result else "Unknown"
        return f"✅ Ticket #{ticket_id} created successfully."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_ticket_actions(
    ticket_id: int = Field(..., description="Ticket ID"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Get all actions/notes on a ticket."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Actions",
                params={"ticket_id": ticket_id, "count": limit},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            actions = response.json().get("actions", [])
        
        if not actions:
            return f"No actions found for ticket #{ticket_id}."
        
        results = []
        for a in actions:
            who = a.get('who', 'Unknown')
            when = a.get('actioned_date', '')[:16]
            note = a.get('note', a.get('outcome', 'No content'))[:500]
            action_type = a.get('actiontype_name', 'Note')
            results.append(f"**{action_type}** by {who} ({when}):\n{note}")
        
        return f"## Actions for Ticket #{ticket_id}\n\n" + "\n\n---\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_add_action(
    ticket_id: int = Field(..., description="Ticket ID"),
    note: str = Field(..., description="Action note/content"),
    outcome: str = Field("Note added", description="Action outcome"),
    timetaken: int = Field(0, description="Time taken in minutes"),
    sendemail: bool = Field(False, description="Send email to client")
) -> str:
    """Add an action/note to a ticket."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        payload = [{
            "ticket_id": ticket_id,
            "note": note,
            "outcome": outcome,
            "timetaken": timetaken,
            "sendemail": sendemail
        }]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{halopsa_config.resource_server}/Actions", json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
        
        return f"✅ Action added to ticket #{ticket_id}."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_invoices(
    client_id: Optional[int] = Field(None, description="Filter by client ID"),
    status: Optional[str] = Field(None, description="Filter: 'draft', 'sent', 'paid', 'overdue'"),
    days: int = Field(90, description="Invoices from last N days"),
    limit: int = Field(50, description="Max results")
) -> str:
    """Get HaloPSA invoices."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100), "order": "date", "orderdesc": "true"}
        
        if client_id:
            params["client_id"] = client_id
        if days:
            params["date_start"] = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Invoice",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            invoices = response.json().get("invoices", [])
        
        if not invoices:
            return "No invoices found."
        
        results = []
        for inv in invoices[:limit]:
            inv_id = inv.get('id', 'N/A')
            client_name = inv.get('client_name', inv.get('clientname', 'Unknown'))
            total = inv.get('total', inv.get('grosstotal', inv.get('gross_total', 0)))
            status_name = inv.get('status_name', inv.get('statusname', inv.get('status', 'N/A')))
            date_str = str(inv.get('date', inv.get('invoicedate', inv.get('dateoccurred', ''))))[:10]
            ref = inv.get('ref', inv.get('invoicenumber', inv.get('invoice_number', 'N/A')))
            posted = "✓ Posted" if inv.get('posted_to_accounting', inv.get('postedtoaccounting', False)) else "Not posted"

            results.append(f"**#{inv_id}** ({ref}) - {client_name}\n  Total: ${total:,.2f} | Status: {status_name} | Date: {date_str} | {posted}")
        
        return f"Found {len(results)} invoice(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_invoice(invoice_id: int = Field(..., description="Invoice ID")) -> str:
    """Get detailed invoice information including line items."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Invoice/{invoice_id}",
                params={"includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            inv = response.json()
        
        lines = []
        for item in inv.get('lines', []):
            desc = item.get('description', item.get('itemname', item.get('item_name', item.get('shortdescription', 'No description'))))[:80]
            qty = item.get('quantity', item.get('qty', item.get('count', 1)))
            price = item.get('price', item.get('unitprice', item.get('unit_price', 0)))
            total = item.get('total', item.get('netamount', item.get('net_amount', 0)))
            lines.append(f"- {desc} (Qty: {qty} x ${price:,.2f}) = ${total:,.2f}")

        posted = "Posted to accounting" if inv.get('posted_to_accounting', inv.get('postedtoaccounting', False)) else "Not posted to accounting"
        xero_id = inv.get('accounting_id', inv.get('accountingid', inv.get('xeroinvoiceid', 'N/A')))
        client_name = inv.get('client_name', inv.get('clientname', 'Unknown'))
        ref = inv.get('ref', inv.get('invoicenumber', inv.get('invoice_number', 'N/A')))
        status_name = inv.get('status_name', inv.get('statusname', inv.get('status', 'N/A')))

        return f"""# Invoice #{inv.get('id')} - {ref}

**Client:** {client_name}
**Status:** {status_name}
**Date:** {str(inv.get('date', inv.get('invoicedate', '')))[:10]}
**Due Date:** {str(inv.get('duedate', inv.get('due_date', '')))[:10]}
**PO Number:** {inv.get('ponumber', inv.get('po_number', 'N/A'))}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Subtotal:** ${inv.get('subtotal', inv.get('nettotal', 0)):,.2f}
**Tax:** ${inv.get('tax', inv.get('taxtotal', 0)):,.2f}
**Total:** ${inv.get('total', inv.get('grosstotal', 0)):,.2f}

**{posted}**
**Xero Invoice ID:** {xero_id}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_unposted_invoices(
    days: int = Field(30, description="Invoices from last N days"),
    limit: int = Field(50, description="Max results")
) -> str:
    """Get HaloPSA invoices that haven't been posted to Xero."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {
            "count": min(limit, 100),
            "order": "date",
            "orderdesc": "true",
            "date_start": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Invoice",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            invoices = response.json().get("invoices", [])
        
        unposted = [inv for inv in invoices if not inv.get('posted_to_accounting')]
        
        if not unposted:
            return f"All invoices from the last {days} days have been posted to accounting."
        
        results = []
        for inv in unposted[:limit]:
            inv_id = inv.get('id', 'N/A')
            client_name = inv.get('client_name', 'Unknown')
            total = inv.get('total', 0)
            date_str = inv.get('date', '')[:10]
            ref = inv.get('ref', 'N/A')
            
            results.append(f"**#{inv_id}** ({ref}) - {client_name}\n  Total: ${total:,.2f} | Date: {date_str}")
        
        return f"Found {len(results)} unposted invoice(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_assets(
    client_id: Optional[int] = Field(None, description="Filter by client ID"),
    search: Optional[str] = Field(None, description="Search by name/serial/asset tag"),
    asset_type: Optional[str] = Field(None, description="Filter by asset type"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List assets/configuration items."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}
        
        if client_id:
            params["client_id"] = client_id
        if search:
            params["search"] = search
        if asset_type:
            params["assettype"] = asset_type
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Asset",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            assets = response.json().get("assets", [])
        
        if not assets:
            return "No assets found."
        
        results = []
        for a in assets[:limit]:
            name = a.get('inventory_number', a.get('devicename', 'Unknown'))
            asset_type_name = a.get('assettype_name', 'N/A')
            client_name = a.get('client_name', 'N/A')
            status = a.get('status_name', 'N/A')
            serial = a.get('serial_number', 'N/A')
            
            results.append(f"**{name}** (ID: {a.get('id')})\n  Type: {asset_type_name} | Client: {client_name} | Status: {status} | S/N: {serial}")
        
        return f"Found {len(results)} asset(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_asset(asset_id: int = Field(..., description="Asset ID")) -> str:
    """Get detailed asset information."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Asset/{asset_id}",
                params={"includedetails": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            a = response.json()
        
        return f"""# Asset: {a.get('inventory_number', a.get('devicename', 'Unknown'))}

**ID:** {a.get('id')}
**Type:** {a.get('assettype_name', 'N/A')}
**Client:** {a.get('client_name', 'N/A')}
**Site:** {a.get('site_name', 'N/A')}
**Status:** {a.get('status_name', 'N/A')}

**Device Name:** {a.get('devicename', 'N/A')}
**Serial Number:** {a.get('serial_number', 'N/A')}
**Asset Tag:** {a.get('assettag', 'N/A')}
**Manufacturer:** {a.get('manufacturer', 'N/A')}
**Model:** {a.get('model', 'N/A')}

**IP Address:** {a.get('ip_address', 'N/A')}
**MAC Address:** {a.get('mac_address', 'N/A')}
**OS:** {a.get('operating_system', 'N/A')}

**Purchase Date:** {a.get('purchase_date', 'N/A')}
**Warranty Expiry:** {a.get('warranty_expiry', 'N/A')}

**Notes:** {a.get('notes', 'N/A')}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_contracts(
    client_id: Optional[int] = Field(None, description="Filter by client ID"),
    active_only: bool = Field(True, description="Only show active contracts"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List contracts/recurring invoices."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}
        
        if client_id:
            params["client_id"] = client_id
        if active_only:
            params["inactive"] = "false"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Contract",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            contracts = response.json().get("contracts", [])
        
        if not contracts:
            return "No contracts found."
        
        results = []
        for c in contracts[:limit]:
            name = c.get('ref', 'Unknown')
            client_name = c.get('client_name', 'N/A')
            billing = c.get('billing_cycle_name', 'N/A')
            value = c.get('value', 0)
            start = c.get('start_date', '')[:10]
            end = c.get('end_date', '')[:10]
            
            results.append(f"**{name}** (ID: {c.get('id')})\n  Client: {client_name} | Value: ${value:,.2f} | Billing: {billing}\n  Period: {start} to {end}")
        
        return f"Found {len(results)} contract(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_contract(contract_id: int = Field(..., description="Contract ID")) -> str:
    """Get detailed contract information."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Contract/{contract_id}",
                params={"includedetails": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            c = response.json()
        
        return f"""# Contract: {c.get('ref', 'Unknown')}

**ID:** {c.get('id')}
**Client:** {c.get('client_name', 'N/A')}
**Status:** {'Active' if not c.get('inactive') else 'Inactive'}

**Value:** ${c.get('value', 0):,.2f}
**Billing Cycle:** {c.get('billing_cycle_name', 'N/A')}
**Payment Terms:** {c.get('payment_terms_name', 'N/A')}

**Start Date:** {c.get('start_date', 'N/A')[:10] if c.get('start_date') else 'N/A'}
**End Date:** {c.get('end_date', 'N/A')[:10] if c.get('end_date') else 'N/A'}
**Next Invoice:** {c.get('next_invoice_date', 'N/A')[:10] if c.get('next_invoice_date') else 'N/A'}

**Notes:** {c.get('notes', 'N/A')}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_sites(
    client_id: Optional[int] = Field(None, description="Filter by client ID"),
    search: Optional[str] = Field(None, description="Search by name"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List client sites."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}
        
        if client_id:
            params["client_id"] = client_id
        if search:
            params["search"] = search
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Site",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            sites = response.json().get("sites", [])
        
        if not sites:
            return "No sites found."
        
        results = []
        for s in sites[:limit]:
            name = s.get('name', 'Unknown')
            client_name = s.get('client_name', 'N/A')
            address = s.get('address', 'N/A')
            
            results.append(f"**{name}** (ID: {s.get('id')})\n  Client: {client_name} | Address: {address}")
        
        return f"Found {len(results)} site(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_agents(
    search: Optional[str] = Field(None, description="Search by name"),
    include_inactive: bool = Field(False, description="Include inactive agents"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List agents/technicians."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}
        
        if search:
            params["search"] = search
        if not include_inactive:
            params["inactive"] = "false"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Agent",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            agents = response.json().get("agents", [])
        
        if not agents:
            return "No agents found."
        
        results = []
        for a in agents[:limit]:
            name = a.get('name', 'Unknown')
            email = a.get('email', 'N/A')
            team = a.get('team_name', 'N/A')
            
            results.append(f"**{name}** (ID: {a.get('id')})\n  Email: {email} | Team: {team}")
        
        return f"Found {len(results)} agent(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_projects(
    client_id: Optional[int] = Field(None, description="Filter by client ID"),
    active_only: bool = Field(True, description="Only show active projects"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List projects."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100), "order": "dateoccurred", "orderdesc": "true"}
        
        if client_id:
            params["client_id"] = client_id
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Projects",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            projects = response.json().get("projects", [])
        
        if not projects:
            return "No projects found."
        
        results = []
        for p in projects[:limit]:
            name = p.get('summary', 'Unknown')
            client_name = p.get('client_name', 'N/A')
            status = p.get('status_name', 'N/A')
            budget = p.get('budgethours', 0)
            
            results.append(f"**{name}** (ID: {p.get('id')})\n  Client: {client_name} | Status: {status} | Budget: {budget}h")
        
        return f"Found {len(results)} project(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_quotes(
    client_id: Optional[int] = Field(None, description="Filter by client ID"),
    status: Optional[str] = Field(None, description="Filter: 'draft', 'sent', 'accepted', 'declined'"),
    days: int = Field(90, description="Quotes from last N days"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List quotes/proposals."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100), "order": "datecreated", "orderdesc": "true"}
        
        if client_id:
            params["client_id"] = client_id
        if days:
            params["datecreated_start"] = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Quotation",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            quotes = response.json().get("quotations", [])
        
        if not quotes:
            return "No quotes found."
        
        results = []
        for q in quotes[:limit]:
            ref = q.get('quotationnumber', 'N/A')
            client_name = q.get('client_name', 'N/A')
            total = q.get('total', 0)
            status_name = q.get('status_name', 'N/A')
            date_str = q.get('datecreated', '')[:10]
            
            results.append(f"**{ref}** (ID: {q.get('id')}) - {client_name}\n  Total: ${total:,.2f} | Status: {status_name} | Date: {date_str}")
        
        return f"Found {len(results)} quote(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_time_entries(
    ticket_id: Optional[int] = Field(None, description="Filter by ticket ID"),
    agent_id: Optional[int] = Field(None, description="Filter by agent ID"),
    days: int = Field(7, description="Time entries from last N days"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List time entries."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {
            "count": min(limit, 100),
            "order": "startdate",
            "orderdesc": "true",
            "startdate_start": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        }
        
        if ticket_id:
            params["ticket_id"] = ticket_id
        if agent_id:
            params["agent_id"] = agent_id
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Timesheet",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            entries = response.json().get("timesheets", [])
        
        if not entries:
            return "No time entries found."
        
        results = []
        total_hours = 0
        for t in entries[:limit]:
            agent = t.get('agent_name', 'Unknown')
            hours = t.get('hours', 0)
            total_hours += hours
            ticket = t.get('ticket_id', 'N/A')
            date_str = t.get('startdate', '')[:10]
            note = t.get('note', '')[:50]
            
            results.append(f"**{hours}h** - {agent} on #{ticket} ({date_str})\n  {note}")
        
        return f"Found {len(results)} time entries (Total: {total_hours:.1f}h):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_log_time(
    ticket_id: int = Field(..., description="Ticket ID"),
    hours: float = Field(..., description="Hours to log"),
    note: str = Field("", description="Time entry note"),
    agent_id: Optional[int] = Field(None, description="Agent ID (defaults to API user)")
) -> str:
    """Log time against a ticket."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        payload = [{
            "ticket_id": ticket_id,
            "hours": hours,
            "note": note,
            "startdate": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        }]
        if agent_id:
            payload[0]["agent_id"] = agent_id
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{halopsa_config.resource_server}/Timesheet", json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
        
        return f"✅ Logged {hours}h to ticket #{ticket_id}."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_search_kb(
    search: str = Field(..., description="Search query"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Search knowledge base articles."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {"search": search, "count": min(limit, 100)}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/KBArticle",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            articles = response.json().get("kbarticles", [])
        
        if not articles:
            return f"No KB articles found for '{search}'."
        
        results = []
        for a in articles[:limit]:
            title = a.get('name', 'Untitled')
            category = a.get('category_name', 'N/A')
            
            results.append(f"**{title}** (ID: {a.get('id')})\n  Category: {category}")
        
        return f"Found {len(results)} KB article(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_ticket_summary(
    days: int = Field(7, description="Summary for last N days"),
    client_id: Optional[int] = Field(None, description="Filter by client ID")
) -> str:
    """Get a summary of tickets for the period."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        params = {
            "count": 500,
            "dateoccurred_start": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        }
        if client_id:
            params["client_id"] = client_id
        
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/Tickets",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            tickets = response.json().get("tickets", [])
        
        if not tickets:
            return f"No tickets in the last {days} days."
        
        total = len(tickets)
        by_status = {}
        by_priority = {}
        by_client = {}
        
        for t in tickets:
            status = t.get('status_name', 'Unknown')
            priority = t.get('priority_name', 'Unknown')
            client_name = t.get('client_name', 'Unknown')
            
            by_status[status] = by_status.get(status, 0) + 1
            by_priority[priority] = by_priority.get(priority, 0) + 1
            by_client[client_name] = by_client.get(client_name, 0) + 1
        
        top_clients = sorted(by_client.items(), key=lambda x: x[1], reverse=True)[:5]
        
        status_lines = [f"  - {k}: {v}" for k, v in sorted(by_status.items(), key=lambda x: x[1], reverse=True)]
        priority_lines = [f"  - {k}: {v}" for k, v in sorted(by_priority.items(), key=lambda x: x[1], reverse=True)]
        client_lines = [f"  - {k}: {v}" for k, v in top_clients]
        
        return f"""# Ticket Summary (Last {days} Days)

**Total Tickets:** {total}

## By Status
{chr(10).join(status_lines)}

## By Priority
{chr(10).join(priority_lines)}

## Top 5 Clients
{chr(10).join(client_lines)}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_recurring_invoices(
    client_id: Optional[int] = Field(None, description="Filter by client ID"),
    search: Optional[str] = Field(None, description="Search by name/reference"),
    active_only: bool = Field(True, description="Only show active recurring invoices"),
    limit: int = Field(50, description="Max results"),
    debug: bool = Field(False, description="Return raw API response for debugging")
) -> str:
    """List HaloPSA recurring invoices."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}

        if client_id:
            params["client_id"] = client_id
        if search:
            params["search"] = search
        if active_only:
            params["inactive"] = "false"

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/RecurringInvoice",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            data = response.json()

            # Debug mode - return raw response
            if debug:
                import json
                # Get first item to show field names
                recurring = data.get("invoices", data.get("recurring_invoices", []))
                if recurring:
                    return f"**Raw API Response (first record):**\n```json\n{json.dumps(recurring[0], indent=2, default=str)}\n```"
                return f"**Raw API Response:**\n```json\n{json.dumps(data, indent=2, default=str)}\n```"

            recurring = data.get("invoices", data.get("recurring_invoices", []))

        if not recurring:
            return "No recurring invoices found."

        results = []
        for r in recurring[:limit]:
            rec_id = r.get('id', 'N/A')
            client_name = r.get('client_name', r.get('clientname', 'Unknown'))
            ref = r.get('ref', r.get('invoicenumber', r.get('recurring_invoice_number', 'N/A')))
            # Try multiple possible field names for totals
            total = r.get('total', r.get('grosstotal', r.get('gross_total', r.get('nettotal', 0))))
            billing = r.get('billing_cycle_name', r.get('billingcycle', 'N/A'))
            next_date = str(r.get('next_invoice_date', r.get('nextinvoicedate', '')))[:10]
            active = "Active" if not r.get('inactive', False) else "Inactive"

            results.append(f"**{ref}** (ID: {rec_id})\n  Client: {client_name} | Total: ${total:,.2f} | Billing: {billing}\n  Next Invoice: {next_date} | Status: {active}")

        return f"Found {len(results)} recurring invoice(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_recurring_invoice(
    recurring_invoice_id: int = Field(..., description="Recurring Invoice ID"),
    debug: bool = Field(False, description="Return raw API response for debugging")
) -> str:
    """Get detailed recurring invoice information including line items."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/RecurringInvoice/{recurring_invoice_id}",
                params={"includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            r = response.json()

        # Debug mode - return raw response
        if debug:
            import json
            return f"**Raw API Response:**\n```json\n{json.dumps(r, indent=2, default=str)}\n```"

        lines = []
        for idx, item in enumerate(r.get('lines', []), 1):
            line_id = item.get('id', 'N/A')
            # Try multiple field names for description
            desc = item.get('description', item.get('itemname', item.get('item_name', item.get('shortdescription', 'No description'))))
            # Try multiple field names for quantity
            qty = item.get('quantity', item.get('qty', item.get('count', 1)))
            # Try multiple field names for price
            price = item.get('price', item.get('unitprice', item.get('unit_price', item.get('baseprice', 0))))
            # Try multiple field names for net amount
            net = item.get('netamount', item.get('net_amount', item.get('nettotal', item.get('net_total', qty * price if price else 0))))
            tax = item.get('tax', item.get('taxamount', item.get('tax_amount', 0)))
            # Product/item code
            item_code = item.get('accountsid', item.get('xero_product_id', item.get('item_code', item.get('itemcode', ''))))
            active_line = "Active" if item.get('active', not item.get('inactive', False)) else "Inactive"

            lines.append(f"{idx}. **{desc}** (Line ID: {line_id})\n   Code: {item_code} | Qty: {qty} x ${price:,.2f} = ${net:,.2f} (+ ${tax:,.2f} tax) | {active_line}")

        active = "Active" if not r.get('inactive', False) else "Inactive"
        client_name = r.get('client_name', r.get('clientname', 'N/A'))
        ref = r.get('ref', r.get('invoicenumber', r.get('recurring_invoice_number', 'Unknown')))

        # Totals - try multiple field names
        subtotal = r.get('subtotal', r.get('nettotal', r.get('net_total', 0)))
        tax = r.get('tax', r.get('taxtotal', r.get('tax_total', 0)))
        total = r.get('total', r.get('grosstotal', r.get('gross_total', 0)))

        return f"""# Recurring Invoice: {ref}

**ID:** {r.get('id')}
**Client:** {client_name} (Client ID: {r.get('client_id', r.get('clientid', 'N/A'))})
**Status:** {active}
**Billing Cycle:** {r.get('billing_cycle_name', r.get('billingcycle', 'N/A'))}
**Next Invoice Date:** {str(r.get('next_invoice_date', r.get('nextinvoicedate', 'N/A')))[:10]}
**PO Number:** {r.get('ponumber', r.get('po_number', 'N/A'))}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Net Total:** ${subtotal:,.2f}
**Tax:** ${tax:,.2f}
**Gross Total:** ${total:,.2f}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_add_recurring_invoice_line(
    recurring_invoice_id: int = Field(..., description="Recurring Invoice ID"),
    description: str = Field(..., description="Line item description"),
    unit_price: float = Field(..., description="Unit price (ex tax)"),
    quantity: float = Field(1, description="Quantity"),
    item_id: Optional[int] = Field(None, description="HaloPSA Item ID (optional - for linking to catalog item)"),
    tax_code: str = Field("GST", description="Tax code (default: GST)")
) -> str:
    """Add a line item to a recurring invoice."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        
        # First get the existing recurring invoice
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/RecurringInvoice",
                params={"rinvoiceid": -abs(recurring_invoice_id), "includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            existing = response.json()
        
        # Create new line item
        new_line = {
            "description": description,
            "count": quantity,
            "price": unit_price,
            "taxcode": tax_code
        }
        if item_id:
            new_line["item_id"] = item_id
        
        # Get existing lines and append new one
        existing_lines = existing.get('lines', [])
        existing_lines.append(new_line)
        
        # Update the recurring invoice with new lines
        payload = [{
            "id": recurring_invoice_id,
            "lines": existing_lines
        }]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{halopsa_config.resource_server}/RecurringInvoice",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
        
        total_value = quantity * unit_price
        return f"✅ Added line item to recurring invoice #{recurring_invoice_id}:\n- {description}\n- Qty: {quantity} x ${unit_price:,.2f} = ${total_value:,.2f}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_update_recurring_invoice_line(
    recurring_invoice_id: int = Field(..., description="Recurring Invoice ID"),
    line_id: int = Field(..., description="Line item ID to update"),
    description: Optional[str] = Field(None, description="New description"),
    quantity: Optional[float] = Field(None, description="New quantity"),
    unit_price: Optional[float] = Field(None, description="New unit price")
) -> str:
    """Update an existing line item on a recurring invoice."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        
        # First get the existing recurring invoice
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/RecurringInvoice/{recurring_invoice_id}",
                params={"includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            existing = response.json()
        
        # Find and update the line
        lines = existing.get('lines', [])
        line_found = False
        for line in lines:
            if line.get('id') == line_id:
                if description is not None:
                    line['description'] = description
                if quantity is not None:
                    line['count'] = quantity
                if unit_price is not None:
                    line['price'] = unit_price
                line_found = True
                break
        
        if not line_found:
            return f"Error: Line ID {line_id} not found in recurring invoice #{recurring_invoice_id}"
        
        # Update the recurring invoice
        payload = [{
            "id": recurring_invoice_id,
            "lines": lines
        }]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{halopsa_config.resource_server}/RecurringInvoice",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
        
        return f"✅ Updated line #{line_id} on recurring invoice #{recurring_invoice_id}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def halopsa_delete_recurring_invoice_line(
    recurring_invoice_id: int = Field(..., description="Recurring Invoice ID"),
    line_id: int = Field(..., description="Line item ID to delete")
) -> str:
    """Delete a line item from a recurring invoice."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."
    
    try:
        token = await halopsa_config.get_access_token()
        
        # First get the existing recurring invoice
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{halopsa_config.resource_server}/RecurringInvoice/{recurring_invoice_id}",
                params={"includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
            existing = response.json()
        
        # Remove the line
        lines = existing.get('lines', [])
        original_count = len(lines)
        lines = [line for line in lines if line.get('id') != line_id]
        
        if len(lines) == original_count:
            return f"Error: Line ID {line_id} not found in recurring invoice #{recurring_invoice_id}"
        
        # Update the recurring invoice without the deleted line
        payload = [{
            "id": recurring_invoice_id,
            "lines": lines
        }]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{halopsa_config.resource_server}/RecurringInvoice",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            response.raise_for_status()
        
        return f"✅ Deleted line #{line_id} from recurring invoice #{recurring_invoice_id}"
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Xero Integration
# ============================================================================

class XeroConfig:
    def __init__(self):
        self.client_id = os.getenv("XERO_CLIENT_ID", "")
        self.client_secret = os.getenv("XERO_CLIENT_SECRET", "")
        self.tenant_id = os.getenv("XERO_TENANT_ID", "")
        self._refresh_token = os.getenv("XERO_REFRESH_TOKEN", "")
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
    
    @property
    def is_configured(self) -> bool:
        return all([self.client_id, self.client_secret, self.tenant_id, self._refresh_token])
    
    async def get_access_token(self) -> str:
        """Get valid access token, refreshing if needed."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://identity.xero.com/connect/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self._refresh_token
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            data = response.json()
            
            self._access_token = data["access_token"]
            if "refresh_token" in data:
                new_refresh = data["refresh_token"]
                if new_refresh != self._refresh_token:
                    self._refresh_token = new_refresh
                    update_secret_sync("XERO_REFRESH_TOKEN", new_refresh)
                    logger.info("Xero refresh token rotated and saved to Secret Manager")
            
            expires_in = data.get("expires_in", 1800)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            return self._access_token

xero_config = XeroConfig()


async def _resolve_invoice_id(invoice_id: str, access_token: str, tenant_id: str) -> str:
    """
    Resolve an invoice number (e.g., 'INV-6633') to its GUID.
    If already a GUID, returns it unchanged.

    Args:
        invoice_id: Either an invoice number (INV-XXXX) or GUID
        access_token: Xero OAuth access token
        tenant_id: Xero tenant ID

    Returns:
        The invoice GUID

    Raises:
        Exception if invoice not found
    """
    import re

    # Check if it's already a GUID (UUID format)
    guid_pattern = re.compile(
        r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    )

    if guid_pattern.match(invoice_id):
        return invoice_id

    # It's an invoice number, look it up
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": tenant_id,
        "Accept": "application/json",
    }

    # Search by invoice number
    url = f"https://api.xero.com/api.xro/2.0/Invoices?where=InvoiceNumber==\"{invoice_id}\""

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

    invoices = data.get("Invoices", [])
    if not invoices:
        raise Exception(f"Invoice '{invoice_id}' not found")

    return invoices[0]["InvoiceID"]


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_invoices(
    status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'PAID', 'VOIDED'"),
    contact_name: Optional[str] = Field(None, description="Filter by contact name (partial match)"),
    days: int = Field(90, description="Invoices from last N days"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Get Xero invoices with filters."""
    if not xero_config.is_configured:
        return "Error: Xero not configured. Run xero_auth_start to connect."
    
    try:
        token = await xero_config.get_access_token()
        
        where_parts = []
        if status:
            where_parts.append(f'Status=="{status.upper()}"')
        
        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where_parts.append(f'Date>=DateTime({since_date.replace("-", ",")})')
        
        params = {"order": "Date DESC"}
        if where_parts:
            params["where"] = " AND ".join(where_parts)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Invoices",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            invoices = response.json().get("Invoices", [])
        
        if contact_name:
            invoices = [i for i in invoices if contact_name.lower() in i.get("Contact", {}).get("Name", "").lower()]
        
        invoices = invoices[:limit]
        
        if not invoices:
            return "No invoices found."
        
        results = []
        for inv in invoices:
            contact = inv.get("Contact", {}).get("Name", "Unknown")
            inv_num = inv.get("InvoiceNumber", "N/A")
            status_val = inv.get("Status", "N/A")
            total = inv.get("Total", 0)
            due = inv.get("AmountDue", 0)
            date_str = inv.get("DateString", "")[:10]
            
            results.append(f"**{inv_num}** - {contact}\n  Status: {status_val} | Total: ${total:,.2f} | Due: ${due:,.2f} | Date: {date_str}")
        
        return f"Found {len(results)} invoice(s):\n\n" + "\n\n".join(results)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return "Error: Xero authentication expired. Run xero_auth_start to reconnect."
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_invoice(invoice_id: str = Field(..., description="Invoice ID (GUID)")) -> str:
    """Get full invoice details including line items."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."
    
    try:
        token = await xero_config.get_access_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.xero.com/api.xro/2.0/Invoices/{invoice_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            inv = response.json().get("Invoices", [{}])[0]
        
        lines = []
        for item in inv.get("LineItems", []):
            desc = item.get("Description", "No description")
            qty = item.get("Quantity", 0)
            amount = item.get("LineAmount", 0)
            lines.append(f"- {desc} (Qty: {qty}) - ${amount:,.2f}")
        
        return f"""# Invoice {inv.get('InvoiceNumber', 'N/A')}

**Contact:** {inv.get('Contact', {}).get('Name', 'Unknown')}
**Status:** {inv.get('Status', 'N/A')}
**Date:** {inv.get('DateString', '')[:10]}
**Due Date:** {inv.get('DueDateString', '')[:10]}
**Reference:** {inv.get('Reference', 'N/A')}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Subtotal:** ${inv.get('SubTotal', 0):,.2f}
**Tax:** ${inv.get('TotalTax', 0):,.2f}
**Total:** ${inv.get('Total', 0):,.2f}
**Amount Due:** ${inv.get('AmountDue', 0):,.2f}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_create_invoice(
    contact_name: str = Field(..., description="Contact/customer name (must exist in Xero)"),
    line_items: str = Field(..., description='JSON array of line items: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]'),
    reference: Optional[str] = Field(None, description="Invoice reference"),
    due_days: int = Field(30, description="Days until due"),
    status: str = Field("DRAFT", description="Status: 'DRAFT' or 'AUTHORISED'")
) -> str:
    """Create a new Xero invoice."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."
    
    try:
        token = await xero_config.get_access_token()
        items = json.loads(line_items)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Contacts",
                params={"where": f'Name.Contains("{contact_name}")'},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            contacts = response.json().get("Contacts", [])
        
        if not contacts:
            return f"Error: Contact '{contact_name}' not found in Xero."
        
        contact_id = contacts[0]["ContactID"]
        
        invoice_data = {
            "Type": "ACCREC",
            "Contact": {"ContactID": contact_id},
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "DueDate": (datetime.now() + timedelta(days=due_days)).strftime("%Y-%m-%d"),
            "LineItems": [
                {
                    "Description": item.get("description", ""),
                    "Quantity": item.get("quantity", 1),
                    "UnitAmount": item.get("unit_amount", 0),
                    "AccountCode": item.get("account_code", "200")
                }
                for item in items
            ],
            "Status": status.upper()
        }
        
        if reference:
            invoice_data["Reference"] = reference
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.xero.com/api.xro/2.0/Invoices",
                json={"Invoices": [invoice_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            created = response.json().get("Invoices", [{}])[0]
        
        return f"✅ Invoice created: **{created.get('InvoiceNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
    except json.JSONDecodeError:
        return "Error: Invalid JSON in line_items."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_update_invoice(
    invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number (e.g., INV-6476)"),
    reference: Optional[str] = Field(None, description="Update invoice reference"),
    status: Optional[str] = Field(None, description="Update status: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'VOIDED'"),
    due_date: Optional[str] = Field(None, description="Update due date (YYYY-MM-DD format)")
) -> str:
    """Update an existing Xero invoice (reference, status, or due date)."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        # Resolve invoice number to GUID if needed
        invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)

        update_data = {"InvoiceID": invoice_guid}

        if reference is not None:
            update_data["Reference"] = reference
        if status:
            update_data["Status"] = status.upper()
        if due_date:
            update_data["DueDate"] = due_date

        if len(update_data) == 1:
            return "Error: No updates specified. Provide reference, status, or due_date."

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.xero.com/api.xro/2.0/Invoices",
                json={"Invoices": [update_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            updated = response.json().get("Invoices", [{}])[0]

        return f"✅ Invoice **{updated.get('InvoiceNumber', invoice_id)}** updated."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_contacts(
    search: Optional[str] = Field(None, description="Search by name"),
    is_customer: bool = Field(True, description="Filter to customers only"),
    limit: int = Field(20, description="Max results")
) -> str:
    """List Xero contacts/customers."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."
    
    try:
        token = await xero_config.get_access_token()
        
        params = {"order": "Name"}
        where_parts = []
        if search:
            where_parts.append(f'Name.Contains("{search}")')
        if is_customer:
            where_parts.append("IsCustomer==true")
        if where_parts:
            params["where"] = " AND ".join(where_parts)
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Contacts",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            contacts = response.json().get("Contacts", [])[:limit]
        
        if not contacts:
            return "No contacts found."
        
        results = []
        for c in contacts:
            name = c.get("Name", "Unknown")
            email = c.get("EmailAddress", "N/A")
            balance = c.get("Balances", {}).get("AccountsReceivable", {}).get("Outstanding", 0)
            results.append(f"- **{name}** ({email}) - Outstanding: ${balance:,.2f}")
        
        return "## Contacts\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_aged_receivables(
    contact_name: Optional[str] = Field(None, description="Filter by contact name"),
    min_amount: float = Field(0, description="Minimum amount outstanding")
) -> str:
    """Get aged receivables report - who owes money and for how long."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."
    
    try:
        token = await xero_config.get_access_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Reports/AgedReceivablesByContact",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            report = response.json().get("Reports", [{}])[0]
        
        rows = report.get("Rows", [])
        results = []
        
        for section in rows:
            if section.get("RowType") == "Section":
                for row in section.get("Rows", []):
                    if row.get("RowType") == "Row":
                        cells = row.get("Cells", [])
                        if len(cells) >= 6:
                            name = cells[0].get("Value", "")
                            total = float(cells[5].get("Value", 0) or 0)
                            
                            if contact_name and contact_name.lower() not in name.lower():
                                continue
                            if total < min_amount:
                                continue
                            
                            current = float(cells[1].get("Value", 0) or 0)
                            days_30 = float(cells[2].get("Value", 0) or 0)
                            days_60 = float(cells[3].get("Value", 0) or 0)
                            days_90 = float(cells[4].get("Value", 0) or 0)
                            
                            results.append(f"**{name}**\n  Current: ${current:,.2f} | 30d: ${days_30:,.2f} | 60d: ${days_60:,.2f} | 90d+: ${days_90:,.2f} | **Total: ${total:,.2f}**")
        
        if not results:
            return "No outstanding receivables found."
        
        return "## Aged Receivables\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_auth_start() -> str:
    """Get authorization URL to connect Xero. Use this if Xero is not connected."""
    client_id = os.getenv("XERO_CLIENT_ID", "")
    if not client_id:
        return "Error: XERO_CLIENT_ID not configured in secrets."
    
    redirect_uri = f"{CLOUD_RUN_URL}/callback"
    
    auth_url = (
        f"https://login.xero.com/identity/connect/authorize"
        f"?response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=offline_access accounting.transactions accounting.contacts accounting.reports.read accounting.settings.read"
        f"&state=crowdit"
    )
    
    return f"""## Xero Authorization Required

**Click this link to authorize:**
{auth_url}

After authorizing, you'll be redirected back automatically and Xero will be connected.

If you see an error page, make sure the redirect URI in your Xero app settings is set to:
`{redirect_uri}`"""


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_auth_complete(
    auth_code: str = Field(..., description="Authorization code from callback URL")
) -> str:
    """Complete Xero authorization with the code from callback URL."""
    client_id = os.getenv("XERO_CLIENT_ID", "")
    client_secret = os.getenv("XERO_CLIENT_SECRET", "")
    
    if not client_id or not client_secret:
        return "Error: Xero credentials not configured."
    
    redirect_uri = f"{CLOUD_RUN_URL}/callback"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://identity.xero.com/connect/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": auth_code,
                    "redirect_uri": redirect_uri
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            tokens = response.json()
            
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]
            
            tenant_response = await client.get(
                "https://api.xero.com/connections",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            tenant_response.raise_for_status()
            connections = tenant_response.json()
            
            if not connections:
                return "Error: No Xero organizations found."
            
            tenant_id = connections[0]["tenantId"]
            org_name = connections[0].get("tenantName", "Unknown")
        
        xero_config._access_token = access_token
        xero_config._refresh_token = refresh_token
        xero_config.tenant_id = tenant_id
        xero_config._token_expiry = datetime.now() + timedelta(seconds=1740)
        
        saved_refresh = update_secret_sync("XERO_REFRESH_TOKEN", refresh_token)
        saved_tenant = update_secret_sync("XERO_TENANT_ID", tenant_id)
        
        if saved_refresh and saved_tenant:
            return f"""✅ Xero connected successfully!

**Organization:** {org_name}
**Tenant ID:** {tenant_id}

Tokens have been automatically saved to Secret Manager."""
        else:
            return f"""✅ Xero connected for this session!

**Organization:** {org_name}

⚠️ To persist, run:
```bash
echo -n "{refresh_token}" | gcloud secrets versions add XERO_REFRESH_TOKEN --data-file=- --project=crowdmcp
echo -n "{tenant_id}" | gcloud secrets versions add XERO_TENANT_ID --data-file=- --project=crowdmcp
```"""

    except httpx.HTTPStatusError as e:
        return f"Error: {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Xero Extended Functions - Bills, Payments, Credit Notes
# ============================================================================

@mcp.tool(annotations={"readOnlyHint": False})
async def xero_update_invoice_lines(
    invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number (e.g., INV-6476) - must be DRAFT status"),
    line_items: str = Field(..., description='JSON array of line items: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]')
) -> str:
    """Replace all line items on a DRAFT invoice. Only works on DRAFT invoices."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        # Resolve invoice number to GUID if needed
        invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)

        items = json.loads(line_items)

        update_data = {
            "InvoiceID": invoice_guid,
            "LineItems": [
                {
                    "Description": item.get("description", ""),
                    "Quantity": item.get("quantity", 1),
                    "UnitAmount": item.get("unit_amount", 0),
                    "AccountCode": item.get("account_code", "200")
                }
                for item in items
            ]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.xero.com/api.xro/2.0/Invoices",
                json={"Invoices": [update_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            updated = response.json().get("Invoices", [{}])[0]

        return f"✅ Invoice **{updated.get('InvoiceNumber', invoice_id)}** line items updated. New total: ${updated.get('Total', 0):,.2f}"
    except json.JSONDecodeError:
        return "Error: Invalid JSON in line_items."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_bills(
    status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'PAID'"),
    contact_name: Optional[str] = Field(None, description="Filter by supplier name"),
    days: int = Field(90, description="Bills from last N days"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Get supplier bills (accounts payable invoices)."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        where_parts = ['Type=="ACCPAY"']
        if status:
            where_parts.append(f'Status=="{status.upper()}"')

        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where_parts.append(f'Date>=DateTime({since_date.replace("-", ",")})')

        params = {"where": " AND ".join(where_parts), "order": "Date DESC"}

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Invoices",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            bills = response.json().get("Invoices", [])

        if contact_name:
            bills = [b for b in bills if contact_name.lower() in b.get("Contact", {}).get("Name", "").lower()]

        bills = bills[:limit]

        if not bills:
            return "No bills found."

        results = []
        for bill in bills:
            contact = bill.get("Contact", {}).get("Name", "Unknown")
            inv_num = bill.get("InvoiceNumber", "N/A")
            status_val = bill.get("Status", "N/A")
            total = bill.get("Total", 0)
            due = bill.get("AmountDue", 0)
            date_str = bill.get("DateString", "")[:10]

            results.append(f"**{inv_num}** - {contact}\n  Status: {status_val} | Total: ${total:,.2f} | Due: ${due:,.2f} | Date: {date_str}")

        return f"Found {len(results)} bill(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_create_bill(
    contact_name: str = Field(..., description="Supplier name (must exist in Xero)"),
    line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "400"}]'),
    invoice_number: Optional[str] = Field(None, description="Supplier's invoice number"),
    due_days: int = Field(30, description="Days until due"),
    status: str = Field("DRAFT", description="Status: 'DRAFT' or 'AUTHORISED'")
) -> str:
    """Create a supplier bill (accounts payable)."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()
        items = json.loads(line_items)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Contacts",
                params={"where": f'Name.Contains("{contact_name}")'},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            contacts = response.json().get("Contacts", [])

        if not contacts:
            return f"Error: Supplier '{contact_name}' not found."

        bill_data = {
            "Type": "ACCPAY",
            "Contact": {"ContactID": contacts[0]["ContactID"]},
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "DueDate": (datetime.now() + timedelta(days=due_days)).strftime("%Y-%m-%d"),
            "LineItems": [
                {
                    "Description": item.get("description", ""),
                    "Quantity": item.get("quantity", 1),
                    "UnitAmount": item.get("unit_amount", 0),
                    "AccountCode": item.get("account_code", "400")
                }
                for item in items
            ],
            "Status": status.upper()
        }

        if invoice_number:
            bill_data["InvoiceNumber"] = invoice_number

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.xero.com/api.xro/2.0/Invoices",
                json={"Invoices": [bill_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            created = response.json().get("Invoices", [{}])[0]

        return f"✅ Bill created: **{created.get('InvoiceNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
    except json.JSONDecodeError:
        return "Error: Invalid JSON in line_items."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_payments(
    invoice_id: Optional[str] = Field(None, description="Filter by invoice ID"),
    days: int = Field(90, description="Payments from last N days"),
    limit: int = Field(50, description="Max results")
) -> str:
    """Get payment records."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        params = {
            "where": f'Date>=DateTime({since_date.replace("-", ",")})',
            "order": "Date DESC"
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Payments",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            payments = response.json().get("Payments", [])

        if invoice_id:
            payments = [p for p in payments if p.get("Invoice", {}).get("InvoiceID") == invoice_id]

        payments = payments[:limit]

        if not payments:
            return "No payments found."

        results = []
        for p in payments:
            inv = p.get("Invoice", {})
            inv_num = inv.get("InvoiceNumber", "N/A")
            contact = inv.get("Contact", {}).get("Name", "Unknown")
            amount = p.get("Amount", 0)
            date_str = p.get("Date", "")[:10]
            status_val = p.get("Status", "N/A")
            payment_type = p.get("PaymentType", "N/A")

            results.append(f"**${amount:,.2f}** - {inv_num} ({contact})\n  Date: {date_str} | Type: {payment_type} | Status: {status_val}")

        return f"Found {len(results)} payment(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_create_payment(
    invoice_id: str = Field(..., description="Invoice ID (GUID) to pay"),
    amount: float = Field(..., description="Payment amount"),
    account_code: str = Field(..., description="Bank account code (e.g., '090' for checking)"),
    date: Optional[str] = Field(None, description="Payment date (YYYY-MM-DD), defaults to today"),
    reference: Optional[str] = Field(None, description="Payment reference")
) -> str:
    """Record a payment against an invoice."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        # Get the account ID for the bank account
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Accounts",
                params={"where": f'Code=="{account_code}"'},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            accounts = response.json().get("Accounts", [])

        if not accounts:
            return f"Error: Account with code '{account_code}' not found."

        payment_data = {
            "Invoice": {"InvoiceID": invoice_id},
            "Account": {"AccountID": accounts[0]["AccountID"]},
            "Amount": amount,
            "Date": date or datetime.now().strftime("%Y-%m-%d")
        }

        if reference:
            payment_data["Reference"] = reference

        async with httpx.AsyncClient() as client:
            response = await client.put(
                "https://api.xero.com/api.xro/2.0/Payments",
                json={"Payments": [payment_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            created = response.json().get("Payments", [{}])[0]

        return f"✅ Payment of ${amount:,.2f} recorded against invoice."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_credit_notes(
    status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'PAID'"),
    contact_name: Optional[str] = Field(None, description="Filter by contact name"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Get credit notes."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        params = {"order": "Date DESC"}
        if status:
            params["where"] = f'Status=="{status.upper()}"'

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/CreditNotes",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            credit_notes = response.json().get("CreditNotes", [])

        if contact_name:
            credit_notes = [cn for cn in credit_notes if contact_name.lower() in cn.get("Contact", {}).get("Name", "").lower()]

        credit_notes = credit_notes[:limit]

        if not credit_notes:
            return "No credit notes found."

        results = []
        for cn in credit_notes:
            contact = cn.get("Contact", {}).get("Name", "Unknown")
            cn_num = cn.get("CreditNoteNumber", "N/A")
            status_val = cn.get("Status", "N/A")
            total = cn.get("Total", 0)
            remaining = cn.get("RemainingCredit", 0)
            date_str = cn.get("DateString", "")[:10]
            cn_type = cn.get("Type", "N/A")

            results.append(f"**{cn_num}** - {contact} ({cn_type})\n  Status: {status_val} | Total: ${total:,.2f} | Remaining: ${remaining:,.2f} | Date: {date_str}")

        return f"Found {len(results)} credit note(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_create_credit_note(
    contact_name: str = Field(..., description="Contact name"),
    line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]'),
    credit_note_type: str = Field("ACCRECCREDIT", description="Type: 'ACCRECCREDIT' (customer) or 'ACCPAYCREDIT' (supplier)"),
    reference: Optional[str] = Field(None, description="Credit note reference"),
    status: str = Field("DRAFT", description="Status: 'DRAFT' or 'AUTHORISED'")
) -> str:
    """Create a credit note."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()
        items = json.loads(line_items)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Contacts",
                params={"where": f'Name.Contains("{contact_name}")'},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            contacts = response.json().get("Contacts", [])

        if not contacts:
            return f"Error: Contact '{contact_name}' not found."

        cn_data = {
            "Type": credit_note_type.upper(),
            "Contact": {"ContactID": contacts[0]["ContactID"]},
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "LineItems": [
                {
                    "Description": item.get("description", ""),
                    "Quantity": item.get("quantity", 1),
                    "UnitAmount": item.get("unit_amount", 0),
                    "AccountCode": item.get("account_code", "200")
                }
                for item in items
            ],
            "Status": status.upper()
        }

        if reference:
            cn_data["Reference"] = reference

        async with httpx.AsyncClient() as client:
            response = await client.put(
                "https://api.xero.com/api.xro/2.0/CreditNotes",
                json={"CreditNotes": [cn_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            created = response.json().get("CreditNotes", [{}])[0]

        return f"✅ Credit note created: **{created.get('CreditNoteNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
    except json.JSONDecodeError:
        return "Error: Invalid JSON in line_items."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_void_invoice(
    invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number (e.g., INV-6476) to void")
) -> str:
    """Void an invoice. Only works on AUTHORISED invoices with no payments."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        # Resolve invoice number to GUID if needed
        invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.xero.com/api.xro/2.0/Invoices",
                json={"Invoices": [{"InvoiceID": invoice_guid, "Status": "VOIDED"}]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            updated = response.json().get("Invoices", [{}])[0]

        return f"✅ Invoice **{updated.get('InvoiceNumber', invoice_id)}** has been voided."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_email_invoice(
    invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number (e.g., INV-6476) to email")
) -> str:
    """Email an invoice to the contact. Invoice must be AUTHORISED."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        # Resolve invoice number to GUID if needed
        invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.xero.com/api.xro/2.0/Invoices/{invoice_guid}/Email",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()

        return f"✅ Invoice {invoice_id} emailed successfully."
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Xero Extended Functions - Quotes & Purchase Orders
# ============================================================================

@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_quotes(
    status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SENT', 'ACCEPTED', 'DECLINED'"),
    contact_name: Optional[str] = Field(None, description="Filter by contact name"),
    days: int = Field(90, description="Quotes from last N days"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Get quotes/proposals."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        where_parts = []
        if status:
            where_parts.append(f'Status=="{status.upper()}"')

        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where_parts.append(f'Date>=DateTime({since_date.replace("-", ",")})')

        params = {"order": "Date DESC"}
        if where_parts:
            params["where"] = " AND ".join(where_parts)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Quotes",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            quotes = response.json().get("Quotes", [])

        if contact_name:
            quotes = [q for q in quotes if contact_name.lower() in q.get("Contact", {}).get("Name", "").lower()]

        quotes = quotes[:limit]

        if not quotes:
            return "No quotes found."

        results = []
        for q in quotes:
            contact = q.get("Contact", {}).get("Name", "Unknown")
            quote_num = q.get("QuoteNumber", "N/A")
            status_val = q.get("Status", "N/A")
            total = q.get("Total", 0)
            date_str = q.get("DateString", "")[:10]
            title = q.get("Title", "")

            results.append(f"**{quote_num}** - {contact}\n  {title}\n  Status: {status_val} | Total: ${total:,.2f} | Date: {date_str}")

        return f"Found {len(results)} quote(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_create_quote(
    contact_name: str = Field(..., description="Contact name"),
    line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]'),
    title: Optional[str] = Field(None, description="Quote title"),
    summary: Optional[str] = Field(None, description="Quote summary"),
    expiry_days: int = Field(30, description="Days until expiry"),
    status: str = Field("DRAFT", description="Status: 'DRAFT' or 'SENT'")
) -> str:
    """Create a quote/proposal."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()
        items = json.loads(line_items)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Contacts",
                params={"where": f'Name.Contains("{contact_name}")'},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            contacts = response.json().get("Contacts", [])

        if not contacts:
            return f"Error: Contact '{contact_name}' not found."

        quote_data = {
            "Contact": {"ContactID": contacts[0]["ContactID"]},
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "ExpiryDate": (datetime.now() + timedelta(days=expiry_days)).strftime("%Y-%m-%d"),
            "LineItems": [
                {
                    "Description": item.get("description", ""),
                    "Quantity": item.get("quantity", 1),
                    "UnitAmount": item.get("unit_amount", 0),
                    "AccountCode": item.get("account_code", "200")
                }
                for item in items
            ],
            "Status": status.upper()
        }

        if title:
            quote_data["Title"] = title
        if summary:
            quote_data["Summary"] = summary

        async with httpx.AsyncClient() as client:
            response = await client.put(
                "https://api.xero.com/api.xro/2.0/Quotes",
                json={"Quotes": [quote_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            created = response.json().get("Quotes", [{}])[0]

        return f"✅ Quote created: **{created.get('QuoteNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
    except json.JSONDecodeError:
        return "Error: Invalid JSON in line_items."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_purchase_orders(
    status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'BILLED'"),
    contact_name: Optional[str] = Field(None, description="Filter by supplier name"),
    days: int = Field(90, description="POs from last N days"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Get purchase orders."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        where_parts = []
        if status:
            where_parts.append(f'Status=="{status.upper()}"')

        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where_parts.append(f'Date>=DateTime({since_date.replace("-", ",")})')

        params = {"order": "Date DESC"}
        if where_parts:
            params["where"] = " AND ".join(where_parts)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/PurchaseOrders",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            pos = response.json().get("PurchaseOrders", [])

        if contact_name:
            pos = [po for po in pos if contact_name.lower() in po.get("Contact", {}).get("Name", "").lower()]

        pos = pos[:limit]

        if not pos:
            return "No purchase orders found."

        results = []
        for po in pos:
            contact = po.get("Contact", {}).get("Name", "Unknown")
            po_num = po.get("PurchaseOrderNumber", "N/A")
            status_val = po.get("Status", "N/A")
            total = po.get("Total", 0)
            date_str = po.get("DateString", "")[:10]

            results.append(f"**{po_num}** - {contact}\n  Status: {status_val} | Total: ${total:,.2f} | Date: {date_str}")

        return f"Found {len(results)} purchase order(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_create_purchase_order(
    contact_name: str = Field(..., description="Supplier name"),
    line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "400"}]'),
    delivery_date: Optional[str] = Field(None, description="Expected delivery date (YYYY-MM-DD)"),
    reference: Optional[str] = Field(None, description="PO reference"),
    status: str = Field("DRAFT", description="Status: 'DRAFT' or 'SUBMITTED'")
) -> str:
    """Create a purchase order."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()
        items = json.loads(line_items)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Contacts",
                params={"where": f'Name.Contains("{contact_name}")'},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            contacts = response.json().get("Contacts", [])

        if not contacts:
            return f"Error: Supplier '{contact_name}' not found."

        po_data = {
            "Contact": {"ContactID": contacts[0]["ContactID"]},
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "LineItems": [
                {
                    "Description": item.get("description", ""),
                    "Quantity": item.get("quantity", 1),
                    "UnitAmount": item.get("unit_amount", 0),
                    "AccountCode": item.get("account_code", "400")
                }
                for item in items
            ],
            "Status": status.upper()
        }

        if delivery_date:
            po_data["DeliveryDate"] = delivery_date
        if reference:
            po_data["Reference"] = reference

        async with httpx.AsyncClient() as client:
            response = await client.put(
                "https://api.xero.com/api.xro/2.0/PurchaseOrders",
                json={"PurchaseOrders": [po_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            created = response.json().get("PurchaseOrders", [{}])[0]

        return f"✅ Purchase Order created: **{created.get('PurchaseOrderNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
    except json.JSONDecodeError:
        return "Error: Invalid JSON in line_items."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_bank_transactions(
    bank_account_code: Optional[str] = Field(None, description="Filter by bank account code"),
    transaction_type: Optional[str] = Field(None, description="Filter: 'SPEND' or 'RECEIVE'"),
    days: int = Field(30, description="Transactions from last N days"),
    limit: int = Field(50, description="Max results")
) -> str:
    """Get bank transactions (spend/receive money)."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        where_parts = []
        if transaction_type:
            where_parts.append(f'Type=="{transaction_type.upper()}"')

        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where_parts.append(f'Date>=DateTime({since_date.replace("-", ",")})')

        params = {"order": "Date DESC"}
        if where_parts:
            params["where"] = " AND ".join(where_parts)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/BankTransactions",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            transactions = response.json().get("BankTransactions", [])

        if bank_account_code:
            transactions = [t for t in transactions if t.get("BankAccount", {}).get("Code") == bank_account_code]

        transactions = transactions[:limit]

        if not transactions:
            return "No bank transactions found."

        results = []
        for t in transactions:
            contact = t.get("Contact", {}).get("Name", "Unknown")
            tx_type = t.get("Type", "N/A")
            total = t.get("Total", 0)
            date_str = t.get("DateString", "")[:10]
            reference = t.get("Reference", "")
            bank = t.get("BankAccount", {}).get("Name", "N/A")

            results.append(f"**{tx_type}** ${total:,.2f} - {contact}\n  Bank: {bank} | Date: {date_str} | Ref: {reference or 'N/A'}")

        return f"Found {len(results)} transaction(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_create_contact(
    name: str = Field(..., description="Contact/company name"),
    email: Optional[str] = Field(None, description="Email address"),
    first_name: Optional[str] = Field(None, description="First name"),
    last_name: Optional[str] = Field(None, description="Last name"),
    phone: Optional[str] = Field(None, description="Phone number"),
    is_customer: bool = Field(True, description="Is a customer"),
    is_supplier: bool = Field(False, description="Is a supplier")
) -> str:
    """Create a new contact."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        contact_data = {
            "Name": name,
            "IsCustomer": is_customer,
            "IsSupplier": is_supplier
        }

        if email:
            contact_data["EmailAddress"] = email
        if first_name:
            contact_data["FirstName"] = first_name
        if last_name:
            contact_data["LastName"] = last_name
        if phone:
            contact_data["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.xero.com/api.xro/2.0/Contacts",
                json={"Contacts": [contact_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            created = response.json().get("Contacts", [{}])[0]

        return f"✅ Contact created: **{created.get('Name', name)}** (ID: {created.get('ContactID', 'N/A')})"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def xero_update_contact(
    contact_id: str = Field(..., description="Contact ID (GUID)"),
    name: Optional[str] = Field(None, description="Update name"),
    email: Optional[str] = Field(None, description="Update email"),
    phone: Optional[str] = Field(None, description="Update phone"),
    is_customer: Optional[bool] = Field(None, description="Set as customer"),
    is_supplier: Optional[bool] = Field(None, description="Set as supplier")
) -> str:
    """Update an existing contact."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        contact_data = {"ContactID": contact_id}

        if name:
            contact_data["Name"] = name
        if email:
            contact_data["EmailAddress"] = email
        if phone:
            contact_data["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
        if is_customer is not None:
            contact_data["IsCustomer"] = is_customer
        if is_supplier is not None:
            contact_data["IsSupplier"] = is_supplier

        if len(contact_data) == 1:
            return "Error: No updates specified."

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.xero.com/api.xro/2.0/Contacts",
                json={"Contacts": [contact_data]},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            updated = response.json().get("Contacts", [{}])[0]

        return f"✅ Contact **{updated.get('Name', contact_id)}** updated."
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Xero Extended Functions - Reports & Reference Data
# ============================================================================

@mcp.tool(annotations={"readOnlyHint": True})
async def xero_profit_loss(
    from_date: Optional[str] = Field(None, description="Start date (YYYY-MM-DD), defaults to start of current financial year"),
    to_date: Optional[str] = Field(None, description="End date (YYYY-MM-DD), defaults to today")
) -> str:
    """Get Profit & Loss report."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        params = {}
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Reports/ProfitAndLoss",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            report = response.json().get("Reports", [{}])[0]

        lines = [f"# Profit & Loss Report", f"**Period:** {report.get('ReportDate', 'N/A')}\n"]

        for row in report.get("Rows", []):
            row_type = row.get("RowType")

            if row_type == "Header":
                continue
            elif row_type == "Section":
                title = row.get("Title", "")
                if title:
                    lines.append(f"\n## {title}")
                for sub_row in row.get("Rows", []):
                    if sub_row.get("RowType") == "Row":
                        cells = sub_row.get("Cells", [])
                        if len(cells) >= 2:
                            account = cells[0].get("Value", "")
                            amount = cells[1].get("Value", "0")
                            try:
                                amount_val = float(amount)
                                lines.append(f"- {account}: ${amount_val:,.2f}")
                            except (ValueError, TypeError):
                                lines.append(f"- {account}: {amount}")
                    elif sub_row.get("RowType") == "SummaryRow":
                        cells = sub_row.get("Cells", [])
                        if len(cells) >= 2:
                            label = cells[0].get("Value", "Total")
                            amount = cells[1].get("Value", "0")
                            try:
                                amount_val = float(amount)
                                lines.append(f"**{label}: ${amount_val:,.2f}**")
                            except (ValueError, TypeError):
                                lines.append(f"**{label}: {amount}**")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_balance_sheet(
    date: Optional[str] = Field(None, description="Report date (YYYY-MM-DD), defaults to today")
) -> str:
    """Get Balance Sheet report."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        params = {}
        if date:
            params["date"] = date

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Reports/BalanceSheet",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            report = response.json().get("Reports", [{}])[0]

        lines = [f"# Balance Sheet", f"**As at:** {report.get('ReportDate', 'N/A')}\n"]

        for row in report.get("Rows", []):
            row_type = row.get("RowType")

            if row_type == "Section":
                title = row.get("Title", "")
                if title:
                    lines.append(f"\n## {title}")
                for sub_row in row.get("Rows", []):
                    if sub_row.get("RowType") == "Row":
                        cells = sub_row.get("Cells", [])
                        if len(cells) >= 2:
                            account = cells[0].get("Value", "")
                            amount = cells[1].get("Value", "0")
                            try:
                                amount_val = float(amount)
                                lines.append(f"- {account}: ${amount_val:,.2f}")
                            except (ValueError, TypeError):
                                lines.append(f"- {account}: {amount}")
                    elif sub_row.get("RowType") == "SummaryRow":
                        cells = sub_row.get("Cells", [])
                        if len(cells) >= 2:
                            label = cells[0].get("Value", "Total")
                            amount = cells[1].get("Value", "0")
                            try:
                                amount_val = float(amount)
                                lines.append(f"**{label}: ${amount_val:,.2f}**")
                            except (ValueError, TypeError):
                                lines.append(f"**{label}: {amount}**")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_trial_balance(
    date: Optional[str] = Field(None, description="Report date (YYYY-MM-DD), defaults to today")
) -> str:
    """Get Trial Balance report."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        params = {}
        if date:
            params["date"] = date

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Reports/TrialBalance",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            report = response.json().get("Reports", [{}])[0]

        lines = [f"# Trial Balance", f"**As at:** {report.get('ReportDate', 'N/A')}\n"]
        lines.append("| Account | Debit | Credit |")
        lines.append("|---------|-------|--------|")

        for row in report.get("Rows", []):
            if row.get("RowType") == "Section":
                for sub_row in row.get("Rows", []):
                    if sub_row.get("RowType") == "Row":
                        cells = sub_row.get("Cells", [])
                        if len(cells) >= 3:
                            account = cells[0].get("Value", "")
                            debit = cells[1].get("Value", "")
                            credit = cells[2].get("Value", "")
                            lines.append(f"| {account} | {debit} | {credit} |")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_bank_summary() -> str:
    """Get bank accounts summary with balances."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Reports/BankSummary",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            report = response.json().get("Reports", [{}])[0]

        lines = [f"# Bank Summary", f"**As at:** {report.get('ReportDate', 'N/A')}\n"]

        for row in report.get("Rows", []):
            if row.get("RowType") == "Section":
                for sub_row in row.get("Rows", []):
                    if sub_row.get("RowType") == "Row":
                        cells = sub_row.get("Cells", [])
                        if len(cells) >= 2:
                            account = cells[0].get("Value", "")
                            balance = cells[1].get("Value", "0")
                            try:
                                balance_val = float(balance)
                                lines.append(f"- **{account}**: ${balance_val:,.2f}")
                            except (ValueError, TypeError):
                                lines.append(f"- **{account}**: {balance}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_aged_payables(
    contact_name: Optional[str] = Field(None, description="Filter by supplier name"),
    min_amount: float = Field(0, description="Minimum amount owed")
) -> str:
    """Get aged payables report - what you owe to suppliers."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Reports/AgedPayablesByContact",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            report = response.json().get("Reports", [{}])[0]

        rows = report.get("Rows", [])
        results = []

        for section in rows:
            if section.get("RowType") == "Section":
                for row in section.get("Rows", []):
                    if row.get("RowType") == "Row":
                        cells = row.get("Cells", [])
                        if len(cells) >= 6:
                            name = cells[0].get("Value", "")
                            total = float(cells[5].get("Value", 0) or 0)

                            if contact_name and contact_name.lower() not in name.lower():
                                continue
                            if total < min_amount:
                                continue

                            current = float(cells[1].get("Value", 0) or 0)
                            days_30 = float(cells[2].get("Value", 0) or 0)
                            days_60 = float(cells[3].get("Value", 0) or 0)
                            days_90 = float(cells[4].get("Value", 0) or 0)

                            results.append(f"**{name}**\n  Current: ${current:,.2f} | 30d: ${days_30:,.2f} | 60d: ${days_60:,.2f} | 90d+: ${days_90:,.2f} | **Total: ${total:,.2f}**")

        if not results:
            return "No outstanding payables found."

        return "## Aged Payables\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_accounts(
    account_type: Optional[str] = Field(None, description="Filter by type: 'BANK', 'REVENUE', 'EXPENSE', 'ASSET', 'LIABILITY', etc."),
    account_class: Optional[str] = Field(None, description="Filter by class: 'ASSET', 'EQUITY', 'EXPENSE', 'LIABILITY', 'REVENUE'")
) -> str:
    """Get chart of accounts."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        params = {}
        where_parts = []
        if account_type:
            where_parts.append(f'Type=="{account_type.upper()}"')
        if account_class:
            where_parts.append(f'Class=="{account_class.upper()}"')
        if where_parts:
            params["where"] = " AND ".join(where_parts)

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Accounts",
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            accounts = response.json().get("Accounts", [])

        if not accounts:
            return "No accounts found."

        # Group by class
        by_class = {}
        for acc in accounts:
            acc_class = acc.get("Class", "Other")
            if acc_class not in by_class:
                by_class[acc_class] = []
            by_class[acc_class].append(acc)

        lines = ["# Chart of Accounts\n"]
        for acc_class, accs in sorted(by_class.items()):
            lines.append(f"\n## {acc_class}")
            for acc in sorted(accs, key=lambda x: x.get("Code", "")):
                code = acc.get("Code", "N/A")
                name = acc.get("Name", "Unknown")
                acc_type = acc.get("Type", "N/A")
                lines.append(f"- **{code}** - {name} ({acc_type})")

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_items(
    search: Optional[str] = Field(None, description="Search by code or name"),
    limit: int = Field(50, description="Max results")
) -> str:
    """Get product/service items."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/Items",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            items = response.json().get("Items", [])

        if search:
            search_lower = search.lower()
            items = [i for i in items if search_lower in i.get("Code", "").lower() or search_lower in i.get("Name", "").lower()]

        items = items[:limit]

        if not items:
            return "No items found."

        results = []
        for item in items:
            code = item.get("Code", "N/A")
            name = item.get("Name", "Unknown")
            desc = item.get("Description", "")[:50]
            sell_price = item.get("SalesDetails", {}).get("UnitPrice", 0)
            buy_price = item.get("PurchaseDetails", {}).get("UnitPrice", 0)

            results.append(f"**{code}** - {name}\n  {desc}\n  Sell: ${sell_price:,.2f} | Buy: ${buy_price:,.2f}")

        return f"Found {len(results)} item(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def xero_get_tax_rates() -> str:
    """Get available tax rates."""
    if not xero_config.is_configured:
        return "Error: Xero not configured."

    try:
        token = await xero_config.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.xero.com/api.xro/2.0/TaxRates",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": xero_config.tenant_id,
                    "Accept": "application/json"
                }
            )
            response.raise_for_status()
            tax_rates = response.json().get("TaxRates", [])

        if not tax_rates:
            return "No tax rates found."

        results = []
        for tr in tax_rates:
            name = tr.get("Name", "Unknown")
            tax_type = tr.get("TaxType", "N/A")
            rate = tr.get("EffectiveRate", tr.get("DisplayTaxRate", 0))
            status = tr.get("Status", "N/A")

            if status == "ACTIVE":
                results.append(f"- **{name}** ({tax_type}): {rate}%")

        return "## Tax Rates\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Front Integration
# ============================================================================

class FrontConfig:
    def __init__(self):
        self.api_key = os.getenv("FRONT_API_KEY", "")
        self.base_url = "https://api2.frontapp.com"
    
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)
    
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

front_config = FrontConfig()

@mcp.tool(annotations={"readOnlyHint": True})
async def front_list_inboxes() -> str:
    """List all Front inboxes (email, WhatsApp, etc.)."""
    if not front_config.is_configured:
        return "Error: Front not configured (missing FRONT_API_KEY)."
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{front_config.base_url}/inboxes", headers=front_config.headers())
            response.raise_for_status()
            inboxes = response.json().get("_results", [])
        if not inboxes:
            return "No inboxes found."
        results = [f"- **{i.get('name', 'Unknown')}** ({i.get('type', 'N/A')}) - ID: `{i.get('id', 'N/A')}`" for i in inboxes]
        return "## Front Inboxes\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": True})
async def front_list_conversations(
    status: str = Field("open", description="Filter: 'open', 'archived', 'deleted', 'spam'"),
    inbox_id: Optional[str] = Field(None, description="Filter by inbox ID"),
    limit: int = Field(20, description="Max results (1-100)")
) -> str:
    """List recent Front conversations."""
    if not front_config.is_configured:
        return "Error: Front not configured."
    try:
        params = {"limit": min(max(1, limit), 100)}
        if status:
            params["q[statuses][]"] = status
        url = f"{front_config.base_url}/inboxes/{inbox_id}/conversations" if inbox_id else f"{front_config.base_url}/conversations"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=front_config.headers())
            response.raise_for_status()
            conversations = response.json().get("_results", [])
        if not conversations:
            return "No conversations found."
        results = [f"**{c.get('subject', 'No subject')[:50]}**\n  From: {c.get('recipient', {}).get('handle', 'Unknown')} | Status: {c.get('status', 'N/A')} | ID: `{c.get('id', 'N/A')}`" for c in conversations]
        return f"Found {len(results)} conversation(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": True})
async def front_get_conversation(conversation_id: str = Field(..., description="Conversation ID")) -> str:
    """Get full conversation details with messages."""
    if not front_config.is_configured:
        return "Error: Front not configured."
    try:
        async with httpx.AsyncClient() as client:
            conv_response = await client.get(f"{front_config.base_url}/conversations/{conversation_id}", headers=front_config.headers())
            conv_response.raise_for_status()
            conv = conv_response.json()
            msg_response = await client.get(f"{front_config.base_url}/conversations/{conversation_id}/messages", headers=front_config.headers())
            msg_response.raise_for_status()
            messages = msg_response.json().get("_results", [])
        msg_text = [f"**{m.get('author', {}).get('email', 'Unknown')}** ({m.get('created_at', '')[:19]}):\n{m.get('text', m.get('body', ''))[:500]}" for m in messages[:10]]
        return f"# {conv.get('subject', 'No subject')}\n\n**Recipient:** {conv.get('recipient', {}).get('handle', 'Unknown')}\n**Status:** {conv.get('status', 'N/A')}\n\n## Messages\n\n{chr(10).join(msg_text) if msg_text else 'No messages'}"
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": True})
async def front_search_conversations(query: str = Field(..., description="Search query"), limit: int = Field(20, description="Max results")) -> str:
    """Search Front conversations."""
    if not front_config.is_configured:
        return "Error: Front not configured."
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{front_config.base_url}/conversations/search/{query}", params={"limit": min(limit, 100)}, headers=front_config.headers())
            response.raise_for_status()
            conversations = response.json().get("_results", [])
        if not conversations:
            return f"No conversations found for '{query}'."
        results = [f"**{c.get('subject', 'No subject')[:50]}**\n  From: {c.get('recipient', {}).get('handle', 'Unknown')} | ID: `{c.get('id', 'N/A')}`" for c in conversations]
        return f"Found {len(results)} conversation(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": False})
async def front_add_tag(conversation_id: str = Field(..., description="Conversation ID"), tag_name: str = Field(..., description="Tag name")) -> str:
    """Add a tag to a Front conversation."""
    if not front_config.is_configured:
        return "Error: Front not configured."
    try:
        async with httpx.AsyncClient() as client:
            tags_response = await client.get(f"{front_config.base_url}/tags", headers=front_config.headers())
            tags_response.raise_for_status()
            tags = tags_response.json().get("_results", [])
            tag_id = next((t.get("id") for t in tags if t.get("name", "").lower() == tag_name.lower()), None)
            if not tag_id:
                return f"Error: Tag '{tag_name}' not found."
            response = await client.post(f"{front_config.base_url}/conversations/{conversation_id}/tags", json={"tag_ids": [tag_id]}, headers=front_config.headers())
            response.raise_for_status()
        return f"✅ Tag '{tag_name}' added."
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool(annotations={"readOnlyHint": True})
async def front_list_tags() -> str:
    """List all available Front tags."""
    if not front_config.is_configured:
        return "Error: Front not configured."
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{front_config.base_url}/tags", headers=front_config.headers())
            response.raise_for_status()
            tags = response.json().get("_results", [])
        if not tags:
            return "No tags found."
        return "## Front Tags\n\n" + "\n".join([f"- **{t.get('name', 'Unknown')}** (ID: `{t.get('id', 'N/A')}`)" for t in tags])
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# SharePoint Integration (Microsoft Graph API)
# ============================================================================

class SharePointConfig:
    def __init__(self):
        self.client_id = os.getenv("SHAREPOINT_CLIENT_ID", "")
        self.client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET", "")
        self.tenant_id = os.getenv("SHAREPOINT_TENANT_ID", "")
        self._refresh_token = os.getenv("SHAREPOINT_REFRESH_TOKEN", "")
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
    
    @property
    def is_configured(self) -> bool:
        return all([self.client_id, self.client_secret, self.tenant_id, self._refresh_token])
    
    async def get_access_token(self) -> str:
        """Get valid access token, refreshing if needed."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self._refresh_token,
                    "scope": "https://graph.microsoft.com/.default offline_access"
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            data = response.json()
            
            self._access_token = data["access_token"]
            if "refresh_token" in data:
                new_refresh = data["refresh_token"]
                if new_refresh != self._refresh_token:
                    self._refresh_token = new_refresh
                    update_secret_sync("SHAREPOINT_REFRESH_TOKEN", new_refresh)
                    logger.info("SharePoint refresh token rotated and saved to Secret Manager")
            
            expires_in = data.get("expires_in", 3600)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            return self._access_token

sharepoint_config = SharePointConfig()


@mcp.tool(annotations={"readOnlyHint": True})
async def sharepoint_auth_start() -> str:
    """Get authorization URL to connect SharePoint. Use this if SharePoint is not connected."""
    client_id = os.getenv("SHAREPOINT_CLIENT_ID", "")
    tenant_id = os.getenv("SHAREPOINT_TENANT_ID", "")
    
    if not client_id:
        return "Error: SHAREPOINT_CLIENT_ID not configured in secrets."
    if not tenant_id:
        return "Error: SHAREPOINT_TENANT_ID not configured in secrets."
    
    redirect_uri = f"{CLOUD_RUN_URL}/sharepoint-callback"
    scopes = "offline_access Sites.ReadWrite.All Files.ReadWrite.All"
    
    auth_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scopes}"
        f"&response_mode=query"
        f"&state=sharepoint"
    )
    
    return f"""## SharePoint Authorization Required

**Click this link to authorize:**
{auth_url}

After authorizing, you'll be redirected back automatically and SharePoint will be connected.

**Redirect URI for Azure AD App:** `{redirect_uri}`

Make sure your Azure AD app has the following API permissions:
- Microsoft Graph > Sites.ReadWrite.All
- Microsoft Graph > Files.ReadWrite.All"""


@mcp.tool(annotations={"readOnlyHint": False})
async def sharepoint_auth_complete(
    auth_code: str = Field(..., description="Authorization code from callback URL")
) -> str:
    """Complete SharePoint authorization with the code from callback URL."""
    client_id = os.getenv("SHAREPOINT_CLIENT_ID", "")
    client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET", "")
    tenant_id = os.getenv("SHAREPOINT_TENANT_ID", "")
    
    if not all([client_id, client_secret, tenant_id]):
        return "Error: SharePoint credentials not configured."
    
    redirect_uri = f"{CLOUD_RUN_URL}/sharepoint-callback"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": auth_code,
                    "redirect_uri": redirect_uri,
                    "scope": "https://graph.microsoft.com/.default offline_access"
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            tokens = response.json()
            
            access_token = tokens["access_token"]
            refresh_token = tokens.get("refresh_token", "")
        
        sharepoint_config._access_token = access_token
        sharepoint_config._refresh_token = refresh_token
        sharepoint_config._token_expiry = datetime.now() + timedelta(seconds=tokens.get("expires_in", 3600) - 60)
        
        saved_refresh = update_secret_sync("SHAREPOINT_REFRESH_TOKEN", refresh_token) if refresh_token else False
        
        if saved_refresh:
            return f"""✅ SharePoint connected successfully!

**Tenant ID:** {tenant_id}

Refresh token has been automatically saved to Secret Manager."""
        else:
            return f"""✅ SharePoint connected for this session!

**Tenant ID:** {tenant_id}

⚠️ To persist, run:
```bash
echo -n "{refresh_token}" | gcloud secrets versions add SHAREPOINT_REFRESH_TOKEN --data-file=- --project=crowdmcp
```"""

    except httpx.HTTPStatusError as e:
        return f"Error: {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def sharepoint_list_sites(
    search: Optional[str] = Field(None, description="Search sites by name"),
    limit: int = Field(20, description="Max results")
) -> str:
    """List SharePoint sites."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured. Run sharepoint_auth_start to connect."
    
    try:
        token = await sharepoint_config.get_access_token()
        
        if search:
            url = f"https://graph.microsoft.com/v1.0/sites?search={search}&$top={limit}"
        else:
            url = f"https://graph.microsoft.com/v1.0/sites?$top={limit}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            sites = response.json().get("value", [])
        
        if not sites:
            return "No sites found."
        
        results = []
        for site in sites[:limit]:
            name = site.get("displayName", site.get("name", "Unknown"))
            web_url = site.get("webUrl", "N/A")
            site_id = site.get("id", "N/A")
            results.append(f"**{name}**\n  URL: {web_url}\n  ID: `{site_id}`")
        
        return f"## SharePoint Sites\n\nFound {len(results)} site(s):\n\n" + "\n\n".join(results)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return "Error: SharePoint authentication expired. Run sharepoint_auth_start to reconnect."
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def sharepoint_get_site(
    site_identifier: str = Field(..., description="Site hostname (e.g., 'crowdit.sharepoint.com') or site ID")
) -> str:
    """Get details of a specific SharePoint site."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured."
    
    try:
        token = await sharepoint_config.get_access_token()
        
        # Handle different identifier formats
        if ".sharepoint.com" in site_identifier:
            # It's a URL/hostname - need to construct the site path
            if "/" in site_identifier.split(".sharepoint.com")[-1]:
                # Has a site path like crowdit.sharepoint.com:/sites/IT
                parts = site_identifier.split(".sharepoint.com")
                hostname = parts[0] + ".sharepoint.com"
                path = parts[1].lstrip(":")
                url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:{path}"
            else:
                # Just the root site
                url = f"https://graph.microsoft.com/v1.0/sites/{site_identifier}"
        else:
            # Assume it's a site ID
            url = f"https://graph.microsoft.com/v1.0/sites/{site_identifier}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            site = response.json()
        
        return f"""# SharePoint Site: {site.get('displayName', 'Unknown')}

**Name:** {site.get('name', 'N/A')}
**URL:** {site.get('webUrl', 'N/A')}
**Site ID:** `{site.get('id', 'N/A')}`
**Description:** {site.get('description', 'No description')}
**Created:** {site.get('createdDateTime', 'N/A')[:10] if site.get('createdDateTime') else 'N/A'}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def sharepoint_list_drives(
    site_id: str = Field(..., description="SharePoint site ID")
) -> str:
    """List document libraries (drives) in a SharePoint site."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured."
    
    try:
        token = await sharepoint_config.get_access_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives",
                headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            drives = response.json().get("value", [])
        
        if not drives:
            return "No document libraries found."
        
        results = []
        for drive in drives:
            name = drive.get("name", "Unknown")
            drive_id = drive.get("id", "N/A")
            drive_type = drive.get("driveType", "N/A")
            web_url = drive.get("webUrl", "N/A")
            quota = drive.get("quota", {})
            used = quota.get("used", 0) / (1024*1024*1024) if quota.get("used") else 0
            
            results.append(f"**{name}**\n  Type: {drive_type} | Used: {used:.2f} GB\n  URL: {web_url}\n  Drive ID: `{drive_id}`")
        
        return f"## Document Libraries\n\nFound {len(results)} drive(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def sharepoint_list_items(
    drive_id: str = Field(..., description="Drive ID"),
    folder_path: str = Field("", description="Folder path (e.g., 'Documents/Projects' or empty for root)"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List files and folders in a SharePoint document library."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured."
    
    try:
        token = await sharepoint_config.get_access_token()
        
        if folder_path:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder_path}:/children?$top={limit}"
        else:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children?$top={limit}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            items = response.json().get("value", [])
        
        if not items:
            return f"No items found in {'/' + folder_path if folder_path else 'root'}."
        
        results = []
        for item in items[:limit]:
            name = item.get("name", "Unknown")
            item_id = item.get("id", "N/A")
            is_folder = "folder" in item
            size = item.get("size", 0)
            modified = item.get("lastModifiedDateTime", "")[:10] if item.get("lastModifiedDateTime") else "N/A"
            
            if is_folder:
                child_count = item.get("folder", {}).get("childCount", 0)
                results.append(f"📁 **{name}/** ({child_count} items)\n   ID: `{item_id}`")
            else:
                size_kb = size / 1024
                results.append(f"📄 **{name}** ({size_kb:.1f} KB) - Modified: {modified}\n   ID: `{item_id}`")
        
        path_display = '/' + folder_path if folder_path else 'root'
        return f"## Contents of {path_display}\n\nFound {len(results)} item(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def sharepoint_create_folder(
    drive_id: str = Field(..., description="Drive ID"),
    folder_name: str = Field(..., description="Name of the new folder"),
    parent_path: str = Field("", description="Parent folder path (empty for root)")
) -> str:
    """Create a new folder in SharePoint."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured."
    
    try:
        token = await sharepoint_config.get_access_token()
        
        if parent_path:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{parent_path}:/children"
        else:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"
        
        payload = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
            )
            response.raise_for_status()
            folder = response.json()
        
        full_path = f"{parent_path}/{folder_name}" if parent_path else folder_name
        return f"✅ Folder created: **{folder_name}**\n\nPath: /{full_path}\nID: `{folder.get('id', 'N/A')}`"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            return f"Error: Folder '{folder_name}' already exists."
        return f"Error: {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def sharepoint_create_folder_structure(
    drive_id: str = Field(..., description="Drive ID"),
    structure: str = Field(..., description='JSON structure: {"root_folder": ["subfolder1", "subfolder2", {"subfolder3": ["nested1", "nested2"]}]}'),
    parent_path: str = Field("", description="Parent folder path (empty for root)")
) -> str:
    """Create a complete folder structure in SharePoint from a JSON definition."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured."
    
    try:
        folder_structure = json.loads(structure)
    except json.JSONDecodeError:
        return "Error: Invalid JSON structure. Example: {\"Projects\": [\"2024\", \"2025\", {\"Templates\": [\"Word\", \"Excel\"]}]}"
    
    created_folders = []
    errors = []
    
    async def create_folder_recursive(drive_id: str, parent: str, structure_item):
        token = await sharepoint_config.get_access_token()
        
        if isinstance(structure_item, str):
            # Simple folder name
            folder_name = structure_item
            try:
                if parent:
                    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{parent}:/children"
                else:
                    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        json={"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                    )
                    if response.status_code == 201:
                        full_path = f"{parent}/{folder_name}" if parent else folder_name
                        created_folders.append(full_path)
                    elif response.status_code == 409:
                        full_path = f"{parent}/{folder_name}" if parent else folder_name
                        errors.append(f"Already exists: {full_path}")
                    else:
                        response.raise_for_status()
            except Exception as e:
                errors.append(f"Failed to create {folder_name}: {str(e)}")
        
        elif isinstance(structure_item, dict):
            # Folder with children
            for folder_name, children in structure_item.items():
                full_path = f"{parent}/{folder_name}" if parent else folder_name
                
                # Create the parent folder first
                try:
                    if parent:
                        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{parent}:/children"
                    else:
                        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"
                    
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            url,
                            json={"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                        )
                        if response.status_code == 201:
                            created_folders.append(full_path)
                        elif response.status_code != 409:  # Ignore already exists
                            response.raise_for_status()
                except Exception as e:
                    errors.append(f"Failed to create {folder_name}: {str(e)}")
                
                # Recursively create children
                if isinstance(children, list):
                    for child in children:
                        await create_folder_recursive(drive_id, full_path, child)
    
    # Process the top-level structure
    for key, value in folder_structure.items():
        await create_folder_recursive(drive_id, parent_path, {key: value})
    
    result = f"## Folder Structure Created\n\n"
    if created_folders:
        result += f"✅ Created {len(created_folders)} folder(s):\n"
        for f in created_folders:
            result += f"  - /{f}\n"
    
    if errors:
        result += f"\n⚠️ {len(errors)} issue(s):\n"
        for e in errors:
            result += f"  - {e}\n"
    
    if not created_folders and not errors:
        result += "No folders were created."
    
    return result


@mcp.tool(annotations={"readOnlyHint": True})
async def sharepoint_search(
    query: str = Field(..., description="Search query"),
    site_id: Optional[str] = Field(None, description="Limit search to specific site ID"),
    limit: int = Field(20, description="Max results")
) -> str:
    """Search for files and folders across SharePoint."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured."
    
    try:
        token = await sharepoint_config.get_access_token()
        
        if site_id:
            url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/search(q='{query}')?$top={limit}"
        else:
            url = f"https://graph.microsoft.com/v1.0/me/drive/root/search(q='{query}')?$top={limit}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            items = response.json().get("value", [])
        
        if not items:
            return f"No results found for '{query}'."
        
        results = []
        for item in items[:limit]:
            name = item.get("name", "Unknown")
            is_folder = "folder" in item
            web_url = item.get("webUrl", "N/A")
            parent_path = item.get("parentReference", {}).get("path", "").split("root:")[-1]
            
            icon = "📁" if is_folder else "📄"
            results.append(f"{icon} **{name}**\n  Path: {parent_path}\n  URL: {web_url}")
        
        return f"## Search Results for '{query}'\n\nFound {len(results)} item(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def sharepoint_upload_file(
    drive_id: str = Field(..., description="Drive ID"),
    file_name: str = Field(..., description="Name for the file"),
    content: str = Field(..., description="File content (text)"),
    folder_path: str = Field("", description="Folder path (empty for root)")
) -> str:
    """Upload a text file to SharePoint."""
    if not sharepoint_config.is_configured:
        return "Error: SharePoint not configured."
    
    try:
        token = await sharepoint_config.get_access_token()
        
        if folder_path:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder_path}/{file_name}:/content"
        else:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{file_name}:/content"
        
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url,
                content=content.encode('utf-8'),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "text/plain"
                }
            )
            response.raise_for_status()
            file_info = response.json()
        
        full_path = f"{folder_path}/{file_name}" if folder_path else file_name
        return f"✅ File uploaded: **{file_name}**\n\nPath: /{full_path}\nSize: {file_info.get('size', 0)} bytes\nURL: {file_info.get('webUrl', 'N/A')}"
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Quoter Integration (ScalePad Quoter - MSP Quoting Software)
# ============================================================================

class QuoterConfig:
    def __init__(self):
        self.api_key = os.getenv("QUOTER_API_KEY", "")
        self.client_id = os.getenv("QUOTER_CLIENT_ID", "")
        self.client_secret = os.getenv("QUOTER_CLIENT_SECRET", "")
        self.base_url = "https://api.quoter.com/v1"
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
    
    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) or (bool(self.client_id) and bool(self.client_secret))
    
    async def get_access_token(self) -> str:
        # If we have a simple API key, use it directly
        if self.api_key:
            return self.api_key
        
        # Otherwise use OAuth2 flow
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token
        
        # Try refresh token first
        if self._refresh_token:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/auth/refresh",
                        headers={"Authorization": f"Bearer {self._refresh_token}"}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        self._access_token = data["access_token"]
                        self._refresh_token = data.get("refresh_token", self._refresh_token)
                        self._token_expiry = datetime.now() + timedelta(seconds=3540)  # 59 minutes
                        return self._access_token
            except Exception:
                pass
        
        # Get new token
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/auth/oauth/authorize",
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials"
                },
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            self._refresh_token = data.get("refresh_token")
            self._token_expiry = datetime.now() + timedelta(seconds=3540)  # 59 minutes
            return self._access_token

quoter_config = QuoterConfig()


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_list_quotes(
    status: Optional[str] = Field(None, description="Filter by status"),
    limit: int = Field(50, description="Max results (1-100)"),
    page: int = Field(1, description="Page number")
) -> str:
    """List quotes from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured. Set QUOTER_API_KEY or QUOTER_CLIENT_ID + QUOTER_CLIENT_SECRET."
    
    try:
        token = await quoter_config.get_access_token()
        params = {"limit": min(max(1, limit), 100), "page": page}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/quotes",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
        
        quotes = data.get("data", [])
        if not quotes:
            return "No quotes found."
        
        results = []
        for q in quotes:
            quote_id = q.get("id", "N/A")
            name = q.get("name", "Untitled")
            status_val = q.get("status", "N/A")
            total = q.get("total", 0)
            contact = q.get("contact_name", q.get("organization", "N/A"))
            created = q.get("created_at", "")[:10] if q.get("created_at") else "N/A"
            
            results.append(f"**{name}** (ID: {quote_id})\n  Contact: {contact} | Status: {status_val} | Total: ${total:,.2f} | Created: {created}")
        
        has_more = data.get("has_more", False)
        more_msg = " (more available)" if has_more else ""
        return f"Found {len(results)} quote(s){more_msg}:\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_list_contacts(
    search: Optional[str] = Field(None, description="Search by name, email, or organization"),
    limit: int = Field(50, description="Max results (1-100)"),
    page: int = Field(1, description="Page number")
) -> str:
    """List contacts from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        params = {"limit": min(max(1, limit), 100), "page": page}
        if search:
            params["organization[cont]"] = search
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/contacts",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
        
        contacts = data.get("data", [])
        if not contacts:
            return "No contacts found."
        
        results = []
        for c in contacts:
            contact_id = c.get("id", "N/A")
            name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip() or "N/A"
            org = c.get("organization", "N/A")
            email = c.get("email", "N/A")
            phone = c.get("work_phone", c.get("mobile_phone", "N/A"))
            
            results.append(f"**{name}** (ID: {contact_id})\n  Organization: {org} | Email: {email} | Phone: {phone}")
        
        return f"Found {len(results)} contact(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_get_contact(
    contact_id: str = Field(..., description="Contact ID")
) -> str:
    """Get detailed contact information from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/contacts/{contact_id}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            c = response.json()
        
        return f"""# Contact: {c.get('first_name', '')} {c.get('last_name', '')}

**ID:** {c.get('id', 'N/A')}
**Organization:** {c.get('organization', 'N/A')}
**Title:** {c.get('title', 'N/A')}
**Email:** {c.get('email', 'N/A')}
**Work Phone:** {c.get('work_phone', 'N/A')}
**Mobile Phone:** {c.get('mobile_phone', 'N/A')}
**Website:** {c.get('website', 'N/A')}

## Billing Address
{c.get('billing_address', 'N/A')} {c.get('billing_address2', '')}
{c.get('billing_city', '')}, {c.get('billing_region_iso', '')} {c.get('billing_postal_code', '')}
{c.get('billing_country_iso', '')}

## Shipping Address
{c.get('shipping_address', 'N/A')} {c.get('shipping_address2', '')}
{c.get('shipping_city', '')}, {c.get('shipping_region_iso', '')} {c.get('shipping_postal_code', '')}
{c.get('shipping_country_iso', '')}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def quoter_create_contact(
    first_name: str = Field(..., description="First name"),
    last_name: str = Field(..., description="Last name"),
    email: str = Field(..., description="Email address"),
    organization: Optional[str] = Field(None, description="Organization/company name"),
    work_phone: Optional[str] = Field(None, description="Work phone number"),
    billing_address: Optional[str] = Field(None, description="Billing address"),
    billing_city: Optional[str] = Field(None, description="Billing city"),
    billing_region_iso: Optional[str] = Field(None, description="Billing state/region (e.g., 'NSW', 'VIC')"),
    billing_postal_code: Optional[str] = Field(None, description="Billing postal code"),
    billing_country_iso: Optional[str] = Field("AU", description="Billing country ISO code (default: AU)")
) -> str:
    """Create a new contact in Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        
        payload = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email
        }
        if organization: payload["organization"] = organization
        if work_phone: payload["work_phone"] = work_phone
        if billing_address: payload["billing_address"] = billing_address
        if billing_city: payload["billing_city"] = billing_city
        if billing_region_iso: payload["billing_region_iso"] = billing_region_iso
        if billing_postal_code: payload["billing_postal_code"] = billing_postal_code
        if billing_country_iso: payload["billing_country_iso"] = billing_country_iso
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{quoter_config.base_url}/contacts",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            c = response.json()
        
        return f"✅ Contact created: **{first_name} {last_name}** (ID: {c.get('id', 'N/A')})"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_list_items(
    search: Optional[str] = Field(None, description="Search by name"),
    category_id: Optional[str] = Field(None, description="Filter by category ID"),
    limit: int = Field(50, description="Max results (1-100)"),
    page: int = Field(1, description="Page number")
) -> str:
    """List items/products from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        params = {"limit": min(max(1, limit), 100), "page": page}
        if search:
            params["name[cont]"] = search
        if category_id:
            params["category_id"] = category_id
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/items",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
        
        items = data.get("data", [])
        if not items:
            return "No items found."
        
        results = []
        for i in items:
            item_id = i.get("id", "N/A")
            name = i.get("name", "Untitled")
            sku = i.get("sku", "N/A")
            price = i.get("price_amount_decimal", 0)
            try:
                price = float(price) / 100 if price else 0
            except:
                price = 0
            category = i.get("category_name", "N/A")
            item_type = i.get("type", "N/A")
            
            results.append(f"**{name}** (SKU: {sku})\n  ID: {item_id} | Type: {item_type} | Price: ${price:,.2f} | Category: {category}")
        
        return f"Found {len(results)} item(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_get_item(
    item_id: str = Field(..., description="Item ID")
) -> str:
    """Get detailed item information from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/items/{item_id}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            i = response.json()
        
        price = i.get("price_amount_decimal", 0)
        try:
            price = float(price) / 100 if price else 0
        except:
            price = 0
        
        cost = i.get("cost_amount_decimal", 0)
        try:
            cost = float(cost) / 100 if cost else 0
        except:
            cost = 0
        
        return f"""# Item: {i.get('name', 'Unknown')}

**ID:** {i.get('id', 'N/A')}
**SKU:** {i.get('sku', 'N/A')}
**Type:** {i.get('type', 'N/A')}
**Category:** {i.get('category_name', 'N/A')}

## Pricing
**Sell Price:** ${price:,.2f}
**Cost:** ${cost:,.2f}
**Taxable:** {i.get('taxable', False)}
**Recurring:** {i.get('recurring', False)}
**Recurring Frequency:** {i.get('recurring_frequency', 'N/A')}

## Details
**Manufacturer:** {i.get('manufacturer_name', 'N/A')}
**MPN:** {i.get('mpn', 'N/A')}
**Description:** {i.get('description', 'N/A')}

**Created:** {i.get('created_at', 'N/A')[:10] if i.get('created_at') else 'N/A'}
**Modified:** {i.get('modified_at', 'N/A')[:10] if i.get('modified_at') else 'N/A'}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_list_categories(
    limit: int = Field(100, description="Max results (1-100)"),
    page: int = Field(1, description="Page number")
) -> str:
    """List categories from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        params = {"limit": min(max(1, limit), 100), "page": page}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/categories",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
        
        categories = data.get("data", [])
        if not categories:
            return "No categories found."
        
        results = []
        for c in categories:
            cat_id = c.get("id", "N/A")
            name = c.get("name", "Untitled")
            parent = c.get("parent_category", "")
            
            parent_info = f" (Parent: {parent})" if parent else ""
            results.append(f"- **{name}** (ID: {cat_id}){parent_info}")
        
        return f"## Categories\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_list_templates(
    limit: int = Field(50, description="Max results (1-100)"),
    page: int = Field(1, description="Page number")
) -> str:
    """List quote templates from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        params = {"limit": min(max(1, limit), 100), "page": page}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/quote_templates",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
        
        templates = data.get("data", [])
        if not templates:
            return "No quote templates found."
        
        results = []
        for t in templates:
            template_id = t.get("id", "N/A")
            name = t.get("name", "Untitled")
            
            results.append(f"- **{name}** (ID: {template_id})")
        
        return f"## Quote Templates\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def quoter_create_quote(
    contact_id: str = Field(..., description="Contact ID"),
    name: Optional[str] = Field(None, description="Quote name/title"),
    template_id: Optional[str] = Field(None, description="Quote template ID to use")
) -> str:
    """Create a new draft quote in Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        
        payload = {"contact_id": contact_id}
        if name: payload["name"] = name
        if template_id: payload["quote_template_id"] = template_id
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{quoter_config.base_url}/quotes",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            q = response.json()
        
        quote_name = q.get("name", "Draft Quote")
        quote_id = q.get("id", "N/A")
        return f"✅ Quote created: **{quote_name}** (ID: {quote_id})\n\nNote: This creates a draft quote. Add line items and publish via the Quoter web interface."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_list_manufacturers(
    search: Optional[str] = Field(None, description="Search by name"),
    limit: int = Field(50, description="Max results (1-100)"),
    page: int = Field(1, description="Page number")
) -> str:
    """List manufacturers from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        params = {"limit": min(max(1, limit), 100), "page": page}
        if search:
            params["name[cont]"] = search
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/manufacturers",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
        
        manufacturers = data.get("data", [])
        if not manufacturers:
            return "No manufacturers found."
        
        results = []
        for m in manufacturers:
            mfr_id = m.get("id", "N/A")
            name = m.get("name", "Unknown")
            
            results.append(f"- **{name}** (ID: {mfr_id})")
        
        return f"## Manufacturers\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def quoter_list_suppliers(
    limit: int = Field(50, description="Max results (1-100)"),
    page: int = Field(1, description="Page number")
) -> str:
    """List suppliers from Quoter."""
    if not quoter_config.is_configured:
        return "Error: Quoter not configured."
    
    try:
        token = await quoter_config.get_access_token()
        params = {"limit": min(max(1, limit), 100), "page": page}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{quoter_config.base_url}/suppliers",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
        
        suppliers = data.get("data", [])
        if not suppliers:
            return "No suppliers found."
        
        results = []
        for s in suppliers:
            supplier_id = s.get("id", "N/A")
            name = s.get("name", "Unknown")
            
            results.append(f"- **{name}** (ID: {supplier_id})")
        
        return f"## Suppliers\n\n" + "\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Pax8 Integration (Cloud Marketplace)
# ============================================================================

class Pax8Config:
    def __init__(self):
        self.client_id = os.getenv("PAX8_CLIENT_ID", "")
        self.client_secret = os.getenv("PAX8_CLIENT_SECRET", "")
        self.base_url = "https://api.pax8.com/v1"
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id) and bool(self.client_secret)

    async def get_access_token(self) -> str:
        """Get valid access token, requesting new one if expired."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/token",
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "audience": "api://p8p.client",
                    "grant_type": "client_credentials"
                },
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            # Pax8 tokens are valid for 24 hours, refresh 1 hour early
            expires_in = data.get("expires_in", 86400)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 3600)
            return self._access_token

pax8_config = Pax8Config()


@mcp.tool(annotations={"readOnlyHint": True})
async def pax8_list_subscriptions(
    company_id: Optional[str] = Field(None, description="Filter by Pax8 company ID"),
    product_id: Optional[str] = Field(None, description="Filter by product ID"),
    status: Optional[str] = Field(None, description="Filter by status: Active, Cancelled, PendingManual, etc."),
    page: int = Field(0, description="Page number (0-indexed)"),
    size: int = Field(50, description="Page size (max 200)")
) -> str:
    """List subscriptions from Pax8 for verification against Xero."""
    if not pax8_config.is_configured:
        return "Error: Pax8 not configured. Set PAX8_CLIENT_ID and PAX8_CLIENT_SECRET environment variables."

    try:
        token = await pax8_config.get_access_token()
        params = {"page": page, "size": min(max(1, size), 200)}
        if company_id:
            params["companyId"] = company_id
        if product_id:
            params["productId"] = product_id
        if status:
            params["status"] = status

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{pax8_config.base_url}/subscriptions",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()

        subscriptions = data.get("content", [])
        page_info = data.get("page", {})

        if not subscriptions:
            return "No subscriptions found."

        results = []
        for s in subscriptions:
            sub_id = s.get("id", "N/A")
            company_name = s.get("companyName", s.get("companyId", "N/A"))
            product_name = s.get("productName", s.get("productId", "N/A"))
            quantity = s.get("quantity", 0)
            status_val = s.get("status", "N/A")
            billing_term = s.get("billingTerm", "N/A")
            price = s.get("price", 0)
            start_date = s.get("startDate", "")[:10] if s.get("startDate") else "N/A"

            results.append(
                f"**{product_name}** (ID: `{sub_id}`)\n"
                f"  Company: {company_name} | Qty: {quantity} | Status: {status_val}\n"
                f"  Price: ${price:,.2f} | Term: {billing_term} | Started: {start_date}"
            )

        total = page_info.get("totalElements", len(subscriptions))
        total_pages = page_info.get("totalPages", 1)
        current_page = page_info.get("number", page)

        return f"## Pax8 Subscriptions (Page {current_page + 1}/{total_pages}, Total: {total})\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def pax8_get_subscription(
    subscription_id: str = Field(..., description="Pax8 subscription ID")
) -> str:
    """Get detailed subscription information from Pax8."""
    if not pax8_config.is_configured:
        return "Error: Pax8 not configured."

    try:
        token = await pax8_config.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{pax8_config.base_url}/subscriptions/{subscription_id}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            s = response.json()

        lines = [
            f"# Subscription: {s.get('productName', 'N/A')}",
            f"\n**ID:** `{s.get('id', 'N/A')}`",
            f"**Company:** {s.get('companyName', s.get('companyId', 'N/A'))}",
            f"**Product ID:** `{s.get('productId', 'N/A')}`",
            f"**Vendor Subscription ID:** `{s.get('vendorSubscriptionId', 'N/A')}`",
            f"\n## Billing Details",
            f"- **Status:** {s.get('status', 'N/A')}",
            f"- **Quantity:** {s.get('quantity', 0)}",
            f"- **Price:** ${s.get('price', 0):,.2f}",
            f"- **Billing Term:** {s.get('billingTerm', 'N/A')}",
            f"- **Commitment Term:** {s.get('commitmentTerm', 'N/A')}",
            f"\n## Dates",
            f"- **Start Date:** {s.get('startDate', 'N/A')}",
            f"- **End Date:** {s.get('endDate', 'N/A')}",
            f"- **Created:** {s.get('createdDate', 'N/A')}",
        ]

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def pax8_list_companies(
    city: Optional[str] = Field(None, description="Filter by city"),
    country: Optional[str] = Field(None, description="Filter by country (e.g., 'AU', 'US')"),
    page: int = Field(0, description="Page number (0-indexed)"),
    size: int = Field(50, description="Page size (max 200)")
) -> str:
    """List companies from Pax8."""
    if not pax8_config.is_configured:
        return "Error: Pax8 not configured."

    try:
        token = await pax8_config.get_access_token()
        params = {"page": page, "size": min(max(1, size), 200)}
        if city:
            params["city"] = city
        if country:
            params["country"] = country

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{pax8_config.base_url}/companies",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()

        companies = data.get("content", [])
        page_info = data.get("page", {})

        if not companies:
            return "No companies found."

        results = []
        for c in companies:
            company_id = c.get("id", "N/A")
            name = c.get("name", "Unknown")
            city_val = c.get("city", "N/A")
            country_val = c.get("country", "N/A")
            status_val = c.get("status", "N/A")

            results.append(f"**{name}** (ID: `{company_id}`)\n  Location: {city_val}, {country_val} | Status: {status_val}")

        total = page_info.get("totalElements", len(companies))
        total_pages = page_info.get("totalPages", 1)
        current_page = page_info.get("number", page)

        return f"## Pax8 Companies (Page {current_page + 1}/{total_pages}, Total: {total})\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def pax8_get_company(
    company_id: str = Field(..., description="Pax8 company ID")
) -> str:
    """Get detailed company information from Pax8."""
    if not pax8_config.is_configured:
        return "Error: Pax8 not configured."

    try:
        token = await pax8_config.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{pax8_config.base_url}/companies/{company_id}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            c = response.json()

        lines = [
            f"# Company: {c.get('name', 'N/A')}",
            f"\n**ID:** `{c.get('id', 'N/A')}`",
            f"**External ID:** `{c.get('externalId', 'N/A')}`",
            f"\n## Contact Details",
            f"- **Address:** {c.get('address', 'N/A')}",
            f"- **City:** {c.get('city', 'N/A')}",
            f"- **State/Province:** {c.get('stateOrProvince', 'N/A')}",
            f"- **Postal Code:** {c.get('postalCode', 'N/A')}",
            f"- **Country:** {c.get('country', 'N/A')}",
            f"- **Phone:** {c.get('phone', 'N/A')}",
            f"- **Website:** {c.get('website', 'N/A')}",
            f"\n## Status",
            f"- **Status:** {c.get('status', 'N/A')}",
            f"- **Bill on Behalf:** {c.get('billOnBehalfOfEnabled', 'N/A')}",
            f"- **Self-Service:** {c.get('selfServiceAllowed', 'N/A')}",
        ]

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def pax8_list_products(
    vendor_name: Optional[str] = Field(None, description="Filter by vendor name (e.g., 'Microsoft')"),
    page: int = Field(0, description="Page number (0-indexed)"),
    size: int = Field(50, description="Page size (max 200)")
) -> str:
    """List available products from Pax8 catalog."""
    if not pax8_config.is_configured:
        return "Error: Pax8 not configured."

    try:
        token = await pax8_config.get_access_token()
        params = {"page": page, "size": min(max(1, size), 200)}
        if vendor_name:
            params["vendorName"] = vendor_name

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{pax8_config.base_url}/products",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()

        products = data.get("content", [])
        page_info = data.get("page", {})

        if not products:
            return "No products found."

        results = []
        for p in products:
            product_id = p.get("id", "N/A")
            name = p.get("name", "Unknown")
            vendor = p.get("vendorName", "N/A")

            results.append(f"**{name}** (ID: `{product_id}`)\n  Vendor: {vendor}")

        total = page_info.get("totalElements", len(products))
        total_pages = page_info.get("totalPages", 1)
        current_page = page_info.get("number", page)

        return f"## Pax8 Products (Page {current_page + 1}/{total_pages}, Total: {total})\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Server Status
# ============================================================================

@mcp.tool(annotations={"readOnlyHint": True})
async def server_status() -> str:
    """Check MCP server status and integrations."""
    lines = ["# Crowd IT MCP Server Status\n"]
    
    if halopsa_config.is_configured:
        try:
            await halopsa_config.get_access_token()
            lines.append("✅ **HaloPSA:** Connected")
        except Exception as e:
            lines.append(f"❌ **HaloPSA:** Auth failed - {str(e)[:50]}")
    else:
        lines.append("⚠️ **HaloPSA:** Not configured")
    
    if xero_config.is_configured:
        try:
            await xero_config.get_access_token()
            lines.append("✅ **Xero:** Connected")
        except Exception as e:
            lines.append(f"❌ **Xero:** Auth failed - {str(e)[:50]}")
    else:
        missing = []
        if not os.getenv("XERO_CLIENT_ID"): missing.append("CLIENT_ID")
        if not os.getenv("XERO_CLIENT_SECRET"): missing.append("CLIENT_SECRET")
        if not os.getenv("XERO_TENANT_ID"): missing.append("TENANT_ID")
        if not os.getenv("XERO_REFRESH_TOKEN"): missing.append("REFRESH_TOKEN")
        lines.append(f"⚠️ **Xero:** Missing: {', '.join(missing)}")
    
    if sharepoint_config.is_configured:
        try:
            await sharepoint_config.get_access_token()
            lines.append("✅ **SharePoint:** Connected")
        except Exception as e:
            lines.append(f"❌ **SharePoint:** Auth failed - {str(e)[:50]}")
    else:
        missing = []
        if not os.getenv("SHAREPOINT_CLIENT_ID"): missing.append("CLIENT_ID")
        if not os.getenv("SHAREPOINT_CLIENT_SECRET"): missing.append("CLIENT_SECRET")
        if not os.getenv("SHAREPOINT_TENANT_ID"): missing.append("TENANT_ID")
        if not os.getenv("SHAREPOINT_REFRESH_TOKEN"): missing.append("REFRESH_TOKEN")
        lines.append(f"⚠️ **SharePoint:** Missing: {', '.join(missing)}")
    
    if front_config.is_configured:
        lines.append("✅ **Front:** Connected")
    else:
        lines.append("⚠️ **Front:** Not configured")
    
    if quoter_config.is_configured:
        try:
            await quoter_config.get_access_token()
            lines.append("✅ **Quoter:** Connected")
        except Exception as e:
            lines.append(f"❌ **Quoter:** Auth failed - {str(e)[:50]}")
    else:
        lines.append("⚠️ **Quoter:** Not configured (set QUOTER_API_KEY)")

    if pax8_config.is_configured:
        try:
            await pax8_config.get_access_token()
            lines.append("✅ **Pax8:** Connected")
        except Exception as e:
            lines.append(f"❌ **Pax8:** Auth failed - {str(e)[:50]}")
    else:
        missing = []
        if not os.getenv("PAX8_CLIENT_ID"): missing.append("CLIENT_ID")
        if not os.getenv("PAX8_CLIENT_SECRET"): missing.append("CLIENT_SECRET")
        lines.append(f"⚠️ **Pax8:** Missing: {', '.join(missing)}")

    lines.append(f"\n**Cloud Run URL:** {CLOUD_RUN_URL}")
    return "\n".join(lines)





# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.responses import PlainTextResponse, HTMLResponse
    
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Starting Crowd IT MCP Server on port {port}")
    
    # Get FastMCP's HTTP app
    mcp_app = mcp.http_app()
    
    # Starlette route handlers
    async def home_route(request):
        return HTMLResponse("<html><body><h1>Crowd IT MCP Server</h1><p>MCP endpoint: /mcp</p></body></html>")
    
    async def health_route(request):
        return PlainTextResponse("OK")
    
    async def callback_route(request):
        code = request.query_params.get("code")
        error = request.query_params.get("error")
        
        if error:
            return HTMLResponse(f"<html><body><h1>❌ Authorization Failed</h1><p>{error}</p></body></html>", status_code=400)
        
        if not code:
            return HTMLResponse("<html><body><h1>No Authorization Code</h1></body></html>", status_code=400)
        
        client_id = os.getenv("XERO_CLIENT_ID", "")
        client_secret = os.getenv("XERO_CLIENT_SECRET", "")
        redirect_uri = f"{CLOUD_RUN_URL}/callback"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post("https://identity.xero.com/connect/token",
                    data={"grant_type": "authorization_code", "client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect_uri},
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                response.raise_for_status()
                tokens = response.json()
                access_token, refresh_token = tokens["access_token"], tokens["refresh_token"]
                
                tenant_response = await client.get("https://api.xero.com/connections", headers={"Authorization": f"Bearer {access_token}"})
                tenant_response.raise_for_status()
                connections = tenant_response.json()
                
                if not connections:
                    return HTMLResponse("<html><body><h1>No Xero organizations found</h1></body></html>", status_code=400)
                
                tenant_id = connections[0]["tenantId"]
                org_name = connections[0].get("tenantName", "Unknown")
            
            xero_config._access_token = access_token
            xero_config._refresh_token = refresh_token
            xero_config.tenant_id = tenant_id
            xero_config._token_expiry = datetime.now() + timedelta(seconds=1740)
            
            saved_refresh = update_secret_sync("XERO_REFRESH_TOKEN", refresh_token)
            saved_tenant = update_secret_sync("XERO_TENANT_ID", tenant_id)
            status_msg = "Tokens saved ✅" if (saved_refresh and saved_tenant) else "⚠️ Manual save needed"
            
            return HTMLResponse(f"""<html><head><title>Xero Connected!</title></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:50px auto;padding:20px;">
<h1 style="color:#27ae60;">✅ Xero Connected!</h1>
<p><b>Organization:</b> {org_name}</p>
<p>{status_msg}</p>
<p>You can close this window.</p>
</body></html>""")
        except Exception as e:
            return HTMLResponse(f"<html><body><h1>Error</h1><p>{str(e)}</p></body></html>", status_code=500)
    
    async def sharepoint_callback_route(request):
        code = request.query_params.get("code")
        error = request.query_params.get("error")
        error_description = request.query_params.get("error_description", "")
        
        if error:
            return HTMLResponse(f"<html><body><h1>❌ SharePoint Authorization Failed</h1><p>{error}: {error_description}</p></body></html>", status_code=400)
        
        if not code:
            return HTMLResponse("<html><body><h1>No Authorization Code</h1></body></html>", status_code=400)
        
        client_id = os.getenv("SHAREPOINT_CLIENT_ID", "")
        client_secret = os.getenv("SHAREPOINT_CLIENT_SECRET", "")
        tenant_id = os.getenv("SHAREPOINT_TENANT_ID", "")
        redirect_uri = f"{CLOUD_RUN_URL}/sharepoint-callback"
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                    data={
                        "grant_type": "authorization_code",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "redirect_uri": redirect_uri,
                        "scope": "https://graph.microsoft.com/.default offline_access"
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
                response.raise_for_status()
                tokens = response.json()
                access_token = tokens["access_token"]
                refresh_token = tokens.get("refresh_token", "")
            
            sharepoint_config._access_token = access_token
            sharepoint_config._refresh_token = refresh_token
            sharepoint_config._token_expiry = datetime.now() + timedelta(seconds=tokens.get("expires_in", 3600) - 60)
            
            saved_refresh = update_secret_sync("SHAREPOINT_REFRESH_TOKEN", refresh_token) if refresh_token else False
            status_msg = "Tokens saved ✅" if saved_refresh else "⚠️ Manual save needed"
            
            return HTMLResponse(f"""<html><head><title>SharePoint Connected!</title></head>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:50px auto;padding:20px;">
<h1 style="color:#27ae60;">✅ SharePoint Connected!</h1>
<p><b>Tenant ID:</b> {tenant_id}</p>
<p>{status_msg}</p>
<p>You can close this window.</p>
</body></html>""")
        except Exception as e:
            return HTMLResponse(f"<html><body><h1>Error</h1><p>{str(e)}</p></body></html>", status_code=500)
    
    # Run FastMCP directly - it handles its own routing
    # Add custom routes via Starlette mounting
    app = Starlette(
        routes=[
            Route("/health", health_route),
            Route("/callback", callback_route),
            Route("/sharepoint-callback", sharepoint_callback_route),
        ],
        lifespan=mcp_app.lifespan,
    )
    
    # Mount MCP app to handle all other paths (including /mcp, /sse)
    app.mount("/", mcp_app)
    
    uvicorn.run(app, host="0.0.0.0", port=port)
