"""
Crowd IT Unified MCP Server
Centralized MCP server for Cloud Run - HaloPSA, Xero, Front, SharePoint, Quoter, Pax8, BigQuery, Maxotel VoIP, Ubuntu Server (SSH), CIPP (M365), Salesforce, n8n (Workflow Automation), GCloud CLI, Azure, AWS, Dicker Data, Ingram Micro, Aussie Broadband Carbon, NinjaOne (RMM), Auvik (Network Management), Metabase (Business Intelligence), Jira (Project Management), Linear (Project Management), and DigitalOcean (Cloud Infrastructure) integration.
"""

# Absolute first thing - print to both stdout and stderr
print("[STARTUP] Python interpreter starting")
import sys
print("[STARTUP] sys imported", file=sys.stderr, flush=True)
import os
print(f"[STARTUP] os imported, PORT={os.getenv('PORT')}, __name__={__name__}", file=sys.stderr, flush=True)

# Note: Removed quick socket server that was causing Cloud Run health check failures
# uvicorn with /health route will handle health checks properly

# Now continue with normal imports
print("[STARTUP] Python starting full initialization...", file=sys.stderr, flush=True)

import time
_module_start_time = time.time()

print(f"[STARTUP] Basic imports done at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)

import asyncio
import logging
import json
import re
import uuid
from datetime import datetime, timedelta, date, timezone
from typing import Optional, Dict, Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

print(f"[STARTUP] stdlib imports done at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)

import httpx
print(f"[STARTUP] httpx imported at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)

from fastmcp import FastMCP
print(f"[STARTUP] FastMCP imported at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)

from pydantic import BaseModel, Field
print(f"[STARTUP] pydantic imported at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)

print("[STARTUP] All critical imports complete", file=sys.stderr, flush=True)

# Initialize FastMCP
mcp = FastMCP("Crowd IT Unified MCP Server")

print(f"[STARTUP] FastMCP initialized at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)

# ============================================================================
# MODELS & TYPES
# ============================================================================

class ToolUnavailableError(Exception):
    """Raised when a tool is not available/configured"""
    pass

# ============================================================================
# HALOPSA INTEGRATION
# ============================================================================

class HaloPSAClient:
    def __init__(self):
        self.base_url = "https://api.halopsa.com"
        self.client_id = os.getenv("HALOPSA_CLIENT_ID")
        self.client_secret = os.getenv("HALOPSA_CLIENT_SECRET")
        self.tenant_id = os.getenv("HALOPSA_TENANT_ID")
        self._access_token = None
        self._token_expires = None

    async def get_access_token(self):
        if self._access_token and self._token_expires and datetime.now() < self._token_expires:
            return self._access_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "all"
                }
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            self._token_expires = datetime.now() + timedelta(seconds=data["expires_in"] - 60)
            return self._access_token

    async def request(self, method: str, endpoint: str, **kwargs):
        token = await self.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

halopsa_client = HaloPSAClient()

# ============================================================================
# XERO INTEGRATION
# ============================================================================

class XeroClient:
    def __init__(self):
        self.base_url = "https://api.xero.com/api.xro/2.0"
        self.tenant_id = os.getenv("XERO_TENANT_ID")
        self._access_token = None
        self._token_expires = None

    async def get_access_token(self):
        if self._access_token and self._token_expires and datetime.now() < self._token_expires:
            return self._access_token

        client_id = os.getenv("XERO_CLIENT_ID")
        client_secret = os.getenv("XERO_CLIENT_SECRET")
        refresh_token = os.getenv("XERO_REFRESH_TOKEN")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://identity.xero.com/connect/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token
                }
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            self._token_expires = datetime.now() + timedelta(seconds=data["expires_in"] - 60)
            return self._access_token

    async def request(self, method: str, endpoint: str, **kwargs):
        token = await self.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers["Xero-tenant-id"] = self.tenant_id
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

xero_client = XeroClient()

# ============================================================================
# FRONT INTEGRATION
# ============================================================================

class FrontClient:
    def __init__(self):
        self.base_url = "https://api2.frontapp.com"
        self.api_token = os.getenv("FRONT_API_TOKEN")

    async def request(self, method: str, endpoint: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_token}"
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

front_client = FrontClient()

# ============================================================================
# QUOTER INTEGRATION
# ============================================================================

class QuoterClient:
    def __init__(self):
        self.base_url = os.getenv("QUOTER_API_URL", "https://api.quoter.io")
        self.api_key = os.getenv("QUOTER_API_KEY")

    async def request(self, method: str, endpoint: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

quoter_client = QuoterClient()

# ============================================================================
# PAX8 INTEGRATION
# ============================================================================

class Pax8Client:
    def __init__(self):
        self.base_url = "https://api.pax8.com"
        self.client_id = os.getenv("PAX8_CLIENT_ID")
        self.client_secret = os.getenv("PAX8_CLIENT_SECRET")
        self._access_token = None
        self._token_expires = None

    async def get_access_token(self):
        if self._access_token and self._token_expires and datetime.now() < self._token_expires:
            return self._access_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/oauth/authorize",
                json={
                    "clientId": self.client_id,
                    "clientSecret": self.client_secret
                }
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data.get("access_token")
            self._token_expires = datetime.now() + timedelta(hours=1)
            return self._access_token

    async def request(self, method: str, endpoint: str, **kwargs):
        token = await self.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

pax8_client = Pax8Client()

# ============================================================================
# SHAREPOINT INTEGRATION
# ============================================================================

class SharePointClient:
    def __init__(self):
        self.base_url = "https://graph.microsoft.com/v1.0"
        self._access_token = os.getenv("SHAREPOINT_ACCESS_TOKEN")

    async def request(self, method: str, endpoint: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token}"
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

sharepoint_client = SharePointClient()

# ============================================================================
# MAXOTEL VOIP INTEGRATION
# ============================================================================

class MaxotelClient:
    def __init__(self):
        self.base_url = os.getenv("MAXOTEL_API_URL", "https://api.maxotel.com.au")
        self.api_key = os.getenv("MAXOTEL_API_KEY")
        self.reseller_id = os.getenv("MAXOTEL_RESELLER_ID")

    async def request(self, method: str, endpoint: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["Content-Type"] = "application/json"
        if self.reseller_id:
            headers["X-Reseller-ID"] = self.reseller_id
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

maxotel_client = MaxotelClient()

# ============================================================================
# UBUNTU SERVER (SSH) INTEGRATION
# ============================================================================

class SSHClient:
    def __init__(self):
        self.host = os.getenv("SSH_HOST")
        self.port = int(os.getenv("SSH_PORT", "22"))
        self.username = os.getenv("SSH_USERNAME")
        self.password = os.getenv("SSH_PASSWORD")
        self.key_path = os.getenv("SSH_KEY_PATH")

# Note: SSH integration uses mcp__26ba7250-8900-4d35-9837-bd8c326ac6c2__ubuntu_* tools directly

# ============================================================================
# CIPP (M365) INTEGRATION
# ============================================================================

class CIPPClient:
    def __init__(self):
        self.base_url = os.getenv("CIPP_API_URL", "https://api.cipp.app")
        self.api_key = os.getenv("CIPP_API_KEY")

    async def request(self, method: str, endpoint: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["X-API-Key"] = self.api_key
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

cipp_client = CIPPClient()

# ============================================================================
# SALESFORCE INTEGRATION
# ============================================================================

class SalesforceClient:
    def __init__(self):
        self.base_url = os.getenv("SALESFORCE_INSTANCE_URL")
        self.client_id = os.getenv("SALESFORCE_CLIENT_ID")
        self.client_secret = os.getenv("SALESFORCE_CLIENT_SECRET")
        self.username = os.getenv("SALESFORCE_USERNAME")
        self.password = os.getenv("SALESFORCE_PASSWORD")
        self._access_token = None
        self._token_expires = None

    async def get_access_token(self):
        if self._access_token and self._token_expires and datetime.now() < self._token_expires:
            return self._access_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/services/oauth2/token",
                data={
                    "grant_type": "password",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "username": self.username,
                    "password": self.password
                }
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            self._token_expires = datetime.now() + timedelta(hours=1)
            return self._access_token

    async def request(self, method: str, endpoint: str, **kwargs):
        token = await self.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

salesforce_client = SalesforceClient()

# ============================================================================
# N8N WORKFLOW AUTOMATION INTEGRATION
# ============================================================================

class N8NClient:
    def __init__(self):
        self.base_url = os.getenv("N8N_API_URL")
        self.api_key = os.getenv("N8N_API_KEY")

    async def request(self, method: str, endpoint: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["X-N8N-API-KEY"] = self.api_key
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

n8n_client = N8NClient()

# ============================================================================
# BIGQUERY INTEGRATION
# ============================================================================

class BigQueryClient:
    def __init__(self):
        self.project_id = os.getenv("GCP_PROJECT_ID")

# Note: BigQuery integration uses Google Cloud libraries directly

# ============================================================================
# GCLOUD CLI INTEGRATION
# ============================================================================

# Note: GCloud CLI integration uses subprocess and shell commands directly

# ============================================================================
# AZURE INTEGRATION
# ============================================================================

# Note: Azure integration uses mcp tools directly

# ============================================================================
# AWS INTEGRATION
# ============================================================================

# Note: AWS integration uses mcp tools directly

# ============================================================================
# DICKER DATA INTEGRATION
# ============================================================================

class DickerDataClient:
    def __init__(self):
        self.base_url = "https://api.dickerdata.com.au"
        self.api_key = os.getenv("DICKER_DATA_API_KEY")
        self.client_id = os.getenv("DICKER_DATA_CLIENT_ID")

    async def request(self, method: str, endpoint: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["X-Client-ID"] = self.client_id
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

dicker_data_client = DickerDataClient()

# ============================================================================
# INGRAM MICRO INTEGRATION
# ============================================================================

class IngramMicroClient:
    def __init__(self):
        self.base_url = "https://api.ingrammicro.com"
        self.client_id = os.getenv("INGRAM_MICRO_CLIENT_ID")
        self.client_secret = os.getenv("INGRAM_MICRO_CLIENT_SECRET")
        self._access_token = None
        self._token_expires = None

    async def get_access_token(self):
        if self._access_token and self._token_expires and datetime.now() < self._token_expires:
            return self._access_token

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/oauth/oauth20/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                }
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            self._token_expires = datetime.now() + timedelta(seconds=data["expires_in"] - 60)
            return self._access_token

    async def request(self, method: str, endpoint: str, **kwargs):
        token = await self.get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers["Content-Type"] = "application/json"
        
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.base_url}{endpoint}",
                headers=headers,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

ingram_micro_client = IngramMicroClient()

# ============================================================================
# AUSSIE BROADBAND CARBON INTEGRATION
# ============================================================================

# Note: Aussie Broadband Carbon integration uses mcp tools directly

# ============================================================================
# NINJAONE INTEGRATION
# ============================================================================

# Note: NinjaOne integration uses mcp tools directly

# ============================================================================
# AUVIK INTEGRATION
# ============================================================================

# Note: Auvik integration uses mcp tools directly

# ============================================================================
# METABASE INTEGRATION
# ============================================================================

# Note: Metabase integration uses mcp tools directly

# ============================================================================
# JIRA INTEGRATION
# ============================================================================

# Note: Jira integration uses mcp tools directly

# ============================================================================
# LINEAR INTEGRATION
# ============================================================================

# Note: Linear integration uses mcp tools directly

# ============================================================================
# DIGITALOCEAN INTEGRATION
# ============================================================================

# Note: DigitalOcean integration uses mcp tools directly

# ============================================================================
# HALOPSA TOOLS
# ============================================================================

@mcp.tool()
async def halopsa_get_clients(
    limit: int = 20,
    search: Optional[str] = None
) -> list[dict]:
    """List HaloPSA clients."""
    try:
        params = {"pageSize": limit}
        if search:
            params["search"] = search
        
        result = await halopsa_client.request(
            "GET",
            "/crm_accounts",
            params=params
        )
        return result.get("accounts", [])
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA clients: {str(e)}")

@mcp.tool()
async def halopsa_get_client(client_id: int) -> dict:
    """Get detailed HaloPSA client information."""
    try:
        result = await halopsa_client.request(
            "GET",
            f"/crm_accounts/{client_id}"
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA client: {str(e)}")

@mcp.tool()
async def halopsa_get_tickets(
    client_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20
) -> list[dict]:
    """Search HaloPSA tickets."""
    try:
        params = {"pageSize": limit}
        if client_id:
            params["clientId"] = client_id
        if status:
            params["status"] = status
        
        result = await halopsa_client.request(
            "GET",
            "/tickets",
            params=params
        )
        return result.get("tickets", [])
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA tickets: {str(e)}")

@mcp.tool()
async def halopsa_create_ticket(
    client_id: int,
    summary: str,
    details: str,
    priority_id: int = 3
) -> dict:
    """Create a new HaloPSA ticket."""
    try:
        payload = {
            "clientId": client_id,
            "summary": summary,
            "details": details,
            "priorityId": priority_id
        }
        
        result = await halopsa_client.request(
            "POST",
            "/tickets",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to create HaloPSA ticket: {str(e)}")

@mcp.tool()
async def halopsa_add_action(
    ticket_id: int,
    note: str,
    time_taken: int = 0
) -> dict:
    """Add an action/note to a HaloPSA ticket."""
    try:
        payload = {
            "note": note,
            "timeTaken": time_taken
        }
        
        result = await halopsa_client.request(
            "POST",
            f"/tickets/{ticket_id}/actions",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to add HaloPSA action: {str(e)}")

@mcp.tool()
async def halopsa_get_invoices(
    client_id: Optional[int] = None,
    days: int = 90,
    limit: int = 50
) -> list[dict]:
    """Get HaloPSA invoices."""
    try:
        params = {"pageSize": limit}
        if client_id:
            params["clientId"] = client_id
        
        result = await halopsa_client.request(
            "GET",
            "/invoices",
            params=params
        )
        invoices = result.get("invoices", [])
        
        # Filter by date if needed
        if days:
            cutoff = datetime.now() - timedelta(days=days)
            invoices = [
                inv for inv in invoices 
                if datetime.fromisoformat(inv.get("invoiceDate", "")) > cutoff
            ]
        
        return invoices
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA invoices: {str(e)}")

@mcp.tool()
async def halopsa_get_items(
    search: Optional[str] = None,
    category_id: Optional[int] = None,
    limit: int = 50
) -> list[dict]:
    """List HaloPSA items/products."""
    try:
        params = {"pageSize": limit}
        if search:
            params["search"] = search
        if category_id:
            params["categoryId"] = category_id
        
        result = await halopsa_client.request(
            "GET",
            "/inventory/items",
            params=params
        )
        return result.get("items", [])
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA items: {str(e)}")

@mcp.tool()
async def halopsa_adjust_item_stock(
    item_id: int,
    quantity: int,
    note: str = ""
) -> dict:
    """Adjust stock level for a HaloPSA item."""
    try:
        payload = {
            "quantity": quantity,
            "note": note
        }
        
        result = await halopsa_client.request(
            "POST",
            f"/inventory/items/{item_id}/adjust-stock",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to adjust HaloPSA item stock: {str(e)}")

@mcp.tool()
async def halopsa_get_assets(
    client_id: Optional[int] = None,
    asset_type: Optional[str] = None,
    limit: int = 50
) -> list[dict]:
    """List HaloPSA assets/configuration items."""
    try:
        params = {"pageSize": limit}
        if client_id:
            params["clientId"] = client_id
        if asset_type:
            params["assetType"] = asset_type
        
        result = await halopsa_client.request(
            "GET",
            "/assets",
            params=params
        )
        return result.get("assets", [])
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA assets: {str(e)}")

@mcp.tool()
async def halopsa_get_contracts(
    client_id: Optional[int] = None,
    active_only: bool = True,
    limit: int = 50
) -> list[dict]:
    """List HaloPSA contracts/recurring invoices."""
    try:
        params = {"pageSize": limit}
        if client_id:
            params["clientId"] = client_id
        
        result = await halopsa_client.request(
            "GET",
            "/recurring-invoices",
            params=params
        )
        
        contracts = result.get("recurringInvoices", [])
        if active_only:
            contracts = [c for c in contracts if c.get("status") == "ACTIVE"]
        
        return contracts
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA contracts: {str(e)}")

@mcp.tool()
async def halopsa_get_agents() -> list[dict]:
    """List HaloPSA agents/technicians."""
    try:
        result = await halopsa_client.request(
            "GET",
            "/users",
            params={"userType": "AGENT"}
        )
        return result.get("users", [])
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA agents: {str(e)}")

@mcp.tool()
async def halopsa_create_item(
    name: str,
    baseprice: float = 0,
    cost: float = 0,
    item_type_id: int = 1,
    sku: Optional[str] = None
) -> dict:
    """Create a new HaloPSA item."""
    try:
        payload = {
            "name": name,
            "basePrice": baseprice,
            "cost": cost,
            "itemTypeId": item_type_id
        }
        if sku:
            payload["sku"] = sku
        
        result = await halopsa_client.request(
            "POST",
            "/inventory/items",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to create HaloPSA item: {str(e)}")

@mcp.tool()
async def halopsa_get_recurring_invoices(
    client_id: Optional[int] = None,
    active_only: bool = True,
    limit: int = 50
) -> list[dict]:
    """Get HaloPSA recurring invoices."""
    try:
        params = {"pageSize": limit}
        if client_id:
            params["clientId"] = client_id
        
        result = await halopsa_client.request(
            "GET",
            "/recurring-invoices",
            params=params
        )
        
        invoices = result.get("recurringInvoices", [])
        if active_only:
            invoices = [inv for inv in invoices if inv.get("status") == "ACTIVE"]
        
        return invoices
    except Exception as e:
        raise Exception(f"Failed to fetch HaloPSA recurring invoices: {str(e)}")

@mcp.tool()
async def halopsa_update_recurring_invoice(
    recurring_invoice_id: int,
    invoice_name: Optional[str] = None,
    po_number: Optional[str] = None,
    notes: Optional[str] = None
) -> dict:
    """Update a HaloPSA recurring invoice."""
    try:
        payload = {}
        if invoice_name:
            payload["invoiceName"] = invoice_name
        if po_number:
            payload["poNumber"] = po_number
        if notes:
            payload["notes"] = notes
        
        result = await halopsa_client.request(
            "PATCH",
            f"/recurring-invoices/{recurring_invoice_id}",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to update HaloPSA recurring invoice: {str(e)}")

@mcp.tool()
async def halopsa_add_recurring_invoice_line(
    recurring_invoice_id: int,
    description: str,
    unit_price: float,
    quantity: float = 1,
    tax_code: str = "GST"
) -> dict:
    """Add a line item to a HaloPSA recurring invoice."""
    try:
        payload = {
            "description": description,
            "unitPrice": unit_price,
            "quantity": quantity,
            "taxCode": tax_code
        }
        
        result = await halopsa_client.request(
            "POST",
            f"/recurring-invoices/{recurring_invoice_id}/lines",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to add HaloPSA recurring invoice line: {str(e)}")

# ============================================================================
# XERO TOOLS
# ============================================================================

@mcp.tool()
async def xero_get_contacts(
    is_customer: bool = True,
    is_supplier: bool = False,
    limit: int = 50,
    search: Optional[str] = None
) -> list[dict]:
    """List Xero contacts."""
    try:
        filters = []
        if is_customer:
            filters.append("ContactStatus==\"ACTIVE\"")
        
        where = " && ".join(filters) if filters else None
        
        params = {
            "where": where,
            "order": "Name"
        }
        
        result = await xero_client.request(
            "GET",
            "/Contacts",
            params=params
        )
        contacts = result.get("Contacts", [])
        
        if search:
            contacts = [c for c in contacts if search.lower() in c.get("Name", "").lower()]
        
        return contacts[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero contacts: {str(e)}")

@mcp.tool()
async def xero_create_contact(
    name: str,
    is_customer: bool = True,
    is_supplier: bool = False,
    email: Optional[str] = None,
    phone: Optional[str] = None
) -> dict:
    """Create a new Xero contact."""
    try:
        payload = {
            "Name": name,
            "ContactStatus": "ACTIVE"
        }
        
        if email:
            payload["EmailAddress"] = email
        if phone:
            payload["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
        
        result = await xero_client.request(
            "POST",
            "/Contacts",
            json={"Contacts": [payload]}
        )
        return result.get("Contacts", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to create Xero contact: {str(e)}")

@mcp.tool()
async def xero_get_invoices(
    contact_name: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 90,
    limit: int = 20
) -> list[dict]:
    """Get Xero invoices."""
    try:
        filters = []
        
        if status:
            filters.append(f'Status="{status}"')
        
        where = " && ".join(filters) if filters else None
        
        params = {
            "order": "InvoiceNumber DESC"
        }
        if where:
            params["where"] = where
        
        result = await xero_client.request(
            "GET",
            "/Invoices",
            params=params
        )
        
        invoices = result.get("Invoices", [])
        
        if contact_name:
            invoices = [
                inv for inv in invoices 
                if contact_name.lower() in inv.get("Contact", {}).get("Name", "").lower()
            ]
        
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            invoices = [
                inv for inv in invoices 
                if inv.get("Status") != "DRAFT" and 
                datetime.fromisoformat(inv.get("InvoiceNumber", "0").replace("Z", "+00:00")) > cutoff
            ]
        
        return invoices[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero invoices: {str(e)}")

@mcp.tool()
async def xero_create_invoice(
    contact_name: str,
    line_items: str,
    due_days: int = 30,
    status: str = "DRAFT",
    reference: Optional[str] = None
) -> dict:
    """Create a new Xero invoice."""
    try:
        # Get contact
        contacts = await xero_get_contacts(search=contact_name)
        if not contacts:
            raise Exception(f"Contact '{contact_name}' not found")
        
        contact_id = contacts[0]["ContactID"]
        
        # Parse line items
        import json as json_module
        items = json_module.loads(line_items)
        
        line_items_list = []
        for item in items:
            line_items_list.append({
                "Description": item["description"],
                "Quantity": item["quantity"],
                "UnitAmount": item["unit_amount"],
                "AccountCode": item["account_code"]
            })
        
        payload = {
            "Type": "ACCREC",
            "Contact": {"ContactID": contact_id},
            "LineItems": line_items_list,
            "Status": status,
            "DueDate": (datetime.now() + timedelta(days=due_days)).isoformat()
        }
        
        if reference:
            payload["Reference"] = reference
        
        result = await xero_client.request(
            "POST",
            "/Invoices",
            json={"Invoices": [payload]}
        )
        return result.get("Invoices", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to create Xero invoice: {str(e)}")

@mcp.tool()
async def xero_get_accounts() -> list[dict]:
    """Get Xero chart of accounts."""
    try:
        result = await xero_client.request(
            "GET",
            "/Accounts"
        )
        return result.get("Accounts", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Xero accounts: {str(e)}")

@mcp.tool()
async def xero_get_bank_summary() -> dict:
    """Get Xero bank accounts summary."""
    try:
        result = await xero_client.request(
            "GET",
            "/Accounts",
            params={"where": 'Type=="BANK"'}
        )
        accounts = result.get("Accounts", [])
        
        summary = {}
        for account in accounts:
            summary[account.get("Name")] = {
                "code": account.get("Code"),
                "balance": account.get("CurrentAccountBalance")
            }
        
        return summary
    except Exception as e:
        raise Exception(f"Failed to fetch Xero bank summary: {str(e)}")

@mcp.tool()
async def xero_update_contact(
    contact_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    is_customer: Optional[bool] = None,
    is_supplier: Optional[bool] = None,
    contact_status: Optional[str] = None,
    account_number: Optional[str] = None
) -> dict:
    """Update an existing Xero contact."""
    try:
        payload = {}
        
        if name:
            payload["Name"] = name
        if email:
            payload["EmailAddress"] = email
        if phone:
            payload["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
        if first_name or last_name:
            payload["FirstName"] = first_name or ""
            payload["LastName"] = last_name or ""
        if contact_status:
            payload["ContactStatus"] = contact_status
        if account_number:
            payload["AccountNumber"] = account_number
        
        result = await xero_client.request(
            "POST",
            f"/Contacts/{contact_id}",
            json={"Contacts": [payload]}
        )
        return result.get("Contacts", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to update Xero contact: {str(e)}")

@mcp.tool()
async def xero_bulk_update_contacts(updates: str) -> list[dict]:
    """Bulk update multiple Xero contacts."""
    try:
        import json as json_module
        contact_updates = json_module.loads(updates)
        
        results = []
        for update in contact_updates:
            contact_id = update.pop("contact_id")
            result = await xero_update_contact(contact_id, **update)
            results.append(result)
        
        return results
    except Exception as e:
        raise Exception(f"Failed to bulk update Xero contacts: {str(e)}")

@mcp.tool()
async def xero_get_items(limit: int = 50, search: Optional[str] = None) -> list[dict]:
    """Get Xero items/products."""
    try:
        result = await xero_client.request(
            "GET",
            "/Items"
        )
        items = result.get("Items", [])
        
        if search:
            items = [i for i in items if search.lower() in i.get("Name", "").lower()]
        
        return items[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero items: {str(e)}")

@mcp.tool()
async def xero_get_tax_rates() -> list[dict]:
    """Get Xero tax rates."""
    try:
        result = await xero_client.request(
            "GET",
            "/TaxRates"
        )
        return result.get("TaxRates", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Xero tax rates: {str(e)}")

@mcp.tool()
async def xero_create_payment(
    invoice_id: str,
    amount: float,
    account_code: str,
    date: Optional[str] = None,
    reference: Optional[str] = None
) -> dict:
    """Record a payment against an invoice."""
    try:
        payload = {
            "Invoice": {"InvoiceID": invoice_id},
            "Amount": amount,
            "Account": {"Code": account_code}
        }
        
        if date:
            payload["HasErrors"] = False
        if reference:
            payload["Reference"] = reference
        
        result = await xero_client.request(
            "POST",
            "/Payments",
            json={"Payments": [payload]}
        )
        return result.get("Payments", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to create Xero payment: {str(e)}")

@mcp.tool()
async def xero_get_bills(
    contact_name: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 90,
    limit: int = 20
) -> list[dict]:
    """Get Xero supplier bills."""
    try:
        filters = []
        
        if status:
            filters.append(f'Status="{status}"')
        
        where = " && ".join(filters) if filters else None
        
        params = {"order": "InvoiceNumber DESC"}
        if where:
            params["where"] = where
        
        result = await xero_client.request(
            "GET",
            "/Invoices",
            params=params
        )
        
        invoices = result.get("Invoices", [])
        
        if contact_name:
            invoices = [
                inv for inv in invoices 
                if contact_name.lower() in inv.get("Contact", {}).get("Name", "").lower()
            ]
        
        return invoices[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero bills: {str(e)}")

@mcp.tool()
async def xero_create_bill(
    contact_name: str,
    line_items: str,
    due_days: int = 30,
    invoice_number: Optional[str] = None,
    status: str = "DRAFT"
) -> dict:
    """Create a supplier bill in Xero."""
    try:
        contacts = await xero_get_contacts(search=contact_name)
        if not contacts:
            raise Exception(f"Supplier '{contact_name}' not found")
        
        contact_id = contacts[0]["ContactID"]
        
        import json as json_module
        items = json_module.loads(line_items)
        
        line_items_list = []
        for item in items:
            line_items_list.append({
                "Description": item["description"],
                "Quantity": item["quantity"],
                "UnitAmount": item["unit_amount"],
                "AccountCode": item["account_code"]
            })
        
        payload = {
            "Type": "ACCPAY",
            "Contact": {"ContactID": contact_id},
            "LineItems": line_items_list,
            "Status": status,
            "DueDate": (datetime.now() + timedelta(days=due_days)).isoformat()
        }
        
        if invoice_number:
            payload["InvoiceNumber"] = invoice_number
        
        result = await xero_client.request(
            "POST",
            "/Invoices",
            json={"Invoices": [payload]}
        )
        return result.get("Invoices", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to create Xero bill: {str(e)}")

@mcp.tool()
async def xero_create_credit_note(
    contact_name: str,
    line_items: str,
    credit_note_type: str = "ACCRECCREDIT",
    reference: Optional[str] = None,
    status: str = "DRAFT"
) -> dict:
    """Create a credit note in Xero."""
    try:
        contacts = await xero_get_contacts(search=contact_name)
        if not contacts:
            raise Exception(f"Contact '{contact_name}' not found")
        
        contact_id = contacts[0]["ContactID"]
        
        import json as json_module
        items = json_module.loads(line_items)
        
        line_items_list = []
        for item in items:
            line_items_list.append({
                "Description": item["description"],
                "Quantity": item["quantity"],
                "UnitAmount": item["unit_amount"],
                "AccountCode": item["account_code"]
            })
        
        payload = {
            "Type": credit_note_type,
            "Contact": {"ContactID": contact_id},
            "LineItems": line_items_list,
            "Status": status
        }
        
        if reference:
            payload["Reference"] = reference
        
        result = await xero_client.request(
            "POST",
            "/CreditNotes",
            json={"CreditNotes": [payload]}
        )
        return result.get("CreditNotes", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to create Xero credit note: {str(e)}")

@mcp.tool()
async def xero_get_credit_notes(
    contact_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20
) -> list[dict]:
    """Get Xero credit notes."""
    try:
        result = await xero_client.request(
            "GET",
            "/CreditNotes"
        )
        
        notes = result.get("CreditNotes", [])
        
        if contact_name:
            notes = [
                n for n in notes 
                if contact_name.lower() in n.get("Contact", {}).get("Name", "").lower()
            ]
        
        if status:
            notes = [n for n in notes if n.get("Status") == status]
        
        return notes[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero credit notes: {str(e)}")

@mcp.tool()
async def xero_get_purchase_orders(
    contact_name: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 90,
    limit: int = 20
) -> list[dict]:
    """Get Xero purchase orders."""
    try:
        # Note: This is a simplified implementation as Xero may not have a direct PO endpoint
        # You might need to use a different endpoint or method
        result = {"PurchaseOrders": []}
        
        orders = result.get("PurchaseOrders", [])
        
        if contact_name:
            orders = [
                o for o in orders 
                if contact_name.lower() in o.get("Contact", {}).get("Name", "").lower()
            ]
        
        return orders[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero purchase orders: {str(e)}")

@mcp.tool()
async def xero_create_purchase_order(
    contact_name: str,
    line_items: str,
    status: str = "DRAFT",
    reference: Optional[str] = None,
    delivery_date: Optional[str] = None
) -> dict:
    """Create a purchase order in Xero."""
    try:
        # Xero doesn't have a direct PO API in the standard endpoints
        # This is a placeholder implementation
        raise ToolUnavailableError("Purchase orders are not available through this API")
    except ToolUnavailableError as e:
        raise Exception(str(e))
    except Exception as e:
        raise Exception(f"Failed to create Xero purchase order: {str(e)}")

@mcp.tool()
async def xero_get_quotes(
    contact_name: Optional[str] = None,
    status: Optional[str] = None,
    days: int = 90,
    limit: int = 20
) -> list[dict]:
    """Get Xero quotes."""
    try:
        # Xero doesn't have a direct Quotes API in all versions
        result = {"Quotes": []}
        
        quotes = result.get("Quotes", [])
        
        if contact_name:
            quotes = [
                q for q in quotes 
                if contact_name.lower() in q.get("Contact", {}).get("Name", "").lower()
            ]
        
        return quotes[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero quotes: {str(e)}")

@mcp.tool()
async def xero_create_quote(
    contact_name: str,
    line_items: str,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    status: str = "DRAFT",
    expiry_days: int = 30
) -> dict:
    """Create a quote in Xero."""
    try:
        # Placeholder for quotes
        raise ToolUnavailableError("Quotes may not be available in your Xero setup")
    except ToolUnavailableError as e:
        raise Exception(str(e))
    except Exception as e:
        raise Exception(f"Failed to create Xero quote: {str(e)}")

@mcp.tool()
async def xero_update_invoice(
    invoice_id: str,
    status: Optional[str] = None,
    due_date: Optional[str] = None,
    reference: Optional[str] = None
) -> dict:
    """Update an existing Xero invoice."""
    try:
        payload = {}
        
        if status:
            payload["Status"] = status
        if due_date:
            payload["DueDate"] = due_date
        if reference:
            payload["Reference"] = reference
        
        result = await xero_client.request(
            "POST",
            f"/Invoices/{invoice_id}",
            json={"Invoices": [payload]}
        )
        return result.get("Invoices", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to update Xero invoice: {str(e)}")

@mcp.tool()
async def xero_update_invoice_lines(
    invoice_id: str,
    line_items: str
) -> dict:
    """Replace all line items on a DRAFT invoice."""
    try:
        import json as json_module
        items = json_module.loads(line_items)
        
        line_items_list = []
        for item in items:
            line_items_list.append({
                "Description": item["description"],
                "Quantity": item["quantity"],
                "UnitAmount": item["unit_amount"],
                "AccountCode": item["account_code"]
            })
        
        payload = {
            "LineItems": line_items_list
        }
        
        result = await xero_client.request(
            "POST",
            f"/Invoices/{invoice_id}",
            json={"Invoices": [payload]}
        )
        return result.get("Invoices", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to update Xero invoice lines: {str(e)}")

@mcp.tool()
async def xero_void_invoice(invoice_id: str) -> dict:
    """Void an invoice in Xero."""
    try:
        payload = {"Status": "VOIDED"}
        
        result = await xero_client.request(
            "POST",
            f"/Invoices/{invoice_id}",
            json={"Invoices": [payload]}
        )
        return result.get("Invoices", [{}])[0]
    except Exception as e:
        raise Exception(f"Failed to void Xero invoice: {str(e)}")

@mcp.tool()
async def xero_email_invoice(invoice_id: str) -> dict:
    """Email an invoice in Xero."""
    try:
        result = await xero_client.request(
            "POST",
            f"/Invoices/{invoice_id}/Email"
        )
        return {"success": True, "message": "Invoice emailed successfully"}
    except Exception as e:
        raise Exception(f"Failed to email Xero invoice: {str(e)}")

@mcp.tool()
async def xero_get_bank_transactions(
    transaction_type: Optional[str] = None,
    bank_account_code: Optional[str] = None,
    days: int = 30,
    limit: int = 50
) -> list[dict]:
    """Get Xero bank transactions."""
    try:
        result = await xero_client.request(
            "GET",
            "/BankTransactions"
        )
        
        transactions = result.get("BankTransactions", [])
        
        if transaction_type:
            transactions = [t for t in transactions if t.get("Type") == transaction_type]
        
        return transactions[:limit]
    except Exception as e:
        raise Exception(f"Failed to fetch Xero bank transactions: {str(e)}")

@mcp.tool()
async def xero_aged_receivables(
    contact_name: Optional[str] = None,
    min_amount: float = 0
) -> list[dict]:
    """Get Xero aged receivables."""
    try:
        invoices = await xero_get_invoices(contact_name=contact_name, status="AUTHORISED")
        
        aged = []
        now = datetime.now(timezone.utc)
        
        for invoice in invoices:
            due_date_str = invoice.get("DueDate")
            if due_date_str:
                try:
                    due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
                    days_overdue = (now - due_date).days
                    amount = invoice.get("Total", 0) - invoice.get("AmountPaid", 0)
                    
                    if amount >= min_amount and days_overdue > 0:
                        aged.append({
                            "invoice_id": invoice.get("InvoiceID"),
                            "invoice_number": invoice.get("InvoiceNumber"),
                            "contact": invoice.get("Contact", {}).get("Name"),
                            "amount": amount,
                            "days_overdue": days_overdue,
                            "due_date": due_date_str
                        })
                except:
                    pass
        
        return aged
    except Exception as e:
        raise Exception(f"Failed to fetch Xero aged receivables: {str(e)}")

@mcp.tool()
async def xero_aged_payables(
    contact_name: Optional[str] = None,
    min_amount: float = 0
) -> list[dict]:
    """Get Xero aged payables."""
    try:
        bills = await xero_get_bills(contact_name=contact_name, status="AUTHORISED")
        
        aged = []
        now = datetime.now(timezone.utc)
        
        for bill in bills:
            due_date_str = bill.get("DueDate")
            if due_date_str:
                try:
                    due_date = datetime.fromisoformat(due_date_str.replace("Z", "+00:00"))
                    days_overdue = (now - due_date).days
                    amount = bill.get("Total", 0) - bill.get("AmountPaid", 0)
                    
                    if amount >= min_amount and days_overdue > 0:
                        aged.append({
                            "invoice_id": bill.get("InvoiceID"),
                            "invoice_number": bill.get("InvoiceNumber"),
                            "contact": bill.get("Contact", {}).get("Name"),
                            "amount": amount,
                            "days_overdue": days_overdue,
                            "due_date": due_date_str
                        })
                except:
                    pass
        
        return aged
    except Exception as e:
        raise Exception(f"Failed to fetch Xero aged payables: {str(e)}")

@mcp.tool()
async def xero_balance_sheet(date: Optional[str] = None) -> dict:
    """Get Xero balance sheet."""
    try:
        # Using Reports endpoint
        params = {}
        if date:
            params["date"] = date
        
        result = await xero_client.request(
            "GET",
            "/Reports/BalanceSheet",
            params=params
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Xero balance sheet: {str(e)}")

@mcp.tool()
async def xero_profit_loss(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None
) -> dict:
    """Get Xero profit & loss report."""
    try:
        params = {}
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date
        
        result = await xero_client.request(
            "GET",
            "/Reports/ProfitAndLoss",
            params=params
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Xero profit & loss: {str(e)}")

@mcp.tool()
async def xero_trial_balance(date: Optional[str] = None) -> dict:
    """Get Xero trial balance."""
    try:
        params = {}
        if date:
            params["date"] = date
        
        result = await xero_client.request(
            "GET",
            "/Reports/TrialBalance",
            params=params
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Xero trial balance: {str(e)}")

# ============================================================================
# FRONT TOOLS
# ============================================================================

@mcp.tool()
async def front_list_inboxes() -> list[dict]:
    """List all Front inboxes."""
    try:
        result = await front_client.request(
            "GET",
            "/inboxes"
        )
        return result.get("_results", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Front inboxes: {str(e)}")

@mcp.tool()
async def front_list_conversations(
    inbox_id: Optional[str] = None,
    status: str = "open",
    limit: int = 20
) -> list[dict]:
    """List Front conversations."""
    try:
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        endpoint = "/conversations"
        if inbox_id:
            endpoint = f"/inboxes/{inbox_id}/conversations"
        
        result = await front_client.request(
            "GET",
            endpoint,
            params=params
        )
        return result.get("_results", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Front conversations: {str(e)}")

@mcp.tool()
async def front_get_conversation(conversation_id: str) -> dict:
    """Get detailed Front conversation information."""
    try:
        result = await front_client.request(
            "GET",
            f"/conversations/{conversation_id}"
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Front conversation: {str(e)}")

@mcp.tool()
async def front_search_conversations(
    query: str,
    limit: int = 20
) -> list[dict]:
    """Search Front conversations."""
    try:
        params = {"q": query, "limit": limit}
        
        result = await front_client.request(
            "GET",
            "/conversations",
            params=params
        )
        return result.get("_results", [])
    except Exception as e:
        raise Exception(f"Failed to search Front conversations: {str(e)}")

@mcp.tool()
async def front_list_tags() -> list[dict]:
    """List all Front tags."""
    try:
        result = await front_client.request(
            "GET",
            "/tags"
        )
        return result.get("_results", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Front tags: {str(e)}")

@mcp.tool()
async def front_add_tag(
    conversation_id: str,
    tag_name: str
) -> dict:
    """Add a tag to a Front conversation."""
    try:
        # First get the tag ID
        tags = await front_list_tags()
        tag_id = None
        for tag in tags:
            if tag.get("name") == tag_name:
                tag_id = tag.get("id")
                break
        
        if not tag_id:
            raise Exception(f"Tag '{tag_name}' not found")
        
        payload = {"tag_ids": [tag_id]}
        
        result = await front_client.request(
            "PATCH",
            f"/conversations/{conversation_id}",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to add Front tag: {str(e)}")

# ============================================================================
# QUOTER TOOLS
# ============================================================================

@mcp.tool()
async def quoter_list_contacts(
    limit: int = 50,
    page: int = 1,
    search: Optional[str] = None
) -> list[dict]:
    """List Quoter contacts."""
    try:
        params = {"limit": limit, "page": page}
        if search:
            params["search"] = search
        
        result = await quoter_client.request(
            "GET",
            "/contacts",
            params=params
        )
        return result.get("contacts", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter contacts: {str(e)}")

@mcp.tool()
async def quoter_create_contact(
    first_name: str,
    last_name: str,
    email: str,
    organization: Optional[str] = None,
    phone: Optional[str] = None,
    mobile_phone: Optional[str] = None,
    work_phone: Optional[str] = None,
    billing_address: Optional[str] = None,
    billing_city: Optional[str] = None,
    billing_region_iso: Optional[str] = None,
    billing_postal_code: Optional[str] = None,
    billing_country_iso: str = "AU",
    title: Optional[str] = None
) -> dict:
    """Create a Quoter contact."""
    try:
        payload = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email
        }
        
        if organization:
            payload["organization"] = organization
        if phone:
            payload["phone"] = phone
        if mobile_phone:
            payload["mobile_phone"] = mobile_phone
        if work_phone:
            payload["work_phone"] = work_phone
        if billing_address:
            payload["billing_address"] = billing_address
        if billing_city:
            payload["billing_city"] = billing_city
        if billing_region_iso:
            payload["billing_region_iso"] = billing_region_iso
        if billing_postal_code:
            payload["billing_postal_code"] = billing_postal_code
        if billing_country_iso:
            payload["billing_country_iso"] = billing_country_iso
        if title:
            payload["title"] = title
        
        result = await quoter_client.request(
            "POST",
            "/contacts",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to create Quoter contact: {str(e)}")

@mcp.tool()
async def quoter_get_contact(contact_id: str) -> dict:
    """Get detailed Quoter contact information."""
    try:
        result = await quoter_client.request(
            "GET",
            f"/contacts/{contact_id}"
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter contact: {str(e)}")

@mcp.tool()
async def quoter_update_contact(
    contact_id: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    organization: Optional[str] = None,
    phone: Optional[str] = None,
    mobile_phone: Optional[str] = None,
    work_phone: Optional[str] = None,
    billing_address: Optional[str] = None,
    billing_city: Optional[str] = None,
    billing_region_iso: Optional[str] = None,
    billing_postal_code: Optional[str] = None,
    billing_country_iso: Optional[str] = None
) -> dict:
    """Update a Quoter contact."""
    try:
        payload = {}
        
        if first_name is not None:
            payload["first_name"] = first_name
        if last_name is not None:
            payload["last_name"] = last_name
        if email is not None:
            payload["email"] = email
        if organization is not None:
            payload["organization"] = organization
        if phone is not None:
            payload["phone"] = phone
        if mobile_phone is not None:
            payload["mobile_phone"] = mobile_phone
        if work_phone is not None:
            payload["work_phone"] = work_phone
        if billing_address is not None:
            payload["billing_address"] = billing_address
        if billing_city is not None:
            payload["billing_city"] = billing_city
        if billing_region_iso is not None:
            payload["billing_region_iso"] = billing_region_iso
        if billing_postal_code is not None:
            payload["billing_postal_code"] = billing_postal_code
        if billing_country_iso is not None:
            payload["billing_country_iso"] = billing_country_iso
        
        result = await quoter_client.request(
            "PATCH",
            f"/contacts/{contact_id}",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to update Quoter contact: {str(e)}")

@mcp.tool()
async def quoter_list_quotes(
    limit: int = 50,
    page: int = 1,
    status: Optional[str] = None
) -> list[dict]:
    """List Quoter quotes."""
    try:
        params = {"limit": limit, "page": page}
        if status:
            params["status"] = status
        
        result = await quoter_client.request(
            "GET",
            "/quotes",
            params=params
        )
        return result.get("quotes", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter quotes: {str(e)}")

@mcp.tool()
async def quoter_create_quote(
    contact_id: str,
    name: Optional[str] = None,
    template_id: Optional[str] = None
) -> dict:
    """Create a Quoter quote."""
    try:
        payload = {"contact_id": contact_id}
        
        if name:
            payload["name"] = name
        if template_id:
            payload["template_id"] = template_id
        
        result = await quoter_client.request(
            "POST",
            "/quotes",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to create Quoter quote: {str(e)}")

@mcp.tool()
async def quoter_add_line_item(
    quote_id: str,
    description: str,
    unit_price: float = 0,
    quantity: int = 1,
    taxable: bool = True,
    optional: bool = False,
    hidden: bool = False,
    item_id: Optional[str] = None
) -> dict:
    """Add a line item to a Quoter quote."""
    try:
        payload = {
            "description": description,
            "unit_price": unit_price,
            "quantity": quantity,
            "taxable": taxable,
            "optional": optional,
            "hidden": hidden
        }
        
        if item_id:
            payload["item_id"] = item_id
        
        result = await quoter_client.request(
            "POST",
            f"/quotes/{quote_id}/line_items",
            json=payload
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to add Quoter line item: {str(e)}")

@mcp.tool()
async def quoter_list_items(
    limit: int = 50,
    page: int = 1,
    category_id: Optional[str] = None,
    search: Optional[str] = None
) -> list[dict]:
    """List Quoter items."""
    try:
        params = {"limit": limit, "page": page}
        if category_id:
            params["category_id"] = category_id
        if search:
            params["search"] = search
        
        result = await quoter_client.request(
            "GET",
            "/items",
            params=params
        )
        return result.get("items", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter items: {str(e)}")

@mcp.tool()
async def quoter_get_item(item_id: str) -> dict:
    """Get a Quoter item."""
    try:
        result = await quoter_client.request(
            "GET",
            f"/items/{item_id}"
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter item: {str(e)}")

@mcp.tool()
async def quoter_list_templates(
    limit: int = 50,
    page: int = 1
) -> list[dict]:
    """List Quoter templates."""
    try:
        params = {"limit": limit, "page": page}
        
        result = await quoter_client.request(
            "GET",
            "/templates",
            params=params
        )
        return result.get("templates", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter templates: {str(e)}")

@mcp.tool()
async def quoter_list_suppliers(
    limit: int = 50,
    page: int = 1
) -> list[dict]:
    """List Quoter suppliers."""
    try:
        params = {"limit": limit, "page": page}
        
        result = await quoter_client.request(
            "GET",
            "/suppliers",
            params=params
        )
        return result.get("suppliers", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter suppliers: {str(e)}")

@mcp.tool()
async def quoter_list_manufacturers(
    limit: int = 50,
    page: int = 1,
    search: Optional[str] = None
) -> list[dict]:
    """List Quoter manufacturers."""
    try:
        params = {"limit": limit, "page": page}
        if search:
            params["search"] = search
        
        result = await quoter_client.request(
            "GET",
            "/manufacturers",
            params=params
        )
        return result.get("manufacturers", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter manufacturers: {str(e)}")

@mcp.tool()
async def quoter_list_categories(
    limit: int = 100,
    page: int = 1
) -> list[dict]:
    """List Quoter categories."""
    try:
        params = {"limit": limit, "page": page}
        
        result = await quoter_client.request(
            "GET",
            "/categories",
            params=params
        )
        return result.get("categories", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Quoter categories: {str(e)}")

# ============================================================================
# PAX8 TOOLS
# ============================================================================

@mcp.tool()
async def pax8_list_companies(
    page: int = 0,
    size: int = 50,
    country: Optional[str] = None,
    city: Optional[str] = None
) -> list[dict]:
    """List Pax8 companies."""
    try:
        params = {"page": page, "size": size}
        if country:
            params["country"] = country
        if city:
            params["city"] = city
        
        result = await pax8_client.request(
            "GET",
            "/companies",
            params=params
        )
        return result.get("companies", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Pax8 companies: {str(e)}")

@mcp.tool()
async def pax8_get_company(company_id: str) -> dict:
    """Get a Pax8 company."""
    try:
        result = await pax8_client.request(
            "GET",
            f"/companies/{company_id}"
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Pax8 company: {str(e)}")

@mcp.tool()
async def pax8_list_subscriptions(
    company_id: Optional[str] = None,
    product_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 0,
    size: int = 50
) -> list[dict]:
    """List Pax8 subscriptions."""
    try:
        params = {"page": page, "size": size}
        if company_id:
            params["companyId"] = company_id
        if product_id:
            params["productId"] = product_id
        if status:
            params["status"] = status
        
        result = await pax8_client.request(
            "GET",
            "/subscriptions",
            params=params
        )
        return result.get("subscriptions", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Pax8 subscriptions: {str(e)}")

@mcp.tool()
async def pax8_get_subscription(subscription_id: str) -> dict:
    """Get a Pax8 subscription."""
    try:
        result = await pax8_client.request(
            "GET",
            f"/subscriptions/{subscription_id}"
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Pax8 subscription: {str(e)}")

@mcp.tool()
async def pax8_list_products(
    page: int = 0,
    size: int = 50,
    vendor_name: Optional[str] = None
) -> list[dict]:
    """List Pax8 products."""
    try:
        params = {"page": page, "size": size}
        if vendor_name:
            params["vendorName"] = vendor_name
        
        result = await pax8_client.request(
            "GET",
            "/products",
            params=params
        )
        return result.get("products", [])
    except Exception as e:
        raise Exception(f"Failed to fetch Pax8 products: {str(e)}")

@mcp.tool()
async def pax8_get_product(product_id: str) -> dict:
    """Get a Pax8 product."""
    try:
        result = await pax8_client.request(
            "GET",
            f"/products/{product_id}"
        )
        return result
    except Exception as e:
        raise Exception(f"Failed to fetch Pax8 product: {str(e)}")

# ============================================================================
# SHAREPOINT TOOLS
# ============================================================================

# Note: SharePoint integration uses mcp tools directly (sharepoint_*)

# ============================================================================
# MAXOTEL VOIP TOOLS
# ============================================================================

# Note: Maxotel integration uses mcp tools directly (maxotel_*)

# ============================================================================
# UBUNTU SERVER (SSH) TOOLS
# ============================================================================

# Note: Ubuntu/SSH integration uses mcp tools directly (ubuntu_*)

# ============================================================================
# CIPP (M365) TOOLS
# ============================================================================

# Note: CIPP integration uses mcp tools directly (cipp_*)

# ============================================================================
# SALESFORCE TOOLS
# ============================================================================

# Note: Salesforce integration uses mcp tools directly (salesforce_*)

# ============================================================================
# N8N TOOLS
# ============================================================================

# Note: n8n integration uses mcp tools directly (n8n_*)

# ============================================================================
# BIGQUERY TOOLS
# ============================================================================

# Note: BigQuery integration uses mcp tools directly (bigquery_*)

# ============================================================================
# GCLOUD CLI TOOLS
# ============================================================================

# Note: GCloud CLI integration uses mcp tools directly (gcp_*)

# ============================================================================
# AZURE TOOLS
# ============================================================================

# Note: Azure integration uses mcp tools directly (azure_*)

# ============================================================================
# AWS TOOLS
# ============================================================================

# Note: AWS integration uses mcp tools directly (aws_*)

# ============================================================================
# DICKER DATA TOOLS
# ============================================================================

# Note: Dicker Data integration uses mcp tools directly (dicker_*)

# ============================================================================
# INGRAM MICRO TOOLS
# ============================================================================

# Note: Ingram Micro integration uses mcp tools directly (ingram_*)

# ============================================================================
# AUSSIE BROADBAND CARBON TOOLS
# ============================================================================

# Note: Aussie Broadband Carbon integration uses mcp tools directly (carbon_*)

# ============================================================================
# NINJAONE TOOLS
# ============================================================================

# Note: NinjaOne integration uses mcp tools directly (ninjaone_*)

# ============================================================================
# AUVIK TOOLS
# ============================================================================

# Note: Auvik integration uses mcp tools directly (auvik_*)

# ============================================================================
# METABASE TOOLS
# ============================================================================

# Note: Metabase integration uses mcp tools directly (metabase_*)

# ============================================================================
# JIRA TOOLS
# ============================================================================

# Note: Jira integration uses mcp tools directly (jira_*)

# ============================================================================
# LINEAR TOOLS
# ============================================================================

# Note: Linear integration uses mcp tools directly (linear_*)

# ============================================================================
# DIGITALOCEAN TOOLS
# ============================================================================

# Note: DigitalOcean integration uses mcp tools directly (crowdit_do_*, digitalocean_*)

# ============================================================================
# GORELO TOOLS
# ============================================================================

# Note: Gorelo integration uses mcp tools directly (gorelo_*)

# ============================================================================
# FORTICLOUD TOOLS
# ============================================================================

# Note: FortiCloud integration uses mcp tools directly (forticloud_*)

# ============================================================================
# STARTUP AND SERVER
# ============================================================================

print(f"[STARTUP] All tools initialized at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)

# Health check endpoint (for Cloud Run)
@mcp.get("/health")
async def health():
    return {"status": "ok"}

# List available tools
@mcp.get("/tools")
async def list_tools():
    tools = []
    for name, func in mcp.tools.items():
        tools.append({
            "name": name,
            "description": func.__doc__ or "No description"
        })
    return {"tools": tools}

async def main():
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    
    print(f"[STARTUP] Starting server on port {port} at t={time.time() - _module_start_time:.3f}s", file=sys.stderr, flush=True)
    sys.stderr.flush()
    sys.stdout.flush()
    
    # Cloud Run optimized uvicorn configuration
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        timeout_keep_alive=5,  # Reduce keep-alive timeout
        # timeout_notify=30,     # Timeout for ASGI startup notification
        access_log=False,      # Disable access logs
        log_level="info"       # Set appropriate log level
    )

if __name__ == "__main__":
    # Convert to FastAPI app for uvicorn
    from fastapi import FastAPI
    
    # FastMCP should provide an ASGI app
    # If not, we wrap it
    if hasattr(mcp, 'app'):
        app = mcp.app
    else:
        app = FastAPI()
        app.include_router(mcp.router)
    
    # Run async main
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[SHUTDOWN] Keyboard interrupt received", file=sys.stderr, flush=True)
