"""
Crowd IT Unified MCP Server
Centralized MCP server for Cloud Run - HaloPSA, Xero, Front, SharePoint, Quoter, Pax8, BigQuery, Maxotel VoIP, and Ubuntu Server (SSH) integration.
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
    instructions="Crowd IT Unified MCP Server - HaloPSA, Xero, Front, SharePoint, Quoter, Pax8, BigQuery, Maxotel VoIP, and Ubuntu Server (SSH) integration for MSP operations.",
    stateless_http=True  # Required for Cloud Run - enables stateless sessions
)

# ============================================================================
# Secret Manager Helper
# ============================================================================

def get_secret_sync(secret_id: str) -> Optional[str]:
    """Read the latest version of a secret from Google Secret Manager."""
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        # Use GCP_PROJECT_ID first (explicitly set in Cloud Run), then GOOGLE_CLOUD_PROJECT, then default
        project_id = os.getenv("GCP_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "crowdmcp"))
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.warning(f"Failed to read secret {secret_id} from Secret Manager: {e}")
        return None


def update_secret_sync(secret_id: str, value: str) -> bool:
    """Update a secret in Google Secret Manager (sync version)."""
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        # Use GCP_PROJECT_ID first (explicitly set in Cloud Run), then GOOGLE_CLOUD_PROJECT, then default
        project_id = os.getenv("GCP_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "crowdmcp"))
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
    tax_code: str = Field("GST", description="Tax code (default: GST)"),
    item_id: Optional[int] = Field(None, description="HaloPSA Item ID (optional - for linking to catalog item)")
) -> str:
    """Add a line item to a recurring invoice."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()

        async with httpx.AsyncClient() as client:
            # Step 1: GET the existing recurring invoice with all lines
            response = await client.get(
                f"{halopsa_config.resource_server}/RecurringInvoice/{recurring_invoice_id}",
                params={"includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            if response.status_code >= 400:
                return f"Error: {response.status_code} - {response.text}"
            invoice = response.json()

            # Step 2: Map tax code string to HaloPSA tax code ID
            tax_code_map = {
                "GST": 12,
                "NO TAX": 0,
                "NONE": 0,
                "BAS EXCLUDED": 4,
            }
            tax_code_id = tax_code_map.get(tax_code.upper(), 12)  # Default to GST

            # Step 3: Create the new line item
            new_line = {
                "item_shortdescription": description,
                "item_longdescription": description,
                "baseprice": unit_price,
                "qty_order": quantity,
                "tax_code": str(tax_code_id),
                "isinactive": False,
                "isActive": True,
            }

            if item_id:
                new_line["_itemid"] = item_id

            # Step 4: Append the new line to existing lines
            existing_lines = invoice.get("lines", [])
            existing_lines.append(new_line)
            invoice["lines"] = existing_lines

            # Step 5: POST the complete invoice object back
            response = await client.post(
                f"{halopsa_config.resource_server}/RecurringInvoice",
                json=[invoice],  # HaloPSA expects an array
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            if response.status_code >= 400:
                return f"Error: {response.status_code} - {response.text}"

        return f"✅ Added line item to recurring invoice #{recurring_invoice_id}:\n- {description}\n- Qty: {quantity} x ${unit_price:.2f} = ${quantity * unit_price:.2f}"

    except httpx.HTTPStatusError as e:
        return f"Error: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_copy_recurring_invoice_lines(
    source_recurring_invoice_id: int = Field(..., description="Source Recurring Invoice ID to copy lines FROM"),
    target_recurring_invoice_id: int = Field(..., description="Target Recurring Invoice ID to copy lines TO"),
    clear_existing: bool = Field(False, description="Clear existing lines on target before copying")
) -> str:
    """Copy all line items from one recurring invoice to another."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()

        async with httpx.AsyncClient() as client:
            # Step 1: GET source recurring invoice
            response = await client.get(
                f"{halopsa_config.resource_server}/RecurringInvoice/{source_recurring_invoice_id}",
                params={"includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            if response.status_code >= 400:
                return f"Error: {response.status_code} - {response.text}"
            source_invoice = response.json()

            # Step 2: GET target recurring invoice
            response = await client.get(
                f"{halopsa_config.resource_server}/RecurringInvoice/{target_recurring_invoice_id}",
                params={"includedetails": "true", "includelines": "true"},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            if response.status_code >= 400:
                return f"Error: {response.status_code} - {response.text}"
            target_invoice = response.json()

            # Step 3: Get source lines and prepare them for the target
            source_lines = source_invoice.get("lines", [])

            new_lines = []
            for line in source_lines:
                # Create a copy of the line without IDs so HaloPSA creates new ones
                new_line = {
                    "item_shortdescription": line.get("item_shortdescription", ""),
                    "item_longdescription": line.get("item_longdescription", ""),
                    "baseprice": line.get("baseprice", 0),
                    "qty_order": line.get("qty_order", 1),
                    "tax_code": line.get("tax_code", "12"),
                    "item_code": line.get("item_code", ""),
                    "isinactive": False,
                    "isActive": True,
                    "isgroupdesc": line.get("isgroupdesc", False),
                    "group_id": line.get("group_id", 0),
                }

                if line.get("_itemid"):
                    new_line["_itemid"] = line.get("_itemid")

                new_lines.append(new_line)

            # Step 4: Set lines on target
            if clear_existing:
                target_invoice["lines"] = new_lines
            else:
                existing_lines = target_invoice.get("lines", [])
                # Remove blank default lines (qty=1, price=0, no description)
                existing_lines = [l for l in existing_lines if l.get("baseprice", 0) > 0 or l.get("item_shortdescription")]
                existing_lines.extend(new_lines)
                target_invoice["lines"] = existing_lines

            # Step 5: POST the complete target invoice back
            response = await client.post(
                f"{halopsa_config.resource_server}/RecurringInvoice",
                json=[target_invoice],
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            if response.status_code >= 400:
                return f"Error: {response.status_code} - {response.text}"

        total_value = sum(l.get("baseprice", 0) * l.get("qty_order", 1) for l in new_lines)

        return f"""✅ Copied {len(new_lines)} line items from recurring invoice #{source_recurring_invoice_id} to #{target_recurring_invoice_id}

**Lines copied:**
{chr(10).join(f"- {l.get('item_shortdescription', 'No desc')}: {l.get('qty_order', 1)} x ${l.get('baseprice', 0):.2f}" for l in new_lines)}

**Total value:** ${total_value:,.2f} + GST"""

    except httpx.HTTPStatusError as e:
        return f"Error: {e.response.status_code} - {e.response.text}"
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


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
async def halopsa_update_recurring_invoice(
    recurring_invoice_id: int = Field(..., description="Recurring Invoice ID to update"),
    invoice_name: Optional[str] = Field(None, description="New name/reference for the recurring invoice"),
    po_number: Optional[str] = Field(None, description="New PO number"),
    notes: Optional[str] = Field(None, description="Notes field")
) -> str:
    """Update a recurring invoice's header details (name/reference, PO number, notes)."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    if not any([invoice_name, po_number, notes]):
        return "Error: At least one field (invoice_name, po_number, or notes) must be provided"

    try:
        token = await halopsa_config.get_access_token()

        # Build the update payload
        payload = {
            "id": recurring_invoice_id,
        }

        if invoice_name is not None:
            payload["invoicename"] = invoice_name

        if po_number is not None:
            payload["ponumber"] = po_number

        if notes is not None:
            payload["notes"] = notes

        # HaloPSA API expects an array for POST updates
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{halopsa_config.resource_server}/RecurringInvoice",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=[payload],
                timeout=30.0
            )

            if response.status_code != 200:
                return f"HaloPSA API Error: {response.status_code} - {response.text}"

            result = response.json()

            if result and len(result) > 0:
                updated = result[0]
                return f"✅ Recurring Invoice **{recurring_invoice_id}** updated. Name: {updated.get('invoicename', 'N/A')}"

            return f"✅ Recurring Invoice **{recurring_invoice_id}** updated."
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# HaloPSA Items API
# ============================================================================

@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def halopsa_get_items(
    search: Optional[str] = Field(None, description="Search by item name, SKU, or description"),
    category_id: Optional[int] = Field(None, description="Filter by category ID"),
    item_type: Optional[str] = Field(None, description="Filter by item type: 'stock', 'non_stock', 'service', 'labour', 'all'"),
    include_inactive: bool = Field(False, description="Include inactive items"),
    supplier_id: Optional[int] = Field(None, description="Filter by supplier ID"),
    limit: int = Field(50, description="Max results (1-100)")
) -> str:
    """List HaloPSA items/products from the catalog."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(max(1, limit), 100)}

        if search:
            params["search"] = search
        if category_id:
            params["category_id"] = category_id
        if supplier_id:
            params["supplier_id"] = supplier_id
        if not include_inactive:
            params["includeinactive"] = "false"

        # Map item type to HaloPSA type IDs
        if item_type:
            type_map = {
                "stock": 1,
                "non_stock": 2,
                "service": 3,
                "labour": 4,
                "labor": 4
            }
            if item_type.lower() in type_map:
                params["itemtype_id"] = type_map[item_type.lower()]

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{halopsa_config.resource_server}/Item",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            items = response.json().get("items", [])

        if not items:
            return "No items found."

        results = []
        for item in items[:limit]:
            item_id = item.get('id', 'N/A')
            name = item.get('name', item.get('itemname', 'Unknown'))
            sku = item.get('sku', item.get('partnumber', 'N/A'))
            price = item.get('baseprice', item.get('price', item.get('unitprice', 0)))
            cost = item.get('cost', item.get('unitcost', 0))
            category = item.get('category_name', item.get('categoryname', 'N/A'))
            active = "Active" if not item.get('inactive', False) else "Inactive"
            stock_level = item.get('stocklevel', item.get('stock_level', ''))

            stock_str = f" | Stock: {stock_level}" if stock_level != '' else ""
            results.append(f"**{name}** (ID: {item_id})\n  SKU: {sku} | Price: ${price:,.2f} | Cost: ${cost:,.2f}{stock_str}\n  Category: {category} | {active}")

        return f"Found {len(results)} item(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_item(
    item_id: int = Field(..., description="Item ID"),
    include_stock: bool = Field(True, description="Include stock level information")
) -> str:
    """Get detailed item information including pricing and stock."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()
        params = {"includedetails": "true"}
        if include_stock:
            params["includestock"] = "true"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{halopsa_config.resource_server}/Item/{item_id}",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            item = response.json()

        name = item.get('name', item.get('itemname', 'Unknown'))
        sku = item.get('sku', item.get('partnumber', 'N/A'))
        price = item.get('baseprice', item.get('price', item.get('unitprice', 0)))
        cost = item.get('cost', item.get('unitcost', 0))
        category = item.get('category_name', item.get('categoryname', 'N/A'))
        item_type = item.get('itemtype_name', item.get('type_name', 'N/A'))
        active = "Active" if not item.get('inactive', False) else "Inactive"

        # Stock information
        stock_level = item.get('stocklevel', item.get('stock_level', 'N/A'))
        reorder_level = item.get('reorderlevel', item.get('reorder_level', 'N/A'))

        # Supplier info
        supplier = item.get('supplier_name', item.get('suppliername', 'N/A'))
        manufacturer = item.get('manufacturer', item.get('manufacturer_name', 'N/A'))

        # Descriptions
        short_desc = item.get('shortdescription', item.get('short_description', ''))
        long_desc = item.get('longdescription', item.get('long_description', item.get('description', '')))

        # Tax
        tax_code = item.get('tax_code_name', item.get('taxcode', 'N/A'))

        # Accounting
        nominal_code = item.get('nominalcode', item.get('nominal_code', item.get('accountscode', 'N/A')))

        return f"""# Item: {name}

**ID:** {item.get('id')}
**SKU/Part Number:** {sku}
**Status:** {active}
**Type:** {item_type}
**Category:** {category}

## Pricing
**Selling Price:** ${price:,.2f}
**Cost Price:** ${cost:,.2f}
**Margin:** ${(price - cost):,.2f} ({((price - cost) / price * 100) if price > 0 else 0:.1f}%)
**Tax Code:** {tax_code}

## Stock
**Current Stock Level:** {stock_level}
**Reorder Level:** {reorder_level}

## Supplier & Manufacturer
**Supplier:** {supplier}
**Manufacturer:** {manufacturer}

## Description
{short_desc}

{long_desc if long_desc else ''}

## Accounting
**Nominal Code:** {nominal_code}"""
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_create_item(
    name: str = Field(..., description="Item name"),
    sku: Optional[str] = Field(None, description="SKU/Part number"),
    baseprice: float = Field(0, description="Selling price (ex tax)"),
    cost: float = Field(0, description="Cost price"),
    short_description: Optional[str] = Field(None, description="Short description"),
    long_description: Optional[str] = Field(None, description="Long/detailed description"),
    category_id: Optional[int] = Field(None, description="Category ID"),
    item_type_id: int = Field(1, description="Item type: 1=Stock, 2=Non-Stock, 3=Service, 4=Labour"),
    tax_code_id: int = Field(12, description="Tax code ID (default: 12 for GST)"),
    supplier_id: Optional[int] = Field(None, description="Supplier ID"),
    reorder_level: int = Field(0, description="Stock reorder level"),
    nominal_code: Optional[str] = Field(None, description="Accounting nominal code")
) -> str:
    """Create a new item in the HaloPSA catalog."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()

        payload = [{
            "name": name,
            "baseprice": baseprice,
            "cost": cost,
            "itemtype_id": item_type_id,
            "tax_code_id": tax_code_id,
            "reorderlevel": reorder_level,
            "inactive": False
        }]

        if sku:
            payload[0]["sku"] = sku
        if short_description:
            payload[0]["shortdescription"] = short_description
        if long_description:
            payload[0]["longdescription"] = long_description
        if category_id:
            payload[0]["category_id"] = category_id
        if supplier_id:
            payload[0]["supplier_id"] = supplier_id
        if nominal_code:
            payload[0]["nominalcode"] = nominal_code

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{halopsa_config.resource_server}/Item",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            result = response.json()

        new_id = result[0].get('id') if isinstance(result, list) and result else result.get('id', 'Unknown')
        return f"✅ Item created successfully.\n\n**ID:** {new_id}\n**Name:** {name}\n**SKU:** {sku or 'N/A'}\n**Price:** ${baseprice:,.2f}\n**Cost:** ${cost:,.2f}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_update_item(
    item_id: int = Field(..., description="Item ID to update"),
    name: Optional[str] = Field(None, description="New item name"),
    sku: Optional[str] = Field(None, description="New SKU/Part number"),
    baseprice: Optional[float] = Field(None, description="New selling price (ex tax)"),
    cost: Optional[float] = Field(None, description="New cost price"),
    short_description: Optional[str] = Field(None, description="New short description"),
    long_description: Optional[str] = Field(None, description="New long description"),
    category_id: Optional[int] = Field(None, description="New category ID"),
    inactive: Optional[bool] = Field(None, description="Set inactive status"),
    reorder_level: Optional[int] = Field(None, description="New stock reorder level")
) -> str:
    """Update an existing item in the HaloPSA catalog."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    # Check that at least one field is being updated
    if not any([name, sku, baseprice is not None, cost is not None, short_description,
                long_description, category_id, inactive is not None, reorder_level is not None]):
        return "Error: At least one field must be provided to update."

    try:
        token = await halopsa_config.get_access_token()

        payload = [{"id": item_id}]

        if name is not None:
            payload[0]["name"] = name
        if sku is not None:
            payload[0]["sku"] = sku
        if baseprice is not None:
            payload[0]["baseprice"] = baseprice
        if cost is not None:
            payload[0]["cost"] = cost
        if short_description is not None:
            payload[0]["shortdescription"] = short_description
        if long_description is not None:
            payload[0]["longdescription"] = long_description
        if category_id is not None:
            payload[0]["category_id"] = category_id
        if inactive is not None:
            payload[0]["inactive"] = inactive
        if reorder_level is not None:
            payload[0]["reorderlevel"] = reorder_level

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{halopsa_config.resource_server}/Item",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            result = response.json()

        updated_name = result[0].get('name', 'Unknown') if isinstance(result, list) and result else 'Unknown'
        return f"✅ Item #{item_id} updated successfully.\n**Name:** {updated_name}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def halopsa_delete_item(
    item_id: int = Field(..., description="Item ID to delete")
) -> str:
    """Delete an item from the HaloPSA catalog. This action cannot be undone."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{halopsa_config.resource_server}/Item/{item_id}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()

        return f"✅ Item #{item_id} deleted successfully."
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_item_categories(
    search: Optional[str] = Field(None, description="Search by category name"),
    limit: int = Field(50, description="Max results")
) -> str:
    """List item categories in HaloPSA."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}

        if search:
            params["search"] = search

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{halopsa_config.resource_server}/ItemCategory",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            categories = response.json().get("categories", response.json().get("itemcategories", []))

        if not categories:
            return "No item categories found."

        results = []
        for cat in categories[:limit]:
            cat_id = cat.get('id', 'N/A')
            name = cat.get('name', cat.get('categoryname', 'Unknown'))
            parent = cat.get('parent_name', cat.get('parentname', 'None'))

            results.append(f"**{name}** (ID: {cat_id})\n  Parent: {parent}")

        return f"Found {len(results)} category(ies):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def halopsa_get_item_stock(
    item_id: Optional[int] = Field(None, description="Filter by item ID"),
    warehouse_id: Optional[int] = Field(None, description="Filter by warehouse/location ID"),
    low_stock_only: bool = Field(False, description="Only show items below reorder level"),
    limit: int = Field(50, description="Max results")
) -> str:
    """Get stock levels for items. Can filter by item, warehouse, or show only low stock items."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()
        params = {"count": min(limit, 100)}

        if item_id:
            params["item_id"] = item_id
        if warehouse_id:
            params["warehouse_id"] = warehouse_id
        if low_stock_only:
            params["lowstock"] = "true"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{halopsa_config.resource_server}/ItemStock",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()
            stock_items = response.json().get("stocks", response.json().get("itemstocks", []))

        if not stock_items:
            return "No stock information found."

        results = []
        for s in stock_items[:limit]:
            item_name = s.get('item_name', s.get('itemname', 'Unknown'))
            stock_level = s.get('stocklevel', s.get('stock_level', s.get('qty', 0)))
            reorder = s.get('reorderlevel', s.get('reorder_level', 0))
            warehouse = s.get('warehouse_name', s.get('warehousename', s.get('location', 'Default')))
            item_id_val = s.get('item_id', s.get('itemid', 'N/A'))

            status = "⚠️ LOW" if stock_level < reorder else "✓"
            results.append(f"**{item_name}** (Item ID: {item_id_val})\n  Stock: {stock_level} | Reorder Level: {reorder} | Warehouse: {warehouse} {status}")

        return f"Found {len(results)} stock record(s):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False})
async def halopsa_adjust_item_stock(
    item_id: int = Field(..., description="Item ID"),
    quantity: int = Field(..., description="Quantity to add (positive) or remove (negative)"),
    warehouse_id: Optional[int] = Field(None, description="Warehouse/location ID (defaults to primary)"),
    note: str = Field("", description="Reason for stock adjustment")
) -> str:
    """Adjust stock level for an item (add or remove stock)."""
    if not halopsa_config.is_configured:
        return "Error: HaloPSA not configured."

    try:
        token = await halopsa_config.get_access_token()

        payload = [{
            "item_id": item_id,
            "quantity": quantity,
            "note": note or f"Stock adjustment: {'+' if quantity > 0 else ''}{quantity}"
        }]

        if warehouse_id:
            payload[0]["warehouse_id"] = warehouse_id

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{halopsa_config.resource_server}/ItemStock",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            )
            response.raise_for_status()

        action = "added to" if quantity > 0 else "removed from"
        return f"✅ Stock adjusted: {abs(quantity)} units {action} item #{item_id}."
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Xero Integration
# ============================================================================

class XeroConfig:
    def __init__(self):
        self.client_id = os.getenv("XERO_CLIENT_ID", "")
        self.client_secret = os.getenv("XERO_CLIENT_SECRET", "")
        self._tenant_id: Optional[str] = None  # Loaded on-demand from Secret Manager
        self._refresh_token: Optional[str] = None  # Loaded on-demand from Secret Manager
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    @property
    def tenant_id(self) -> str:
        """Get tenant ID from Secret Manager (with env var fallback)."""
        if self._tenant_id:
            return self._tenant_id
        # Try Secret Manager first
        tid = get_secret_sync("XERO_TENANT_ID")
        if tid:
            self._tenant_id = tid
            return tid
        # Fallback to environment variable
        self._tenant_id = os.getenv("XERO_TENANT_ID", "")
        return self._tenant_id

    @tenant_id.setter
    def tenant_id(self, value: str):
        self._tenant_id = value

    def _get_refresh_token(self) -> str:
        """Get refresh token from Secret Manager (with env var fallback)."""
        if self._refresh_token:
            return self._refresh_token
        # Try Secret Manager first for the latest token
        token = get_secret_sync("XERO_REFRESH_TOKEN")
        if token:
            self._refresh_token = token
            logger.info("Loaded Xero refresh token from Secret Manager")
            return token
        # Fallback to environment variable
        token = os.getenv("XERO_REFRESH_TOKEN", "")
        if token:
            self._refresh_token = token
            logger.info("Loaded Xero refresh token from environment variable")
        return token

    @property
    def is_configured(self) -> bool:
        return all([self.client_id, self.client_secret, self.tenant_id, self._get_refresh_token()])
    
    async def get_access_token(self) -> str:
        """Get valid access token, refreshing if needed."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        current_refresh_token = self._get_refresh_token()
        if not current_refresh_token:
            raise Exception("No Xero refresh token available. Run xero_auth_start to connect.")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://identity.xero.com/connect/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": current_refresh_token
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            if response.status_code >= 400:
                if response.status_code == 401:
                    raise Exception("Xero authentication expired or invalid. Run xero_auth_start to reconnect.")
                elif response.status_code == 400:
                    raise Exception("Xero token refresh failed. The refresh token may be invalid or expired. Run xero_auth_start to reconnect.")
                else:
                    raise Exception(f"Xero token refresh failed: {response.status_code} - {response.text}")
            data = response.json()

            self._access_token = data["access_token"]
            if "refresh_token" in data:
                new_refresh = data["refresh_token"]
                if new_refresh != current_refresh_token:
                    self._refresh_token = new_refresh
                    update_secret_sync("XERO_REFRESH_TOKEN", new_refresh)
                    logger.info("Xero refresh token rotated and saved to Secret Manager")

            expires_in = data.get("expires_in", 1800)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            return self._access_token

xero_config = XeroConfig()


def _check_xero_response(response: httpx.Response) -> Optional[str]:
    """
    Check Xero API response for errors and return a user-friendly error message.
    Returns None if the response is successful, otherwise returns an error string.
    """
    if response.status_code >= 400:
        # Try to extract error details from response
        try:
            error_data = response.json()
            if "Message" in error_data:
                return f"Xero API Error: {response.status_code} - {error_data['Message']}"
            elif "Detail" in error_data:
                return f"Xero API Error: {response.status_code} - {error_data['Detail']}"
            elif "Elements" in error_data:
                # Validation errors
                elements = error_data.get("Elements", [])
                if elements and "ValidationErrors" in elements[0]:
                    errors = [e.get("Message", "") for e in elements[0]["ValidationErrors"]]
                    return f"Xero API Error: {response.status_code} - {'; '.join(errors)}"
        except Exception:
            pass

        # Handle specific status codes
        if response.status_code == 401:
            return "Xero API Error: 401 - Authentication expired. Run xero_auth_start to reconnect."
        elif response.status_code == 403:
            return "Xero API Error: 403 - Access forbidden. Check your Xero app permissions."
        elif response.status_code == 404:
            return "Xero API Error: 404 - Resource not found."
        elif response.status_code == 429:
            return "Xero API Error: 429 - Rate limit exceeded. Please wait before retrying."

        return f"Xero API Error: {response.status_code} - {response.text}"
    return None


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
        if response.status_code >= 400:
            raise Exception(f"Xero API Error: {response.status_code} - {response.text}")
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            if response.status_code >= 400:
                return f"Xero API Error: {response.status_code} - {response.text}"
            tokens = response.json()

            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]

            tenant_response = await client.get(
                "https://api.xero.com/connections",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            if tenant_response.status_code >= 400:
                return f"Xero API Error: {tenant_response.status_code} - {tenant_response.text}"
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error

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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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
            error = _check_xero_response(response)
            if error:
                return error
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


# ============================================================================
# BigQuery Integration (Karisma RIS Data Warehouse)
# ============================================================================

class BigQueryConfig:
    """BigQuery configuration from environment variables.

    Environment variables:
    - BIGQUERY_PROJECT_ID: Default project for data/table references (e.g., 'crowdmcp')
    - BIGQUERY_JOB_PROJECT_ID: Project where query jobs run and are billed (optional, defaults to BIGQUERY_PROJECT_ID)
    - GOOGLE_APPLICATION_CREDENTIALS_JSON: Service account JSON for Cloud Run (optional, uses ADC if not set)

    Required IAM permissions:
    - The service account needs 'roles/bigquery.jobUser' (bigquery.jobs.create) on the JOB project
    - The service account needs 'roles/bigquery.dataViewer' on any projects containing data to query
    """
    def __init__(self):
        self.project_id = os.getenv("BIGQUERY_PROJECT_ID", "")
        # Job project is where queries run and billing happens - defaults to project_id
        # The service account MUST have bigquery.jobs.create permission on this project
        self.job_project_id = os.getenv("BIGQUERY_JOB_PROJECT_ID", "") or self.project_id
        self.credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
        self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(self.project_id)

    def get_client(self):
        """Get or create BigQuery client with proper credentials.

        The client is initialized with job_project_id to ensure queries run
        in a project where the service account has bigquery.jobs.create permission.
        """
        if self._client is None:
            try:
                from google.cloud import bigquery

                if self.credentials_json:
                    # Parse credentials from environment variable (for Cloud Run)
                    from google.oauth2 import service_account
                    credentials_info = json.loads(self.credentials_json)
                    credentials = service_account.Credentials.from_service_account_info(credentials_info)
                    # Use job_project_id for client - this is where jobs are created/billed
                    self._client = bigquery.Client(project=self.job_project_id, credentials=credentials)
                else:
                    # Use Application Default Credentials
                    self._client = bigquery.Client(project=self.job_project_id)
            except ImportError:
                raise ImportError("google-cloud-bigquery package not installed")

        return self._client

bigquery_config = BigQueryConfig()


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
# BigQuery Tools - Karisma RIS Data Warehouse
# ============================================================================

@mcp.tool()
async def bigquery_query(
    sql: str = Field(..., description="SQL query to execute against BigQuery"),
    max_results: int = Field(100, description="Maximum rows to return (1-1000)")
) -> str:
    """
    Execute a SQL query against BigQuery and return results as markdown table.
    Use this for querying RIS data from Karisma radiology databases.

    Cross-Project Queries:
    - Jobs run in BIGQUERY_JOB_PROJECT_ID (requires bigquery.jobs.create permission)
    - Data can be queried from any project using fully qualified table names: `project.dataset.table`
    - Use fully qualified names when querying other projects (e.g., `vision-radiology.dataset.table`)

    Common datasets in crowdmcp project:
    - karisma_warehouse: Radiology data synced from Karisma RIS systems

    Karisma RIS Data - Key Tables and Views:

    Pre-built Views (recommended):
    - vw_Sonographer_Services: Sonographer activity with worksite, service, patient details
    - vw_WorkSite_Revenue: Invoice-level revenue by worksite and date
    - vw_Radiologist_Revenue: Invoice item-level revenue by radiologist and worksite
    - vw_Referrer_Activity: Referring doctor activity with patient counts
    - vw_Study_Types: Study breakdown by modality, department, worksite

    Core Tables:
    - Data_Request: Patient visits/requests
    - Data_RequestService: Services within a request
    - Data_WorkSite: Site/location information
    - Data_Practitioner: Radiologists/doctors (reporting providers)
    - Data_ResourceInstance: Sonographers/technicians
    - Data_Report: Radiology reports
    - Data_Invoice: Billing records

    Example queries:
    - SELECT * FROM `crowdmcp.karisma_warehouse.vw_Sonographer_Services` WHERE SonographerName = 'Name' LIMIT 10
    - SELECT WorkSiteName, COUNT(*) as Studies FROM `crowdmcp.karisma_warehouse.vw_Study_Types` GROUP BY WorkSiteName
    - SELECT RadiologistName, SUM(ItemCharged) as Revenue FROM `crowdmcp.karisma_warehouse.vw_Radiologist_Revenue` GROUP BY RadiologistName
    """
    if not bigquery_config.is_configured:
        return "Error: BigQuery is not configured. Set BIGQUERY_PROJECT_ID environment variable."

    try:
        client = bigquery_config.get_client()

        # Execute query with timeout
        query_job = client.query(sql)
        results = query_job.result(timeout=120)  # 2 minute timeout

        # Format results
        rows = list(results)[:min(max_results, 1000)]

        if not rows:
            return "Query executed successfully. No results returned."

        # Get column names from schema
        columns = [field.name for field in results.schema]

        # Format as markdown table
        output = ["| " + " | ".join(columns) + " |"]
        output.append("| " + " | ".join(["---"] * len(columns)) + " |")

        for row in rows:
            values = []
            for col in columns:
                val = row[col]
                if val is None:
                    values.append("NULL")
                else:
                    str_val = str(val)
                    # Truncate long values for readability
                    values.append(str_val[:60] + "..." if len(str_val) > 60 else str_val)
            output.append("| " + " | ".join(values) + " |")

        # Include query stats
        bytes_processed = query_job.total_bytes_processed or 0
        mb_processed = bytes_processed / 1024 / 1024

        return f"Query returned {len(rows)} row(s) (processed {mb_processed:.2f} MB):\n\n" + "\n".join(output)

    except ImportError:
        return "Error: google-cloud-bigquery package not installed. Add to pyproject.toml dependencies."
    except Exception as e:
        logger.error(f"BigQuery query error: {e}")
        return f"BigQuery error: {str(e)}"


@mcp.tool()
async def bigquery_list_datasets() -> str:
    """
    List all datasets in the BigQuery project.
    Use this to discover available data sources.
    """
    if not bigquery_config.is_configured:
        return "Error: BigQuery is not configured. Set BIGQUERY_PROJECT_ID environment variable."

    try:
        client = bigquery_config.get_client()
        datasets = list(client.list_datasets())

        if not datasets:
            return f"No datasets found in project '{bigquery_config.project_id}'."

        output = [f"# Datasets in {bigquery_config.project_id}\n"]
        for dataset in datasets:
            output.append(f"- **{dataset.dataset_id}**")

        output.append(f"\nTotal: {len(datasets)} dataset(s)")
        return "\n".join(output)

    except ImportError:
        return "Error: google-cloud-bigquery package not installed."
    except Exception as e:
        logger.error(f"BigQuery list datasets error: {e}")
        return f"BigQuery error: {str(e)}"


@mcp.tool()
async def bigquery_list_tables(
    dataset: str = Field(..., description="Dataset name to list tables from (e.g., 'karisma_warehouse')")
) -> str:
    """
    List all tables in a BigQuery dataset with row counts and sizes.
    """
    if not bigquery_config.is_configured:
        return "Error: BigQuery is not configured. Set BIGQUERY_PROJECT_ID environment variable."

    try:
        client = bigquery_config.get_client()

        dataset_ref = f"{bigquery_config.project_id}.{dataset}"
        tables = list(client.list_tables(dataset_ref))

        if not tables:
            return f"No tables found in dataset '{dataset}'."

        output = [
            f"# Tables in {dataset}\n",
            "| Table | Type | Rows | Size |",
            "| --- | --- | --- | --- |"
        ]

        for table in tables:
            # Get full table info for row count and size
            try:
                full_table = client.get_table(table)
                rows = f"{full_table.num_rows:,}" if full_table.num_rows else "?"
                size = f"{full_table.num_bytes / 1024 / 1024:.1f} MB" if full_table.num_bytes else "?"
            except:
                rows = "?"
                size = "?"

            output.append(f"| {table.table_id} | {table.table_type} | {rows} | {size} |")

        output.append(f"\nTotal: {len(tables)} table(s)")
        return "\n".join(output)

    except ImportError:
        return "Error: google-cloud-bigquery package not installed."
    except Exception as e:
        logger.error(f"BigQuery list tables error: {e}")
        return f"BigQuery error: {str(e)}"


@mcp.tool()
async def bigquery_describe_table(
    dataset: str = Field(..., description="Dataset name"),
    table: str = Field(..., description="Table name to describe")
) -> str:
    """
    Get detailed schema and metadata for a BigQuery table.
    Shows all columns with their types, modes, and descriptions.
    """
    if not bigquery_config.is_configured:
        return "Error: BigQuery is not configured. Set BIGQUERY_PROJECT_ID environment variable."

    try:
        client = bigquery_config.get_client()

        table_ref = f"{bigquery_config.project_id}.{dataset}.{table}"
        table_obj = client.get_table(table_ref)

        output = [
            f"# {dataset}.{table}\n",
            f"**Type:** {table_obj.table_type}",
        ]

        if table_obj.num_rows is not None:
            output.append(f"**Rows:** {table_obj.num_rows:,}")
        if table_obj.num_bytes is not None:
            output.append(f"**Size:** {table_obj.num_bytes / 1024 / 1024:.2f} MB")
        if table_obj.created:
            output.append(f"**Created:** {table_obj.created.strftime('%Y-%m-%d %H:%M')}")
        if table_obj.modified:
            output.append(f"**Modified:** {table_obj.modified.strftime('%Y-%m-%d %H:%M')}")

        output.extend([
            "\n## Schema\n",
            "| Column | Type | Mode | Description |",
            "| --- | --- | --- | --- |"
        ])

        for field in table_obj.schema:
            desc = (field.description or "")[:60]
            output.append(f"| {field.name} | {field.field_type} | {field.mode} | {desc} |")

        output.append(f"\nTotal: {len(table_obj.schema)} column(s)")
        return "\n".join(output)

    except ImportError:
        return "Error: google-cloud-bigquery package not installed."
    except Exception as e:
        logger.error(f"BigQuery describe table error: {e}")
        return f"BigQuery error: {str(e)}"


@mcp.tool()
async def bigquery_sample_data(
    dataset: str = Field(..., description="Dataset name"),
    table: str = Field(..., description="Table name"),
    limit: int = Field(5, description="Number of sample rows (1-20)")
) -> str:
    """
    Get sample data from a BigQuery table.
    Quick way to preview table contents without writing SQL.
    """
    if not bigquery_config.is_configured:
        return "Error: BigQuery is not configured."

    limit = min(max(1, limit), 20)
    sql = f"SELECT * FROM `{bigquery_config.project_id}.{dataset}.{table}` LIMIT {limit}"

    return await bigquery_query(sql=sql, max_results=limit)


# ============================================================================
# FortiCloud Integration (FortiGate Cloud, FortiManager, FortiAnalyzer, etc.)
# ============================================================================

class FortiCloudConfig:
    """FortiCloud API configuration using OAuth2 authentication.

    Environment variables:
    - FORTICLOUD_API_KEY: API Key (username) from FortiCloud IAM
    - FORTICLOUD_API_SECRET: API Secret (password) from FortiCloud IAM
    - FORTICLOUD_CLIENT_ID: Client ID for the specific service (default: fortigatecloud)

    Available client IDs:
    - fortigatecloud: FortiGate Cloud
    - FortiManager: FortiManager Cloud
    - FortiAnalyzer: FortiAnalyzer Cloud
    - FortiSASE: FortiSASE Cloud
    - fortipresence: FortiPresence Cloud
    - fortiztp: FortiZTP Cloud
    - forticameracloud: FortiCamera Cloud
    - assetmanagement: Asset Management Cloud
    - iam: IAM Cloud

    Required IAM permissions:
    - The API user needs appropriate permissions for the services being accessed
    """
    def __init__(self):
        self.api_key = os.getenv("FORTICLOUD_API_KEY", "")
        self.api_secret = os.getenv("FORTICLOUD_API_SECRET", "")
        self.client_id = os.getenv("FORTICLOUD_CLIENT_ID", "fortigatecloud")
        self.auth_url = "https://customerapiauth.fortinet.com/api/v1/oauth/token/"
        self.fortigate_cloud_url = "https://www.forticloud.com/forticloudapi/v1"
        self._access_token = None
        self._token_expiry = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.api_secret)

    async def get_access_token(self) -> str:
        """Get or refresh FortiCloud access token."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.auth_url,
                json={
                    "username": self.api_key,
                    "password": self.api_secret,
                    "client_id": self.client_id,
                    "grant_type": "password"
                },
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            # Token expires in ~4 hours (14400 seconds), refresh 5 mins early
            expires_in = data.get("expires_in", 14400)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 300)

            return self._access_token

    async def api_request(self, method: str, endpoint: str, params: dict = None, json_data: dict = None) -> dict:
        """Make authenticated request to FortiCloud API."""
        token = await self.get_access_token()

        async with httpx.AsyncClient() as client:
            url = f"{self.fortigate_cloud_url}/{endpoint.lstrip('/')}"
            response = await client.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()


forticloud_config = FortiCloudConfig()


@mcp.tool(annotations={"readOnlyHint": True})
async def forticloud_list_devices(
    status: str = Field("all", description="Filter by status: all, online, offline")
) -> str:
    """
    List all FortiGate devices registered in FortiGate Cloud.
    Shows device name, serial number, firmware version, and online status.
    """
    if not forticloud_config.is_configured:
        return "Error: FortiCloud is not configured. Set FORTICLOUD_API_KEY and FORTICLOUD_API_SECRET."

    try:
        data = await forticloud_config.api_request("GET", "/devices")

        devices = data.get("result", data.get("devices", []))
        if not devices:
            return "No FortiGate devices found in FortiCloud."

        # Filter by status if specified
        if status == "online":
            devices = [d for d in devices if d.get("online", False)]
        elif status == "offline":
            devices = [d for d in devices if not d.get("online", False)]

        output = ["# FortiGate Cloud Devices\n"]
        output.append("| Device Name | Serial Number | Model | Firmware | Status |")
        output.append("| --- | --- | --- | --- | --- |")

        for device in devices:
            name = device.get("name", device.get("hostname", "Unknown"))
            sn = device.get("sn", device.get("serial", "N/A"))
            model = device.get("model", device.get("platform", "N/A"))
            firmware = device.get("firmware", device.get("os_version", "N/A"))
            online = "Online" if device.get("online", False) else "Offline"

            output.append(f"| {name} | {sn} | {model} | {firmware} | {online} |")

        output.append(f"\nTotal: {len(devices)} device(s)")
        return "\n".join(output)

    except httpx.HTTPStatusError as e:
        logger.error(f"FortiCloud API error: {e}")
        return f"FortiCloud API error: {e.response.status_code} - {e.response.text[:100]}"
    except Exception as e:
        logger.error(f"FortiCloud error: {e}")
        return f"FortiCloud error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def forticloud_device_details(
    serial_number: str = Field(..., description="FortiGate device serial number")
) -> str:
    """
    Get detailed information about a specific FortiGate device.
    Includes system info, interfaces, licenses, and configuration status.
    """
    if not forticloud_config.is_configured:
        return "Error: FortiCloud is not configured."

    try:
        data = await forticloud_config.api_request("GET", f"/devices/{serial_number}")

        device = data.get("result", data)
        if not device:
            return f"Device {serial_number} not found."

        output = [f"# FortiGate Device: {device.get('name', serial_number)}\n"]

        # Basic info
        output.append("## System Information")
        output.append(f"- **Serial Number:** {device.get('sn', serial_number)}")
        output.append(f"- **Model:** {device.get('model', 'N/A')}")
        output.append(f"- **Firmware:** {device.get('firmware', 'N/A')}")
        output.append(f"- **Status:** {'Online' if device.get('online') else 'Offline'}")

        if device.get('last_seen'):
            output.append(f"- **Last Seen:** {device.get('last_seen')}")

        # Network info
        if device.get('mgmt_ip'):
            output.append(f"- **Management IP:** {device.get('mgmt_ip')}")
        if device.get('public_ip'):
            output.append(f"- **Public IP:** {device.get('public_ip')}")

        # License info
        if device.get('license'):
            output.append("\n## License Status")
            license_info = device.get('license', {})
            output.append(f"- **Type:** {license_info.get('type', 'N/A')}")
            output.append(f"- **Expiry:** {license_info.get('expiry', 'N/A')}")

        # Features/services
        if device.get('features') or device.get('services'):
            output.append("\n## Enabled Services")
            for feature in device.get('features', device.get('services', [])):
                if isinstance(feature, dict):
                    output.append(f"- {feature.get('name', feature)}: {feature.get('status', 'N/A')}")
                else:
                    output.append(f"- {feature}")

        return "\n".join(output)

    except httpx.HTTPStatusError as e:
        return f"FortiCloud API error: {e.response.status_code}"
    except Exception as e:
        return f"FortiCloud error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def forticloud_device_alerts(
    serial_number: str = Field(None, description="Filter by device serial (optional, shows all if not specified)"),
    severity: str = Field("all", description="Filter by severity: all, critical, warning, info"),
    limit: int = Field(50, description="Maximum alerts to return (1-200)")
) -> str:
    """
    Get alerts and notifications from FortiGate Cloud.
    Shows security events, system alerts, and configuration changes.
    """
    if not forticloud_config.is_configured:
        return "Error: FortiCloud is not configured."

    try:
        params = {"limit": min(max(1, limit), 200)}
        if serial_number:
            params["sn"] = serial_number

        data = await forticloud_config.api_request("GET", "/alerts", params=params)

        alerts = data.get("result", data.get("alerts", []))
        if not alerts:
            return "No alerts found."

        # Filter by severity if specified
        if severity != "all":
            alerts = [a for a in alerts if a.get("severity", "").lower() == severity.lower()]

        output = ["# FortiCloud Alerts\n"]
        output.append("| Time | Device | Severity | Message |")
        output.append("| --- | --- | --- | --- |")

        for alert in alerts[:limit]:
            time = alert.get("time", alert.get("timestamp", "N/A"))
            device = alert.get("device_name", alert.get("sn", "N/A"))
            sev = alert.get("severity", "info").upper()
            msg = alert.get("message", alert.get("description", "N/A"))[:60]

            output.append(f"| {time} | {device} | {sev} | {msg} |")

        output.append(f"\nShowing {min(len(alerts), limit)} of {len(alerts)} alert(s)")
        return "\n".join(output)

    except httpx.HTTPStatusError as e:
        return f"FortiCloud API error: {e.response.status_code}"
    except Exception as e:
        return f"FortiCloud error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def forticloud_device_logs(
    serial_number: str = Field(..., description="FortiGate device serial number"),
    log_type: str = Field("traffic", description="Log type: traffic, event, utm, vpn"),
    limit: int = Field(50, description="Maximum logs to return (1-500)")
) -> str:
    """
    Get logs from a specific FortiGate device via FortiCloud.
    Supports traffic logs, event logs, UTM logs, and VPN logs.
    """
    if not forticloud_config.is_configured:
        return "Error: FortiCloud is not configured."

    try:
        params = {
            "type": log_type,
            "limit": min(max(1, limit), 500)
        }

        data = await forticloud_config.api_request("GET", f"/devices/{serial_number}/logs", params=params)

        logs = data.get("result", data.get("logs", []))
        if not logs:
            return f"No {log_type} logs found for device {serial_number}."

        output = [f"# {log_type.title()} Logs - {serial_number}\n"]

        # Format based on log type
        if log_type == "traffic":
            output.append("| Time | Src IP | Dst IP | Action | Bytes |")
            output.append("| --- | --- | --- | --- | --- |")
            for log in logs[:limit]:
                time = log.get("date", "") + " " + log.get("time", "")
                src = log.get("srcip", "N/A")
                dst = log.get("dstip", "N/A")
                action = log.get("action", "N/A")
                bytes_sent = log.get("sentbyte", 0)
                output.append(f"| {time} | {src} | {dst} | {action} | {bytes_sent} |")
        else:
            output.append("| Time | Type | Level | Message |")
            output.append("| --- | --- | --- | --- |")
            for log in logs[:limit]:
                time = log.get("date", "") + " " + log.get("time", "")
                log_type_val = log.get("type", log.get("subtype", "N/A"))
                level = log.get("level", "N/A")
                msg = log.get("msg", log.get("message", "N/A"))[:50]
                output.append(f"| {time} | {log_type_val} | {level} | {msg} |")

        output.append(f"\nShowing {min(len(logs), limit)} of {len(logs)} log(s)")
        return "\n".join(output)

    except httpx.HTTPStatusError as e:
        return f"FortiCloud API error: {e.response.status_code}"
    except Exception as e:
        return f"FortiCloud error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def forticloud_device_config(
    serial_number: str = Field(..., description="FortiGate device serial number"),
    path: str = Field("system/global", description="Config path (e.g., 'system/global', 'firewall/policy', 'vpn/ipsec/phase1-interface')")
) -> str:
    """
    Get configuration from a FortiGate device via FortiCloud proxy.
    This proxies requests to the FortiOS REST API through FortiCloud.

    Common config paths:
    - system/global: Global system settings
    - system/interface: Network interfaces
    - firewall/policy: Firewall policies
    - firewall/address: Address objects
    - vpn/ipsec/phase1-interface: VPN phase1 tunnels
    - router/static: Static routes
    """
    if not forticloud_config.is_configured:
        return "Error: FortiCloud is not configured."

    try:
        # FortiCloud proxies FortiOS API calls via /fgt/<SN>/api/v2/cmdb/<path>
        endpoint = f"/fgt/{serial_number}/api/v2/cmdb/{path}"
        data = await forticloud_config.api_request("GET", endpoint)

        results = data.get("results", data.get("result", []))
        if not results:
            return f"No configuration found at path: {path}"

        output = [f"# FortiGate Config: {path}\n"]
        output.append(f"**Device:** {serial_number}\n")

        # Format as JSON-like output
        if isinstance(results, list):
            for i, item in enumerate(results[:20], 1):  # Limit to 20 items
                output.append(f"## Entry {i}")
                if isinstance(item, dict):
                    for key, value in item.items():
                        if isinstance(value, (dict, list)):
                            output.append(f"- **{key}:** (complex object)")
                        else:
                            output.append(f"- **{key}:** {value}")
                else:
                    output.append(f"- {item}")
                output.append("")
        elif isinstance(results, dict):
            for key, value in results.items():
                if isinstance(value, (dict, list)):
                    output.append(f"- **{key}:** (complex object)")
                else:
                    output.append(f"- **{key}:** {value}")

        return "\n".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Config path not found: {path}"
        return f"FortiCloud API error: {e.response.status_code}"
    except Exception as e:
        return f"FortiCloud error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def forticloud_vpn_status(
    serial_number: str = Field(..., description="FortiGate device serial number")
) -> str:
    """
    Get VPN tunnel status from a FortiGate device.
    Shows IPsec and SSL VPN connections.
    """
    if not forticloud_config.is_configured:
        return "Error: FortiCloud is not configured."

    try:
        output = [f"# VPN Status - {serial_number}\n"]

        # Try to get IPsec tunnel status
        try:
            ipsec_data = await forticloud_config.api_request(
                "GET",
                f"/fgt/{serial_number}/api/v2/monitor/vpn/ipsec"
            )
            ipsec_tunnels = ipsec_data.get("results", [])

            output.append("## IPsec Tunnels\n")
            if ipsec_tunnels:
                output.append("| Name | Status | Remote GW | Incoming | Outgoing |")
                output.append("| --- | --- | --- | --- | --- |")
                for tunnel in ipsec_tunnels:
                    name = tunnel.get("name", "N/A")
                    status = "Up" if tunnel.get("status") == "up" else "Down"
                    remote = tunnel.get("rgwy", tunnel.get("remote_gateway", "N/A"))
                    incoming = tunnel.get("incoming_bytes", 0)
                    outgoing = tunnel.get("outgoing_bytes", 0)
                    output.append(f"| {name} | {status} | {remote} | {incoming} | {outgoing} |")
            else:
                output.append("No IPsec tunnels configured.")

        except Exception as e:
            output.append(f"Could not retrieve IPsec status: {str(e)[:50]}")

        # Try to get SSL VPN status
        try:
            ssl_data = await forticloud_config.api_request(
                "GET",
                f"/fgt/{serial_number}/api/v2/monitor/vpn/ssl"
            )
            ssl_tunnels = ssl_data.get("results", [])

            output.append("\n## SSL VPN Connections\n")
            if ssl_tunnels:
                output.append("| User | Source IP | Duration | Bytes In | Bytes Out |")
                output.append("| --- | --- | --- | --- | --- |")
                for conn in ssl_tunnels:
                    user = conn.get("user_name", conn.get("user", "N/A"))
                    src_ip = conn.get("remote_host", "N/A")
                    duration = conn.get("duration", "N/A")
                    bytes_in = conn.get("bytes_in", 0)
                    bytes_out = conn.get("bytes_out", 0)
                    output.append(f"| {user} | {src_ip} | {duration} | {bytes_in} | {bytes_out} |")
            else:
                output.append("No active SSL VPN connections.")

        except Exception as e:
            output.append(f"Could not retrieve SSL VPN status: {str(e)[:50]}")

        return "\n".join(output)

    except Exception as e:
        return f"FortiCloud error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def forticloud_system_status(
    serial_number: str = Field(..., description="FortiGate device serial number")
) -> str:
    """
    Get system resource status from a FortiGate device.
    Shows CPU, memory, disk usage, uptime, and session counts.
    """
    if not forticloud_config.is_configured:
        return "Error: FortiCloud is not configured."

    try:
        output = [f"# System Status - {serial_number}\n"]

        # Get system status
        try:
            status_data = await forticloud_config.api_request(
                "GET",
                f"/fgt/{serial_number}/api/v2/monitor/system/status"
            )
            status = status_data.get("results", status_data)

            output.append("## System Information")
            output.append(f"- **Hostname:** {status.get('hostname', 'N/A')}")
            output.append(f"- **Serial:** {status.get('serial', serial_number)}")
            output.append(f"- **Model:** {status.get('model', 'N/A')}")
            output.append(f"- **Firmware:** {status.get('version', 'N/A')}")
            output.append(f"- **Uptime:** {status.get('uptime', 'N/A')}")

        except Exception as e:
            output.append(f"Could not retrieve system status: {str(e)[:50]}")

        # Get resource usage
        try:
            perf_data = await forticloud_config.api_request(
                "GET",
                f"/fgt/{serial_number}/api/v2/monitor/system/resource/usage"
            )
            perf = perf_data.get("results", {})

            output.append("\n## Resource Usage")
            cpu = perf.get("cpu", [{}])
            if cpu:
                cpu_usage = cpu[0].get("current", cpu if isinstance(cpu, (int, float)) else "N/A")
                output.append(f"- **CPU:** {cpu_usage}%")

            mem = perf.get("mem", {})
            if mem:
                mem_used = mem.get("used", "N/A")
                mem_total = mem.get("total", "N/A")
                output.append(f"- **Memory:** {mem_used} / {mem_total}")

            disk = perf.get("disk", {})
            if disk:
                disk_used = disk.get("used", "N/A")
                disk_total = disk.get("total", "N/A")
                output.append(f"- **Disk:** {disk_used} / {disk_total}")

        except Exception as e:
            output.append(f"Could not retrieve resource usage: {str(e)[:50]}")

        # Get session info
        try:
            session_data = await forticloud_config.api_request(
                "GET",
                f"/fgt/{serial_number}/api/v2/monitor/system/session/info"
            )
            sessions = session_data.get("results", {})

            output.append("\n## Session Statistics")
            output.append(f"- **Active Sessions:** {sessions.get('session_count', 'N/A')}")
            output.append(f"- **Session Limit:** {sessions.get('session_limit', 'N/A')}")

        except Exception as e:
            pass  # Session info not critical

        return "\n".join(output)

    except Exception as e:
        return f"FortiCloud error: {str(e)}"


# ============================================================================
# Maxotel VoIP Integration
# ============================================================================

class MaxotelConfig:
    """Maxotel API configuration using username + API key authentication."""
    def __init__(self):
        self.username = os.getenv("MAXOTEL_USERNAME", "")
        self.api_key = os.getenv("MAXOTEL_API_KEY", "")
        self.base_url = "https://api.maxo.com.au/wla/"

    @property
    def is_configured(self) -> bool:
        return bool(self.username) and bool(self.api_key)

    def get_base_params(self) -> dict:
        """Get base query parameters for all API requests."""
        return {
            "user": self.username,
            "key": self.api_key
        }

maxotel_config = MaxotelConfig()


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def maxotel_get_cdr(
    start_date: str = Field(..., description="Start date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    end_date: str = Field(..., description="End date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    charges_only: bool = Field(False, description="If true, only include calls with charges"),
    connected_only: bool = Field(False, description="If true, only include connected calls"),
    accref: Optional[str] = Field(None, description="Filter by account/billing reference"),
    client_id: Optional[str] = Field(None, description="Filter by MaxoTel client ID"),
    cust_id: Optional[str] = Field(None, description="Filter by customer ID"),
    limit: int = Field(100, description="Maximum number of records to return")
) -> str:
    """
    Get Call Detail Records (CDR) from Maxotel VoIP system.
    Returns call history including direction, duration, origin, destination, and costs.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    try:
        # Parse dates to unix timestamps
        from datetime import datetime as dt

        def parse_to_unix(date_str: str) -> int:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                try:
                    return int(dt.strptime(date_str, fmt).timestamp())
                except ValueError:
                    continue
            raise ValueError(f"Invalid date format: {date_str}")

        start_unix = parse_to_unix(start_date)
        end_unix = parse_to_unix(end_date)

        params = maxotel_config.get_base_params()
        params.update({
            "action": "getcdr",
            "chargesonly": "1" if charges_only else "0",
            "start": str(start_unix),
            "end": str(end_unix)
        })

        if connected_only:
            params["connectedonly"] = "1"
        if accref:
            params["accref"] = accref
        if client_id:
            params["clientid"] = client_id
        if cust_id:
            params["custid"] = cust_id

        async with httpx.AsyncClient() as client:
            response = await client.get(maxotel_config.base_url, params=params, timeout=60.0)
            response.raise_for_status()
            data = response.json()

        if data.get("response") == "ERROR":
            return f"Maxotel API Error: {data.get('response_text', 'Unknown error')}"

        response_data = data.get("response_data", {})
        call_count = response_data.get("Calls", 0)
        calls = response_data.get("call_data", [])

        if not calls:
            return "No call records found for the specified period."

        # Limit results
        calls = calls[:limit]

        # Format as markdown table
        output = [
            f"# Call Detail Records\n",
            f"**Period:** {start_date} to {end_date}",
            f"**Total Calls:** {call_count}\n",
            "| Date/Time | Direction | Origin | Destination | Duration | Status | Cost |",
            "| --- | --- | --- | --- | --- | --- | --- |"
        ]

        for call in calls:
            datetime_str = call.get("datetime", "N/A")
            direction = call.get("direction", "N/A")
            origin = call.get("origin", "N/A")
            destination = call.get("destination", "N/A")
            duration = call.get("duration_2", call.get("duration", "N/A"))
            status = call.get("status", "N/A")
            cost = f"${float(call.get('cost', 0)):.2f}" if call.get("cost") else "$0.00"

            output.append(f"| {datetime_str} | {direction} | {origin} | {destination} | {duration} | {status} | {cost} |")

        if len(calls) < call_count:
            output.append(f"\n*Showing {len(calls)} of {call_count} records*")

        return "\n".join(output)
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"Maxotel CDR error: {e}")
        return f"Maxotel error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def maxotel_get_cdr_csv(
    start_date: str = Field(..., description="Start date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    end_date: str = Field(..., description="End date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    charges_only: bool = Field(False, description="If true, only include calls with charges"),
    connected_only: bool = Field(False, description="If true, only include connected calls"),
    accref: Optional[str] = Field(None, description="Filter by account/billing reference"),
    include_headings: bool = Field(True, description="Include header row in CSV output")
) -> str:
    """
    Export Call Detail Records (CDR) as CSV format from Maxotel VoIP system.
    Useful for bulk data export and analysis.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    try:
        from datetime import datetime as dt

        def parse_to_unix(date_str: str) -> int:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                try:
                    return int(dt.strptime(date_str, fmt).timestamp())
                except ValueError:
                    continue
            raise ValueError(f"Invalid date format: {date_str}")

        start_unix = parse_to_unix(start_date)
        end_unix = parse_to_unix(end_date)

        params = maxotel_config.get_base_params()
        params.update({
            "action": "getcdrcsv",
            "chargesonly": "1" if charges_only else "0",
            "start": str(start_unix),
            "end": str(end_unix)
        })

        if connected_only:
            params["connectedonly"] = "1"
        if accref:
            params["accref"] = accref
        if include_headings:
            params["showheadings"] = "1"

        async with httpx.AsyncClient() as client:
            response = await client.get(maxotel_config.base_url, params=params, timeout=60.0)
            response.raise_for_status()

        csv_content = response.text
        if not csv_content.strip():
            return "No call records found for the specified period."

        # Return CSV with markdown code block formatting
        lines = csv_content.strip().split('\n')
        return f"# CDR Export (CSV)\n\n**Period:** {start_date} to {end_date}\n**Records:** {len(lines) - (1 if include_headings else 0)}\n\n```csv\n{csv_content}\n```"
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"Maxotel CDR CSV error: {e}")
        return f"Maxotel error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def maxotel_get_customer_transactions(
    start_date: str = Field(..., description="Start date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    end_date: str = Field(..., description="End date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    accref: Optional[str] = Field(None, description="Filter by account/billing reference"),
    client_id: Optional[str] = Field(None, description="Filter by MaxoTel client ID"),
    cust_id: Optional[str] = Field(None, description="Filter by customer ID"),
    subscriptions_only: bool = Field(False, description="Show only subscription-related transactions"),
    payments_only: bool = Field(False, description="Show only payment transactions"),
    as_csv: bool = Field(False, description="Return results as CSV format")
) -> str:
    """
    Get customer transaction details from Maxotel.
    Includes payments, subscriptions, and other account transactions.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    try:
        from datetime import datetime as dt

        def parse_to_unix(date_str: str) -> int:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                try:
                    return int(dt.strptime(date_str, fmt).timestamp())
                except ValueError:
                    continue
            raise ValueError(f"Invalid date format: {date_str}")

        start_unix = parse_to_unix(start_date)
        end_unix = parse_to_unix(end_date)

        params = maxotel_config.get_base_params()
        params.update({
            "action": "getCustTxns",
            "start": str(start_unix),
            "end": str(end_unix)
        })

        if accref:
            params["accref"] = accref
        if client_id:
            params["clientid"] = client_id
        if cust_id:
            params["custid"] = cust_id
        if subscriptions_only:
            params["subscriptionsonly"] = "1"
        if payments_only:
            params["paymentsonly"] = "1"
        if as_csv:
            params["getcsv"] = "1"
            params["showheadings"] = "1"

        async with httpx.AsyncClient() as client:
            response = await client.get(maxotel_config.base_url, params=params, timeout=60.0)
            response.raise_for_status()

        if as_csv:
            csv_content = response.text
            if not csv_content.strip():
                return "No transactions found for the specified period."
            lines = csv_content.strip().split('\n')
            return f"# Customer Transactions (CSV)\n\n**Period:** {start_date} to {end_date}\n**Records:** {len(lines) - 1}\n\n```csv\n{csv_content}\n```"

        data = response.json()

        if data.get("response") == "ERROR":
            return f"Maxotel API Error: {data.get('response_text', 'Unknown error')}"

        response_data = data.get("response_data", {})
        txn_count = response_data.get("Transactions", 0)
        transactions = response_data.get("transaction_data", [])

        if not transactions:
            return "No transactions found for the specified period."

        output = [
            f"# Customer Transactions\n",
            f"**Period:** {start_date} to {end_date}",
            f"**Total Transactions:** {txn_count}\n",
            "| Date/Time | Description | Type | Period | Amount |",
            "| --- | --- | --- | --- | --- |"
        ]

        total_amount = 0.0
        for txn in transactions:
            datetime_str = txn.get("datetime", "N/A")
            description = txn.get("description", "N/A")[:50]

            txn_type = []
            if txn.get("payment") == "1":
                txn_type.append("Payment")
            if txn.get("subscription") == "1":
                txn_type.append("Subscription")
            type_str = ", ".join(txn_type) if txn_type else "Other"

            period = txn.get("period", "-")
            amount = float(txn.get("amount", 0))
            total_amount += amount

            output.append(f"| {datetime_str} | {description} | {type_str} | {period} | ${amount:.2f} |")

        output.append(f"\n**Total Amount:** ${total_amount:.2f}")
        return "\n".join(output)
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"Maxotel customer transactions error: {e}")
        return f"Maxotel error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def maxotel_get_invoices(
    month: int = Field(..., description="Month (1-12) the invoice was raised"),
    year: int = Field(..., description="Year (YYYY) the invoice was raised"),
    include_unpaid: bool = Field(False, description="Include unpaid invoices from any period")
) -> str:
    """
    Get invoice details from Maxotel VoIP system.
    Returns invoices for a specific billing month including amounts and payment status.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    try:
        params = maxotel_config.get_base_params()
        params.update({
            "action": "getInvoices",
            "month": f"{month:02d}",
            "year": str(year)
        })

        if include_unpaid:
            params["unpaid"] = "1"

        async with httpx.AsyncClient() as client:
            response = await client.get(maxotel_config.base_url, params=params, timeout=60.0)
            response.raise_for_status()
            data = response.json()

        if data.get("response") == "ERROR":
            return f"Maxotel API Error: {data.get('response_text', 'Unknown error')}"

        response_data = data.get("response_data", {})
        invoice_count = response_data.get("Invoices", 0)
        invoices = response_data.get("invoice_data", [])

        if not invoices:
            return f"No invoices found for {month:02d}/{year}."

        output = [
            f"# Maxotel Invoices\n",
            f"**Period:** {month:02d}/{year}",
            f"**Total Invoices:** {invoice_count}\n",
            "| Invoice ID | Customer | Business | Amount | Paid | Status |",
            "| --- | --- | --- | --- | --- | --- |"
        ]

        total_amount = 0.0
        total_paid = 0.0

        for inv in invoices:
            invoice_id = inv.get("invoice_id", "N/A")
            customer = f"{inv.get('first_name', '')} {inv.get('last_name', '')}".strip() or "N/A"
            business = inv.get("business_name", "-")[:30]
            amount = float(inv.get("amount", 0))
            paid = float(inv.get("amount_paid", 0))
            status = inv.get("status", "Unknown")

            total_amount += amount
            total_paid += paid

            output.append(f"| {invoice_id} | {customer} | {business} | ${amount:.2f} | ${paid:.2f} | {status} |")

        output.append(f"\n**Total Amount:** ${total_amount:.2f}")
        output.append(f"**Total Paid:** ${total_paid:.2f}")
        output.append(f"**Outstanding:** ${total_amount - total_paid:.2f}")

        return "\n".join(output)
    except Exception as e:
        logger.error(f"Maxotel invoices error: {e}")
        return f"Maxotel error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def maxotel_get_transactions(
    start_date: str = Field(..., description="Start date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    end_date: str = Field(..., description="End date/time in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format"),
    subscriptions_only: bool = Field(False, description="Show only subscription-related transactions"),
    payments_only: bool = Field(False, description="Show only payment transactions"),
    as_csv: bool = Field(False, description="Return results as CSV format")
) -> str:
    """
    Get wholesale transaction details from Maxotel.
    Shows transactions at the whitelabel account level.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    try:
        from datetime import datetime as dt

        def parse_to_unix(date_str: str) -> int:
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                try:
                    return int(dt.strptime(date_str, fmt).timestamp())
                except ValueError:
                    continue
            raise ValueError(f"Invalid date format: {date_str}")

        start_unix = parse_to_unix(start_date)
        end_unix = parse_to_unix(end_date)

        params = maxotel_config.get_base_params()
        params.update({
            "action": "getTxns",
            "start": str(start_unix),
            "end": str(end_unix)
        })

        if subscriptions_only:
            params["subscriptionsonly"] = "1"
        if payments_only:
            params["paymentsonly"] = "1"
        if as_csv:
            params["getcsv"] = "1"
            params["showheadings"] = "1"

        async with httpx.AsyncClient() as client:
            response = await client.get(maxotel_config.base_url, params=params, timeout=60.0)
            response.raise_for_status()

        if as_csv:
            csv_content = response.text
            if not csv_content.strip():
                return "No transactions found for the specified period."
            lines = csv_content.strip().split('\n')
            return f"# Wholesale Transactions (CSV)\n\n**Period:** {start_date} to {end_date}\n**Records:** {len(lines) - 1}\n\n```csv\n{csv_content}\n```"

        data = response.json()

        if data.get("response") == "ERROR":
            return f"Maxotel API Error: {data.get('response_text', 'Unknown error')}"

        response_data = data.get("response_data", {})
        txn_count = response_data.get("Transactions", 0)
        transactions = response_data.get("transaction_data", [])

        if not transactions:
            return "No transactions found for the specified period."

        output = [
            f"# Wholesale Transactions\n",
            f"**Period:** {start_date} to {end_date}",
            f"**Total Transactions:** {txn_count}\n",
            "| Date/Time | Client ID | Description | Type | Period | Amount |",
            "| --- | --- | --- | --- | --- | --- |"
        ]

        total_amount = 0.0
        for txn in transactions:
            datetime_str = txn.get("datetime", "N/A")
            client_id = txn.get("clientid", "N/A")
            description = txn.get("description", "N/A")[:40]

            txn_type = []
            if txn.get("payment") == "1":
                txn_type.append("Payment")
            if txn.get("subscription") == "1":
                txn_type.append("Subscription")
            type_str = ", ".join(txn_type) if txn_type else "Other"

            period = txn.get("period", "-")
            amount = float(txn.get("amount", 0))
            total_amount += amount

            output.append(f"| {datetime_str} | {client_id} | {description} | {type_str} | {period} | ${amount:.2f} |")

        output.append(f"\n**Total Amount:** ${total_amount:.2f}")
        return "\n".join(output)
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"Maxotel transactions error: {e}")
        return f"Maxotel error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def maxotel_list_plans() -> str:
    """
    List available plans from Maxotel for customer provisioning.
    Returns plan IDs, names, prices, and features.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    try:
        params = maxotel_config.get_base_params()
        params.update({
            "action": "newCustomer",
            "list_plans": "1"
        })

        async with httpx.AsyncClient() as client:
            response = await client.get(maxotel_config.base_url, params=params, timeout=60.0)
            response.raise_for_status()
            data = response.json()

        if data.get("Response") == "ERROR":
            return f"Maxotel API Error: {data.get('Response_text', 'Unknown error')}"

        plans = data.get("Plans", [])

        if not plans:
            return "No plans available."

        output = [
            "# Maxotel Plans\n",
            "| Plan ID | Name | Price | Lines | IP Trunks | PBX Extensions | DIDs | Active |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"
        ]

        for plan in plans:
            plan_id = plan.get("Account_plan_id", "N/A")
            name = plan.get("Name", "N/A")
            price = f"${float(plan.get('Price', 0)):.2f}"
            lines = plan.get("Lines", "0")
            ip_trunks = plan.get("Ip_trunks", "0")
            pbx_extens = plan.get("Pbx_extens", "0")
            dids = plan.get("Dids", "0")
            active = "Yes" if plan.get("Active") == "1" else "No"

            output.append(f"| {plan_id} | {name} | {price} | {lines} | {ip_trunks} | {pbx_extens} | {dids} | {active} |")

        output.append(f"\nTotal: {len(plans)} plan(s)")
        return "\n".join(output)
    except Exception as e:
        logger.error(f"Maxotel list plans error: {e}")
        return f"Maxotel error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
async def maxotel_create_customer(
    # Account credentials (required)
    account_username: str = Field(..., description="Customer username (min 6 alphanumeric characters)"),
    account_password: str = Field(..., description="Customer password (min 6 characters)"),
    # Contact details (required)
    account_first_name: str = Field(..., description="Account holder first name (min 2 chars)"),
    account_last_name: str = Field(..., description="Account holder surname (min 2 chars)"),
    account_email: str = Field(..., description="Account holder email"),
    # Address (required)
    account_address: str = Field(..., description="Street address (min 7 chars)"),
    account_city: str = Field(..., description="City/suburb (min 2 chars)"),
    account_post_code: str = Field(..., description="Postcode (min 4 chars)"),
    account_state: str = Field(..., description="State: QLD, NSW, ACT, VIC, TAS, SA, WA, NT"),
    # IPND Service Location (required)
    ipnd_street_number: str = Field(..., description="IPND street number"),
    ipnd_street_name: str = Field(..., description="IPND street name (min 2, max 25 chars)"),
    ipnd_street_type: str = Field(..., description="IPND street type (e.g., St, Ave, Rd)"),
    ipnd_locality: str = Field(..., description="IPND suburb name"),
    ipnd_state: str = Field(..., description="IPND state: QLD, NSW, ACT, VIC, TAS, SA, WA, NT"),
    ipnd_postcode: str = Field(..., description="IPND postcode (4 digits)"),
    # Plan (required)
    account_plan_id: str = Field(..., description="Plan ID (from maxotel_list_plans)"),
    # Optional parameters below
    account_cust_id: Optional[str] = Field(None, description="Your customer account reference"),
    account_mobile: Optional[str] = Field(None, description="Mobile number (min 10 digits, required if no phone)"),
    account_phone: Optional[str] = Field(None, description="Phone number (min 10 digits, required if no mobile)"),
    account_timezone: str = Field("Australia/Sydney", description="Timezone (e.g., Australia/Sydney, Australia/Melbourne)"),
    ipnd_building_type: str = Field("OFF", description="Building type: APT, FF, FY, MB, OFF, RM, SE, SHE, SHOP, SITE, LU, VL, LLA, WE"),
    ipnd_floor_type: str = Field("L", description="Floor type: BF, L, LG, LM, UG"),
    account_plan_prorated: bool = Field(True, description="Charge prorated plan fee"),
    account_postpaid: bool = Field(False, description="True for postpaid, False for prepaid"),
    account_credit_limit: float = Field(0.0, description="Account spend limit"),
    strict: bool = Field(True, description="When true, soft fails prevent processing"),
    confirm: bool = Field(True, description="Activate account immediately")
) -> str:
    """
    Create a new customer account in Maxotel VoIP system.

    Security Warning: VoIP fraud is prevalent. Keep new customers prepaid,
    verify details, and never set call rates to 0.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    # Validate at least one phone number
    if not account_mobile and not account_phone:
        return "Error: Either account_mobile or account_phone is required."

    try:
        params = maxotel_config.get_base_params()
        params["action"] = "newCustomer"

        # Build POST data
        form_data = {
            # Account credentials
            "account_username": account_username,
            "account_password": account_password,
            # Contact details
            "account_first_name": account_first_name,
            "account_last_name": account_last_name,
            "account_email": account_email,
            "account_timezone": account_timezone,
            # Address
            "account_address": account_address,
            "account_city": account_city,
            "account_post_code": account_post_code,
            "account_state": account_state,
            "account_country": "Australia",
            # IPND Service Location
            "ipnd_service_building_type": ipnd_building_type,
            "ipnd_service_building_floor_type": ipnd_floor_type,
            "ipnd_service_street_house_number_1": ipnd_street_number,
            "ipnd_service_street_name_1": ipnd_street_name,
            "ipnd_service_street_type_1": ipnd_street_type,
            "ipnd_service_address_locality": ipnd_locality,
            "ipnd_service_province_id": ipnd_state,
            "ipnd_service_address_post_code": ipnd_postcode,
            # Plan & Billing
            "account_plan_id": account_plan_id,
            "account_plan_prorated": "1" if account_plan_prorated else "0",
            "account_postpaid": "1" if account_postpaid else "0",
            "account_credit_limit": str(account_credit_limit),
            # Flags
            "strict": "1" if strict else "0",
            "confirm": "1" if confirm else "0"
        }

        if account_cust_id:
            form_data["account_cust_id"] = account_cust_id
        if account_mobile:
            form_data["account_mobile"] = account_mobile
        if account_phone:
            form_data["account_phone"] = account_phone

        async with httpx.AsyncClient() as client:
            response = await client.post(
                maxotel_config.base_url,
                params=params,
                data=form_data,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()

        if data.get("Response") == "ERROR":
            errors = data.get("Errors", [])
            warnings = data.get("Warnings", [])

            error_msgs = [f"- {e.get('Element', 'Unknown')}: {e.get('Error_msg', 'Error')}" for e in errors]
            warning_msgs = [f"- {w.get('Element', 'Unknown')}: {w.get('Error_msg', 'Warning')}" for w in warnings]

            output = [f"# Customer Creation Failed\n", data.get("Response_text", "Error adding customer")]
            if error_msgs:
                output.append("\n**Errors:**")
                output.extend(error_msgs)
            if warning_msgs:
                output.append("\n**Warnings:**")
                output.extend(warning_msgs)

            return "\n".join(output)

        customer = data.get("Customer", {})
        return f"""# Customer Created Successfully

**Client ID:** {customer.get('Clientid', 'N/A')}
**Customer ID:** {customer.get('Custid', 'N/A')}
**Account Reference:** {customer.get('Accref', 'N/A')}

{data.get('Response_txt', 'Customer successfully added.')}"""

    except Exception as e:
        logger.error(f"Maxotel create customer error: {e}")
        return f"Maxotel error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def maxotel_quick_login(
    accref: Optional[str] = Field(None, description="Customer account reference (required if no client_id)"),
    client_id: Optional[str] = Field(None, description="MaxoTel client ID (required if no accref)"),
    cust_id: Optional[str] = Field(None, description="Your reference number for the customer"),
    admin: bool = Field(False, description="True for staff access, False for customer access")
) -> str:
    """
    Generate a quick login URL for a customer or staff member.
    Allows single-sign-on into the Maxotel portal without credentials.

    Token is valid for 30 seconds after generation.
    """
    if not maxotel_config.is_configured:
        return "Error: Maxotel not configured. Set MAXOTEL_USERNAME and MAXOTEL_API_KEY environment variables."

    if not accref and not client_id:
        return "Error: Either accref or client_id is required."

    try:
        params = maxotel_config.get_base_params()
        params.update({
            "action": "quickLogin",
            "admin": "1" if admin else "0"
        })

        if accref:
            params["accref"] = accref
        if client_id:
            params["clientid"] = client_id
        if cust_id:
            params["custid"] = cust_id

        async with httpx.AsyncClient() as client:
            response = await client.get(maxotel_config.base_url, params=params, timeout=60.0)
            response.raise_for_status()
            data = response.json()

        if data.get("Response") == "ERROR":
            return f"Maxotel API Error: {data.get('Response_text', 'Unknown error')}"

        response_data = data.get("Response_data", data.get("response_data", {}))
        login_url = response_data.get("Login_url", response_data.get("login_url", ""))
        key_valid = response_data.get("Key_valid", response_data.get("key_valid", 30))

        return f"""# Quick Login Generated

**Login URL:** {login_url}

**Token Valid:** {key_valid} seconds

**Access Type:** {"Staff (Admin)" if admin else "Customer"}

Note: This URL is single-use and expires after {key_valid} seconds."""

    except Exception as e:
        logger.error(f"Maxotel quick login error: {e}")
        return f"Maxotel error: {str(e)}"


# ============================================================================
# Ubuntu Server Integration (SSH)
# ============================================================================

class UbuntuConfig:
    """Ubuntu server SSH configuration for remote command execution."""
    def __init__(self):
        self.hostname = os.getenv("UBUNTU_HOSTNAME", "")
        self.port = int(os.getenv("UBUNTU_PORT", "22"))
        self.username = os.getenv("UBUNTU_USERNAME", "")
        self.password = os.getenv("UBUNTU_PASSWORD", "")
        # SSH private key (base64 encoded or direct key content)
        self._private_key = os.getenv("UBUNTU_PRIVATE_KEY", "")
        # Optional: path to private key file in Secret Manager
        self._private_key_secret = os.getenv("UBUNTU_PRIVATE_KEY_SECRET", "")
        # Known hosts verification (disable for self-signed/unknown hosts)
        self.verify_host = os.getenv("UBUNTU_VERIFY_HOST", "false").lower() == "true"
        # Connection timeout
        self.timeout = int(os.getenv("UBUNTU_TIMEOUT", "30"))
        # Friendly name for this server
        self.server_name = os.getenv("UBUNTU_SERVER_NAME", "Ubuntu Server")

    @property
    def is_configured(self) -> bool:
        """Check if minimum SSH configuration is available."""
        has_auth = bool(self.password) or bool(self._private_key) or bool(self._private_key_secret)
        return bool(self.hostname) and bool(self.username) and has_auth

    def get_private_key(self) -> Optional[str]:
        """Get the SSH private key, loading from Secret Manager if needed."""
        if self._private_key:
            # Check if base64 encoded
            import base64
            try:
                decoded = base64.b64decode(self._private_key).decode('utf-8')
                if decoded.startswith('-----BEGIN'):
                    return decoded
            except Exception:
                pass
            # Return as-is if not base64 or already plain text
            if self._private_key.startswith('-----BEGIN'):
                return self._private_key
            return None

        if self._private_key_secret:
            secret_value = get_secret_sync(self._private_key_secret)
            if secret_value:
                return secret_value

        return None

ubuntu_config = UbuntuConfig()


async def _get_ssh_connection():
    """Create an SSH connection to the Ubuntu server."""
    import asyncssh

    connect_kwargs = {
        "host": ubuntu_config.hostname,
        "port": ubuntu_config.port,
        "username": ubuntu_config.username,
        "connect_timeout": ubuntu_config.timeout,
    }

    # Add authentication
    private_key = ubuntu_config.get_private_key()
    if private_key:
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
    elif ubuntu_config.password:
        connect_kwargs["password"] = ubuntu_config.password

    # Host key verification
    if not ubuntu_config.verify_host:
        connect_kwargs["known_hosts"] = None

    return await asyncssh.connect(**connect_kwargs)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
async def ubuntu_execute_command(
    command: str = Field(..., description="The shell command to execute on the Ubuntu server"),
    timeout: int = Field(60, description="Command timeout in seconds (max 300)"),
    working_directory: Optional[str] = Field(None, description="Directory to run the command in")
) -> str:
    """
    Execute a shell command on the remote Ubuntu server via SSH.

    Security: This tool has full shell access. Use with caution.
    Commands are executed as the configured SSH user.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured. Set UBUNTU_HOSTNAME, UBUNTU_USERNAME, and UBUNTU_PASSWORD or UBUNTU_PRIVATE_KEY."

    try:
        import asyncssh

        timeout = min(max(1, timeout), 300)  # Clamp between 1-300 seconds

        # Prepend cd if working directory specified
        if working_directory:
            command = f"cd {working_directory} && {command}"

        async with await _get_ssh_connection() as conn:
            result = await asyncio.wait_for(
                conn.run(command, check=False),
                timeout=timeout
            )

            output_parts = []

            if result.stdout:
                output_parts.append(f"**STDOUT:**\n```\n{result.stdout.strip()}\n```")

            if result.stderr:
                output_parts.append(f"**STDERR:**\n```\n{result.stderr.strip()}\n```")

            exit_status = f"**Exit Code:** {result.exit_status}"

            if not output_parts:
                output_parts.append("*(No output)*")

            return f"# Command Executed on {ubuntu_config.server_name}\n\n**Command:** `{command}`\n\n{exit_status}\n\n" + "\n\n".join(output_parts)

    except asyncio.TimeoutError:
        return f"Error: Command timed out after {timeout} seconds."
    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu execute command error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def ubuntu_read_file(
    file_path: str = Field(..., description="Absolute path to the file to read"),
    max_lines: int = Field(500, description="Maximum number of lines to return (default 500, max 2000)"),
    encoding: str = Field("utf-8", description="File encoding (default utf-8)")
) -> str:
    """
    Read the contents of a file from the Ubuntu server.
    Large files are truncated to max_lines.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    try:
        import asyncssh

        max_lines = min(max(1, max_lines), 2000)

        async with await _get_ssh_connection() as conn:
            # Check if file exists and get info
            result = await conn.run(f"stat -c '%s %F' {file_path} 2>/dev/null", check=False)

            if result.exit_status != 0:
                return f"Error: File not found or not accessible: {file_path}"

            file_info = result.stdout.strip().split(' ', 1)
            file_size = int(file_info[0]) if file_info else 0
            file_type = file_info[1] if len(file_info) > 1 else "unknown"

            if "directory" in file_type.lower():
                return f"Error: {file_path} is a directory. Use ubuntu_list_directory instead."

            # Read the file with line limit
            result = await conn.run(f"head -n {max_lines} {file_path}", check=False)

            if result.exit_status != 0:
                return f"Error reading file: {result.stderr.strip()}"

            content = result.stdout

            # Check if file was truncated
            total_lines_result = await conn.run(f"wc -l < {file_path}", check=False)
            total_lines = int(total_lines_result.stdout.strip()) if total_lines_result.exit_status == 0 else 0

            truncated_msg = ""
            if total_lines > max_lines:
                truncated_msg = f"\n\n*File truncated: showing {max_lines} of {total_lines} lines*"

            return f"# File: {file_path}\n\n**Size:** {file_size:,} bytes | **Lines:** {total_lines}\n\n```\n{content}\n```{truncated_msg}"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu read file error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def ubuntu_write_file(
    file_path: str = Field(..., description="Absolute path to the file to write"),
    content: str = Field(..., description="Content to write to the file"),
    append: bool = Field(False, description="Append to file instead of overwriting"),
    create_dirs: bool = Field(False, description="Create parent directories if they don't exist"),
    mode: Optional[str] = Field(None, description="File permissions (e.g., '644', '755')")
) -> str:
    """
    Write content to a file on the Ubuntu server.

    Warning: This will overwrite existing files unless append=True.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    try:
        import asyncssh

        async with await _get_ssh_connection() as conn:
            # Create parent directories if requested
            if create_dirs:
                parent_dir = '/'.join(file_path.rsplit('/', 1)[:-1])
                if parent_dir:
                    await conn.run(f"mkdir -p {parent_dir}", check=False)

            # Use a heredoc to write content safely
            operator = ">>" if append else ">"
            # Escape content for shell
            escaped_content = content.replace("'", "'\\''")

            write_cmd = f"cat << 'EOFMARKER' {operator} {file_path}\n{content}\nEOFMARKER"
            result = await conn.run(write_cmd, check=False)

            if result.exit_status != 0:
                return f"Error writing file: {result.stderr.strip()}"

            # Set permissions if specified
            if mode:
                await conn.run(f"chmod {mode} {file_path}", check=False)

            # Get final file info
            stat_result = await conn.run(f"stat -c '%s bytes, %A' {file_path}", check=False)
            file_info = stat_result.stdout.strip() if stat_result.exit_status == 0 else "unknown"

            action = "appended to" if append else "written to"
            return f"# File {action.title()}\n\n**Path:** {file_path}\n**Info:** {file_info}\n**Bytes written:** {len(content.encode('utf-8')):,}"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu write file error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def ubuntu_list_directory(
    path: str = Field("/", description="Directory path to list"),
    show_hidden: bool = Field(False, description="Include hidden files (starting with .)"),
    long_format: bool = Field(True, description="Show detailed file information"),
    recursive: bool = Field(False, description="List subdirectories recursively (use with caution)")
) -> str:
    """
    List contents of a directory on the Ubuntu server.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    try:
        import asyncssh

        ls_flags = "-l" if long_format else ""
        if show_hidden:
            ls_flags += "a"
        if recursive:
            ls_flags += "R"

        ls_flags = f"-{ls_flags}" if ls_flags else ""

        async with await _get_ssh_connection() as conn:
            result = await conn.run(f"ls {ls_flags} {path} 2>&1", check=False)

            if result.exit_status != 0:
                return f"Error listing directory: {result.stdout.strip()}"

            output = result.stdout.strip()

            # Count items
            if long_format:
                lines = [l for l in output.split('\n') if l and not l.startswith('total')]
                item_count = len(lines)
            else:
                item_count = len(output.split())

            return f"# Directory: {path}\n\n**Items:** {item_count}\n\n```\n{output}\n```"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu list directory error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def ubuntu_system_info() -> str:
    """
    Get system information from the Ubuntu server including OS, memory, disk, and uptime.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    try:
        import asyncssh

        async with await _get_ssh_connection() as conn:
            commands = {
                "hostname": "hostname -f 2>/dev/null || hostname",
                "os": "lsb_release -d 2>/dev/null | cut -f2 || cat /etc/os-release | grep PRETTY_NAME | cut -d'\"' -f2",
                "kernel": "uname -r",
                "uptime": "uptime -p 2>/dev/null || uptime",
                "memory": "free -h | grep Mem | awk '{print $2 \" total, \" $3 \" used, \" $4 \" free\"}'",
                "disk": "df -h / | tail -1 | awk '{print $2 \" total, \" $3 \" used, \" $4 \" free (\" $5 \" used)\"}'",
                "cpu": "nproc",
                "load": "cat /proc/loadavg | cut -d' ' -f1-3",
                "ip": "hostname -I 2>/dev/null | awk '{print $1}' || ip route get 1 | awk '{print $7}'",
            }

            results = {}
            for name, cmd in commands.items():
                result = await conn.run(cmd, check=False)
                results[name] = result.stdout.strip() if result.exit_status == 0 else "N/A"

            return f"""# System Information: {ubuntu_config.server_name}

**Hostname:** {results['hostname']}
**IP Address:** {results['ip']}
**OS:** {results['os']}
**Kernel:** {results['kernel']}
**Uptime:** {results['uptime']}

## Resources
**CPU Cores:** {results['cpu']}
**Load Average:** {results['load']}
**Memory:** {results['memory']}
**Disk (/):** {results['disk']}"""

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu system info error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def ubuntu_service_status(
    service_name: Optional[str] = Field(None, description="Specific service name to check (e.g., 'nginx', 'docker'). If not provided, lists all active services.")
) -> str:
    """
    Check the status of system services on the Ubuntu server.
    Uses systemctl to query service states.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    try:
        import asyncssh

        async with await _get_ssh_connection() as conn:
            if service_name:
                # Check specific service
                result = await conn.run(f"systemctl status {service_name} 2>&1", check=False)

                # Also get enabled/disabled state
                enabled_result = await conn.run(f"systemctl is-enabled {service_name} 2>&1", check=False)
                enabled_state = enabled_result.stdout.strip()

                return f"# Service: {service_name}\n\n**Enabled:** {enabled_state}\n\n```\n{result.stdout.strip()}\n```"
            else:
                # List all active services
                result = await conn.run("systemctl list-units --type=service --state=running --no-pager --no-legend | head -30", check=False)

                if result.exit_status != 0:
                    return f"Error getting service list: {result.stderr.strip()}"

                services = result.stdout.strip()
                lines = services.split('\n')

                return f"# Active Services on {ubuntu_config.server_name}\n\n**Running Services:** {len(lines)}\n\n```\n{services}\n```\n\n*Showing first 30 services. Use service_name parameter for specific service details.*"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu service status error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def ubuntu_manage_service(
    service_name: str = Field(..., description="Name of the service to manage"),
    action: str = Field(..., description="Action to perform: 'start', 'stop', 'restart', 'reload', 'enable', 'disable'")
) -> str:
    """
    Manage (start/stop/restart) a system service on the Ubuntu server.

    Requires the SSH user to have sudo privileges for systemctl commands.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    valid_actions = ['start', 'stop', 'restart', 'reload', 'enable', 'disable']
    if action.lower() not in valid_actions:
        return f"Error: Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}"

    try:
        import asyncssh

        async with await _get_ssh_connection() as conn:
            # Try with sudo first, fall back to direct command
            cmd = f"sudo systemctl {action.lower()} {service_name} 2>&1"
            result = await conn.run(cmd, check=False)

            if result.exit_status != 0:
                # Try without sudo
                cmd = f"systemctl {action.lower()} {service_name} 2>&1"
                result = await conn.run(cmd, check=False)

            if result.exit_status != 0:
                return f"Error: Failed to {action} {service_name}\n\n```\n{result.stdout.strip()}\n```"

            # Get new status
            status_result = await conn.run(f"systemctl is-active {service_name} 2>&1", check=False)
            current_state = status_result.stdout.strip()

            return f"# Service Action Completed\n\n**Service:** {service_name}\n**Action:** {action}\n**Current State:** {current_state}\n\n{result.stdout.strip() if result.stdout.strip() else 'Action completed successfully.'}"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu manage service error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def ubuntu_process_list(
    filter_user: Optional[str] = Field(None, description="Filter by username"),
    filter_name: Optional[str] = Field(None, description="Filter by process name (grep pattern)"),
    sort_by: str = Field("cpu", description="Sort by: 'cpu', 'mem', 'pid', 'time'"),
    limit: int = Field(20, description="Maximum number of processes to show")
) -> str:
    """
    List running processes on the Ubuntu server.
    Similar to 'top' or 'ps aux' but formatted for readability.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    try:
        import asyncssh

        limit = min(max(1, limit), 100)

        sort_map = {
            "cpu": "-pcpu",
            "mem": "-pmem",
            "pid": "-pid",
            "time": "-time"
        }
        sort_flag = sort_map.get(sort_by.lower(), "-pcpu")

        async with await _get_ssh_connection() as conn:
            cmd = f"ps aux --sort={sort_flag}"

            if filter_user:
                cmd = f"ps -u {filter_user} aux --sort={sort_flag}"

            if filter_name:
                cmd = f"{cmd} | grep -i '{filter_name}' | grep -v grep"

            cmd = f"{cmd} | head -n {limit + 1}"  # +1 for header

            result = await conn.run(cmd, check=False)

            if result.exit_status != 0 and not result.stdout.strip():
                return f"Error getting process list: {result.stderr.strip()}"

            output = result.stdout.strip()
            lines = output.split('\n')
            process_count = len(lines) - 1 if lines else 0

            filter_info = ""
            if filter_user:
                filter_info += f" | User: {filter_user}"
            if filter_name:
                filter_info += f" | Filter: '{filter_name}'"

            return f"# Process List on {ubuntu_config.server_name}\n\n**Showing:** {process_count} processes (sorted by {sort_by}){filter_info}\n\n```\n{output}\n```"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu process list error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": True})
async def ubuntu_docker_status(
    container_name: Optional[str] = Field(None, description="Specific container name/ID to inspect"),
    show_logs: bool = Field(False, description="Show recent logs for the container (requires container_name)"),
    log_lines: int = Field(50, description="Number of log lines to show (if show_logs=True)")
) -> str:
    """
    Get Docker container status and information from the Ubuntu server.
    Requires Docker to be installed and accessible by the SSH user.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    try:
        import asyncssh

        async with await _get_ssh_connection() as conn:
            # Check if docker is available
            docker_check = await conn.run("which docker 2>/dev/null", check=False)
            if docker_check.exit_status != 0:
                return "Error: Docker is not installed or not in PATH on this server."

            if container_name:
                # Get specific container info
                inspect_result = await conn.run(f"docker inspect {container_name} --format '{{{{.State.Status}}}} | {{{{.State.StartedAt}}}} | {{{{.Config.Image}}}}' 2>&1", check=False)

                if inspect_result.exit_status != 0:
                    return f"Error: Container '{container_name}' not found or not accessible.\n\n```\n{inspect_result.stdout.strip()}\n```"

                container_info = inspect_result.stdout.strip()

                output = f"# Container: {container_name}\n\n**Status:** {container_info}\n"

                if show_logs:
                    log_lines = min(max(1, log_lines), 500)
                    logs_result = await conn.run(f"docker logs --tail {log_lines} {container_name} 2>&1", check=False)
                    output += f"\n## Recent Logs ({log_lines} lines)\n\n```\n{logs_result.stdout.strip()}\n```"

                return output
            else:
                # List all containers
                result = await conn.run("docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}' 2>&1", check=False)

                if result.exit_status != 0:
                    return f"Error getting Docker status: {result.stdout.strip()}"

                output = result.stdout.strip()
                lines = output.split('\n')
                container_count = len(lines) - 1 if lines else 0

                # Get running count
                running_result = await conn.run("docker ps -q | wc -l", check=False)
                running_count = running_result.stdout.strip() if running_result.exit_status == 0 else "?"

                return f"# Docker Containers on {ubuntu_config.server_name}\n\n**Total:** {container_count} | **Running:** {running_count}\n\n```\n{output}\n```"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu docker status error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
async def ubuntu_docker_manage(
    container_name: str = Field(..., description="Container name or ID"),
    action: str = Field(..., description="Action: 'start', 'stop', 'restart', 'pause', 'unpause', 'kill'")
) -> str:
    """
    Manage Docker containers on the Ubuntu server.
    Start, stop, restart, or kill containers.
    """
    if not ubuntu_config.is_configured:
        return "Error: Ubuntu server not configured."

    valid_actions = ['start', 'stop', 'restart', 'pause', 'unpause', 'kill']
    if action.lower() not in valid_actions:
        return f"Error: Invalid action '{action}'. Valid actions: {', '.join(valid_actions)}"

    try:
        import asyncssh

        async with await _get_ssh_connection() as conn:
            result = await conn.run(f"docker {action.lower()} {container_name} 2>&1", check=False)

            if result.exit_status != 0:
                return f"Error: Failed to {action} container '{container_name}'\n\n```\n{result.stdout.strip()}\n```"

            # Get new status
            status_result = await conn.run(f"docker inspect {container_name} --format '{{{{.State.Status}}}}' 2>&1", check=False)
            current_state = status_result.stdout.strip() if status_result.exit_status == 0 else "unknown"

            return f"# Docker Container Action\n\n**Container:** {container_name}\n**Action:** {action}\n**Current State:** {current_state}"

    except asyncssh.Error as e:
        logger.error(f"SSH error: {e}")
        return f"SSH Error: {str(e)}"
    except Exception as e:
        logger.error(f"Ubuntu docker manage error: {e}")
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

    # BigQuery status
    if bigquery_config.is_configured:
        try:
            client = bigquery_config.get_client()
            # Quick connectivity test
            list(client.list_datasets(max_results=1))
            job_info = f", jobs: {bigquery_config.job_project_id}" if bigquery_config.job_project_id != bigquery_config.project_id else ""
            lines.append(f"✅ **BigQuery:** Connected (data: {bigquery_config.project_id}{job_info})")
        except Exception as e:
            lines.append(f"❌ **BigQuery:** Error - {str(e)[:50]}")
    else:
        missing = []
        if not os.getenv("BIGQUERY_PROJECT_ID"):
            missing.append("BIGQUERY_PROJECT_ID")
        lines.append(f"⚠️ **BigQuery:** Missing: {', '.join(missing)}")

    # FortiCloud status
    if forticloud_config.is_configured:
        try:
            await forticloud_config.get_access_token()
            lines.append(f"✅ **FortiCloud:** Connected ({forticloud_config.client_id})")
        except Exception as e:
            lines.append(f"❌ **FortiCloud:** Auth failed - {str(e)[:50]}")
    else:
        missing = []
        if not os.getenv("FORTICLOUD_API_KEY"):
            missing.append("API_KEY")
        if not os.getenv("FORTICLOUD_API_SECRET"):
            missing.append("API_SECRET")
        lines.append(f"⚠️ **FortiCloud:** Missing: {', '.join(missing)}")

    # Maxotel status
    if maxotel_config.is_configured:
        lines.append("✅ **Maxotel:** Configured")
    else:
        missing = []
        if not os.getenv("MAXOTEL_USERNAME"):
            missing.append("USERNAME")
        if not os.getenv("MAXOTEL_API_KEY"):
            missing.append("API_KEY")
        lines.append(f"⚠️ **Maxotel:** Missing: {', '.join(missing)}")

    # Ubuntu Server (SSH) status
    if ubuntu_config.is_configured:
        try:
            import asyncssh
            async with await _get_ssh_connection() as conn:
                result = await conn.run("hostname", check=False)
                hostname = result.stdout.strip() if result.exit_status == 0 else "unknown"
            lines.append(f"✅ **Ubuntu ({ubuntu_config.server_name}):** Connected to {hostname}")
        except Exception as e:
            lines.append(f"❌ **Ubuntu ({ubuntu_config.server_name}):** SSH failed - {str(e)[:50]}")
    else:
        missing = []
        if not os.getenv("UBUNTU_HOSTNAME"):
            missing.append("HOSTNAME")
        if not os.getenv("UBUNTU_USERNAME"):
            missing.append("USERNAME")
        if not os.getenv("UBUNTU_PASSWORD") and not os.getenv("UBUNTU_PRIVATE_KEY"):
            missing.append("PASSWORD or PRIVATE_KEY")
        lines.append(f"⚠️ **Ubuntu Server:** Missing: {', '.join(missing)}")

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

    async def status_page_route(request):
        """Web-based status page showing all service integrations."""
        from datetime import datetime

        check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

        # Build status checks for each service
        services = []

        # HaloPSA
        if halopsa_config.is_configured:
            try:
                await halopsa_config.get_access_token()
                services.append(("HaloPSA", "ok", "Connected", "Ticketing & PSA"))
            except Exception as e:
                services.append(("HaloPSA", "error", f"Auth failed: {str(e)[:40]}", "Ticketing & PSA"))
        else:
            services.append(("HaloPSA", "warning", "Not configured", "Ticketing & PSA"))

        # Xero
        if xero_config.is_configured:
            try:
                await xero_config.get_access_token()
                services.append(("Xero", "ok", "Connected", "Accounting"))
            except Exception as e:
                services.append(("Xero", "error", f"Auth failed: {str(e)[:40]}", "Accounting"))
        else:
            missing = []
            if not os.getenv("XERO_CLIENT_ID"): missing.append("CLIENT_ID")
            if not os.getenv("XERO_CLIENT_SECRET"): missing.append("CLIENT_SECRET")
            if not os.getenv("XERO_TENANT_ID"): missing.append("TENANT_ID")
            if not os.getenv("XERO_REFRESH_TOKEN"): missing.append("REFRESH_TOKEN")
            services.append(("Xero", "warning", f"Missing: {', '.join(missing)}" if missing else "Not configured", "Accounting"))

        # SharePoint
        if sharepoint_config.is_configured:
            try:
                await sharepoint_config.get_access_token()
                services.append(("SharePoint", "ok", "Connected", "Document Management"))
            except Exception as e:
                services.append(("SharePoint", "error", f"Auth failed: {str(e)[:40]}", "Document Management"))
        else:
            missing = []
            if not os.getenv("SHAREPOINT_CLIENT_ID"): missing.append("CLIENT_ID")
            if not os.getenv("SHAREPOINT_CLIENT_SECRET"): missing.append("CLIENT_SECRET")
            if not os.getenv("SHAREPOINT_TENANT_ID"): missing.append("TENANT_ID")
            if not os.getenv("SHAREPOINT_REFRESH_TOKEN"): missing.append("REFRESH_TOKEN")
            services.append(("SharePoint", "warning", f"Missing: {', '.join(missing)}" if missing else "Not configured", "Document Management"))

        # Front
        if front_config.is_configured:
            services.append(("Front", "ok", "Configured", "Email & Communications"))
        else:
            services.append(("Front", "warning", "Not configured", "Email & Communications"))

        # Quoter
        if quoter_config.is_configured:
            try:
                await quoter_config.get_access_token()
                services.append(("Quoter", "ok", "Connected", "Quoting"))
            except Exception as e:
                services.append(("Quoter", "error", f"Auth failed: {str(e)[:40]}", "Quoting"))
        else:
            services.append(("Quoter", "warning", "Not configured", "Quoting"))

        # Pax8
        if pax8_config.is_configured:
            try:
                await pax8_config.get_access_token()
                services.append(("Pax8", "ok", "Connected", "Cloud Marketplace"))
            except Exception as e:
                services.append(("Pax8", "error", f"Auth failed: {str(e)[:40]}", "Cloud Marketplace"))
        else:
            missing = []
            if not os.getenv("PAX8_CLIENT_ID"): missing.append("CLIENT_ID")
            if not os.getenv("PAX8_CLIENT_SECRET"): missing.append("CLIENT_SECRET")
            services.append(("Pax8", "warning", f"Missing: {', '.join(missing)}" if missing else "Not configured", "Cloud Marketplace"))

        # BigQuery
        if bigquery_config.is_configured:
            try:
                client = bigquery_config.get_client()
                list(client.list_datasets(max_results=1))
                job_info = f" (jobs: {bigquery_config.job_project_id})" if bigquery_config.job_project_id != bigquery_config.project_id else ""
                services.append(("BigQuery", "ok", f"Connected to {bigquery_config.project_id}{job_info}", "Data Warehouse"))
            except Exception as e:
                services.append(("BigQuery", "error", f"Error: {str(e)[:40]}", "Data Warehouse"))
        else:
            services.append(("BigQuery", "warning", "Missing: BIGQUERY_PROJECT_ID", "Data Warehouse"))

        # FortiCloud
        if forticloud_config.is_configured:
            try:
                await forticloud_config.get_access_token()
                services.append(("FortiCloud", "ok", f"Connected ({forticloud_config.client_id})", "Network Security"))
            except Exception as e:
                services.append(("FortiCloud", "error", f"Auth failed: {str(e)[:40]}", "Network Security"))
        else:
            missing = []
            if not os.getenv("FORTICLOUD_API_KEY"): missing.append("API_KEY")
            if not os.getenv("FORTICLOUD_API_SECRET"): missing.append("API_SECRET")
            services.append(("FortiCloud", "warning", f"Missing: {', '.join(missing)}" if missing else "Not configured", "Network Security"))

        # Maxotel
        if maxotel_config.is_configured:
            services.append(("Maxotel", "ok", "Configured", "VoIP"))
        else:
            missing = []
            if not os.getenv("MAXOTEL_USERNAME"): missing.append("USERNAME")
            if not os.getenv("MAXOTEL_API_KEY"): missing.append("API_KEY")
            services.append(("Maxotel", "warning", f"Missing: {', '.join(missing)}" if missing else "Not configured", "VoIP"))

        # Ubuntu Server
        if ubuntu_config.is_configured:
            try:
                import asyncssh
                async with await _get_ssh_connection() as conn:
                    result = await conn.run("hostname", check=False)
                    hostname = result.stdout.strip() if result.exit_status == 0 else "unknown"
                services.append((f"Ubuntu ({ubuntu_config.server_name})", "ok", f"Connected to {hostname}", "Remote Server"))
            except Exception as e:
                services.append((f"Ubuntu ({ubuntu_config.server_name})", "error", f"SSH failed: {str(e)[:40]}", "Remote Server"))
        else:
            missing = []
            if not os.getenv("UBUNTU_HOSTNAME"): missing.append("HOSTNAME")
            if not os.getenv("UBUNTU_USERNAME"): missing.append("USERNAME")
            if not os.getenv("UBUNTU_PASSWORD") and not os.getenv("UBUNTU_PRIVATE_KEY"): missing.append("AUTH")
            services.append(("Ubuntu Server", "warning", f"Missing: {', '.join(missing)}" if missing else "Not configured", "Remote Server"))

        # Count statuses
        ok_count = sum(1 for s in services if s[1] == "ok")
        error_count = sum(1 for s in services if s[1] == "error")
        warning_count = sum(1 for s in services if s[1] == "warning")

        # Overall status
        if error_count > 0:
            overall_status = "error"
            overall_text = "Issues Detected"
        elif warning_count > 0:
            overall_status = "warning"
            overall_text = "Partially Configured"
        else:
            overall_status = "ok"
            overall_text = "All Systems Operational"

        # Build service rows HTML
        service_rows = ""
        for name, status, message, category in services:
            if status == "ok":
                icon = "✅"
                badge_class = "badge-ok"
            elif status == "error":
                icon = "❌"
                badge_class = "badge-error"
            else:
                icon = "⚠️"
                badge_class = "badge-warning"

            service_rows += f"""
            <tr>
                <td><span class="status-icon">{icon}</span> {name}</td>
                <td><span class="badge {badge_class}">{status.upper()}</span></td>
                <td class="message">{message}</td>
                <td class="category">{category}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="60">
    <title>Crowd IT MCP Server - Status</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #e0e0e0;
            padding: 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}
        header {{
            text-align: center;
            margin-bottom: 30px;
            padding: 30px;
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            backdrop-filter: blur(10px);
        }}
        h1 {{
            font-size: 2rem;
            margin-bottom: 10px;
            color: #fff;
        }}
        .subtitle {{
            color: #888;
            font-size: 0.9rem;
        }}
        .overall-status {{
            display: inline-block;
            padding: 12px 24px;
            border-radius: 30px;
            font-weight: 600;
            font-size: 1.1rem;
            margin: 20px 0;
        }}
        .overall-ok {{ background: rgba(39, 174, 96, 0.2); color: #27ae60; border: 2px solid #27ae60; }}
        .overall-warning {{ background: rgba(241, 196, 15, 0.2); color: #f1c40f; border: 2px solid #f1c40f; }}
        .overall-error {{ background: rgba(231, 76, 60, 0.2); color: #e74c3c; border: 2px solid #e74c3c; }}
        .stats {{
            display: flex;
            justify-content: center;
            gap: 30px;
            margin-top: 15px;
        }}
        .stat {{
            text-align: center;
        }}
        .stat-value {{
            font-size: 1.5rem;
            font-weight: bold;
        }}
        .stat-label {{
            font-size: 0.8rem;
            color: #888;
        }}
        .stat-ok .stat-value {{ color: #27ae60; }}
        .stat-warning .stat-value {{ color: #f1c40f; }}
        .stat-error .stat-value {{ color: #e74c3c; }}
        .services-card {{
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 25px;
            backdrop-filter: blur(10px);
        }}
        .services-card h2 {{
            margin-bottom: 20px;
            font-size: 1.2rem;
            color: #fff;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 14px 12px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        th {{
            color: #888;
            font-weight: 500;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        tr:last-child td {{
            border-bottom: none;
        }}
        tr:hover {{
            background: rgba(255,255,255,0.03);
        }}
        .status-icon {{
            margin-right: 8px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .badge-ok {{ background: rgba(39, 174, 96, 0.2); color: #27ae60; }}
        .badge-warning {{ background: rgba(241, 196, 15, 0.2); color: #f1c40f; }}
        .badge-error {{ background: rgba(231, 76, 60, 0.2); color: #e74c3c; }}
        .message {{
            color: #aaa;
            font-size: 0.9rem;
        }}
        .category {{
            color: #666;
            font-size: 0.85rem;
        }}
        footer {{
            text-align: center;
            margin-top: 30px;
            color: #666;
            font-size: 0.85rem;
        }}
        .refresh-info {{
            margin-top: 10px;
            font-size: 0.8rem;
        }}
        @media (max-width: 600px) {{
            .stats {{ flex-wrap: wrap; gap: 15px; }}
            th, td {{ padding: 10px 8px; font-size: 0.85rem; }}
            .category {{ display: none; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Crowd IT MCP Server</h1>
            <p class="subtitle">Model Context Protocol - Service Status Dashboard</p>
            <div class="overall-status overall-{overall_status}">{overall_text}</div>
            <div class="stats">
                <div class="stat stat-ok">
                    <div class="stat-value">{ok_count}</div>
                    <div class="stat-label">Operational</div>
                </div>
                <div class="stat stat-warning">
                    <div class="stat-value">{warning_count}</div>
                    <div class="stat-label">Not Configured</div>
                </div>
                <div class="stat stat-error">
                    <div class="stat-value">{error_count}</div>
                    <div class="stat-label">Issues</div>
                </div>
            </div>
        </header>

        <div class="services-card">
            <h2>Service Integrations</h2>
            <table>
                <thead>
                    <tr>
                        <th>Service</th>
                        <th>Status</th>
                        <th>Details</th>
                        <th>Category</th>
                    </tr>
                </thead>
                <tbody>
                    {service_rows}
                </tbody>
            </table>
        </div>

        <footer>
            <p>Last checked: {check_time}</p>
            <p class="refresh-info">Auto-refreshes every 60 seconds</p>
            <p style="margin-top: 10px;">MCP Endpoint: <code>{CLOUD_RUN_URL}/mcp</code></p>
        </footer>
    </div>
</body>
</html>"""

        return HTMLResponse(html)

    # Run FastMCP directly - it handles its own routing
    # Add custom routes via Starlette mounting
    app = Starlette(
        routes=[
            Route("/health", health_route),
            Route("/status", status_page_route),
            Route("/callback", callback_route),
            Route("/sharepoint-callback", sharepoint_callback_route),
        ],
        lifespan=mcp_app.lifespan,
    )
    
    # Mount MCP app to handle all other paths (including /mcp, /sse)
    app.mount("/", mcp_app)
    
    uvicorn.run(app, host="0.0.0.0", port=port)