"""
Xero Integration Tools for Crowd IT MCP Server

This module provides Xero accounting capabilities via the Xero API.

Capabilities:
- OAuth authentication flow (start and complete)
- Invoices: list, get details, create, update
- Contacts: search and list with filtering
- Aged receivables report

Authentication: Uses OAuth 2.0 with refresh token rotation.

Environment Variables:
    XERO_CLIENT_ID: OAuth client ID
    XERO_CLIENT_SECRET: OAuth client secret
    XERO_TENANT_ID: Organization tenant ID (or loaded from Secret Manager)
    XERO_REFRESH_TOKEN: OAuth refresh token (or loaded from Secret Manager)
"""

import os
import json
import logging
from typing import Optional
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

CLOUD_RUN_URL = os.getenv(
    "CLOUD_RUN_URL",
    "https://crowdit-mcp-server-348600156950.australia-southeast1.run.app",
)


# =============================================================================
# Configuration and Authentication
# =============================================================================

class XeroConfig:
    def __init__(self):
        self.client_id = os.getenv("XERO_CLIENT_ID", "")
        self.client_secret = os.getenv("XERO_CLIENT_SECRET", "")
        self._tenant_id: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    @property
    def tenant_id(self) -> str:
        if self._tenant_id:
            return self._tenant_id
        try:
            from app.core.config import get_secret_sync
            tid = get_secret_sync("XERO_TENANT_ID")
            if tid:
                self._tenant_id = tid
                return tid
        except Exception:
            pass
        self._tenant_id = os.getenv("XERO_TENANT_ID", "")
        return self._tenant_id

    @tenant_id.setter
    def tenant_id(self, value: str):
        self._tenant_id = value

    def _get_refresh_token(self) -> str:
        if self._refresh_token:
            return self._refresh_token
        try:
            from app.core.config import get_secret_sync
            token = get_secret_sync("XERO_REFRESH_TOKEN")
            if token:
                self._refresh_token = token
                logger.info("Loaded Xero refresh token from Secret Manager")
                return token
        except Exception:
            pass
        token = os.getenv("XERO_REFRESH_TOKEN", "")
        if token:
            self._refresh_token = token
            logger.info("Loaded Xero refresh token from environment variable")
        return token

    @property
    def is_configured(self) -> bool:
        return all([self.client_id, self.client_secret, self.tenant_id, self._get_refresh_token()])

    async def get_access_token(self) -> str:
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
                    "refresh_token": current_refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
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
                    try:
                        from app.core.config import update_secret_sync
                        update_secret_sync("XERO_REFRESH_TOKEN", new_refresh)
                        logger.info("Xero refresh token rotated and saved to Secret Manager")
                    except Exception:
                        logger.warning("Could not save rotated Xero refresh token to Secret Manager")

            expires_in = data.get("expires_in", 1800)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            return self._access_token


# =============================================================================
# Helpers
# =============================================================================

def _check_xero_response(response: httpx.Response) -> Optional[str]:
    if response.status_code >= 400:
        try:
            error_data = response.json()
            if "Message" in error_data:
                return f"Xero API Error: {response.status_code} - {error_data['Message']}"
            elif "Detail" in error_data:
                return f"Xero API Error: {response.status_code} - {error_data['Detail']}"
            elif "Elements" in error_data:
                elements = error_data.get("Elements", [])
                if elements and "ValidationErrors" in elements[0]:
                    errors = [e.get("Message", "") for e in elements[0]["ValidationErrors"]]
                    return f"Xero API Error: {response.status_code} - {'; '.join(errors)}"
        except Exception:
            pass

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
    import re

    guid_pattern = re.compile(
        r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    )

    if guid_pattern.match(invoice_id):
        return invoice_id

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": tenant_id,
        "Accept": "application/json",
    }

    url = f'https://api.xero.com/api.xro/2.0/Invoices?where=InvoiceNumber=="{invoice_id}"'

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise Exception(f"Xero API Error: {response.status_code} - {response.text}")
        data = response.json()

    invoices = data.get("Invoices", [])
    if not invoices:
        raise Exception(f"Invoice '{invoice_id}' not found")

    return invoices[0]["InvoiceID"]


# =============================================================================
# Tool Registration
# =============================================================================

def register_xero_tools(mcp, xero_config: 'XeroConfig'):
    """Register all Xero tools with the MCP server."""

    try:
        from pydantic import Field
    except ImportError:
        from dataclasses import field as Field

    # =========================================================================
    # AUTHENTICATION
    # =========================================================================

    @mcp.tool(
        name="xero_auth_start",
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
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
            f"&scope=offline_access openid profile email accounting.transactions accounting.contacts accounting.reports.read accounting.settings.read accounting.settings accounting.attachments accounting.journals.read"
            f"&state=crowdit"
        )

        return f"""## Xero Authorization Required

**Click this link to authorize:**
{auth_url}

After authorizing, you'll be redirected back automatically and Xero will be connected.

If you see an error page, make sure the redirect URI in your Xero app settings is set to:
`{redirect_uri}`"""

    @mcp.tool(
        name="xero_auth_complete",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_auth_complete(
        auth_code: str = Field(..., description="Authorization code from callback URL"),
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
                        "redirect_uri": redirect_uri,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if response.status_code >= 400:
                    return f"Xero API Error: {response.status_code} - {response.text}"
                tokens = response.json()

                access_token = tokens["access_token"]
                refresh_token = tokens["refresh_token"]

                tenant_response = await client.get(
                    "https://api.xero.com/connections",
                    headers={"Authorization": f"Bearer {access_token}"},
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

            saved_refresh = False
            saved_tenant = False
            try:
                from app.core.config import update_secret_sync
                saved_refresh = update_secret_sync("XERO_REFRESH_TOKEN", refresh_token)
                saved_tenant = update_secret_sync("XERO_TENANT_ID", tenant_id)
            except Exception:
                pass

            if saved_refresh and saved_tenant:
                return f"""Xero connected successfully!

**Organization:** {org_name}
**Tenant ID:** {tenant_id}

Tokens have been automatically saved to Secret Manager."""
            else:
                return f"""Xero connected for this session!

**Organization:** {org_name}

To persist, run:
```bash
echo -n "{refresh_token}" | gcloud secrets versions add XERO_REFRESH_TOKEN --data-file=- --project=crowdmcp
echo -n "{tenant_id}" | gcloud secrets versions add XERO_TENANT_ID --data-file=- --project=crowdmcp
```"""

        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # INVOICES
    # =========================================================================

    @mcp.tool(
        name="xero_get_invoices",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_invoices(
        status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'PAID', 'VOIDED'"),
        contact_name: Optional[str] = Field(None, description="Filter by contact name (partial match)"),
        days: int = Field(90, description="Invoices from last N days"),
        limit: int = Field(20, description="Max results"),
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
                        "Accept": "application/json",
                    },
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

    @mcp.tool(
        name="xero_get_invoice",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_invoice(
        invoice_id: str = Field(..., description="Invoice ID (GUID)"),
    ) -> str:
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
                        "Accept": "application/json",
                    },
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

    @mcp.tool(
        name="xero_create_invoice",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_invoice(
        contact_name: str = Field(..., description="Contact/customer name (must exist in Xero)"),
        line_items: str = Field(..., description='JSON array of line items: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]'),
        reference: Optional[str] = Field(None, description="Invoice reference"),
        due_days: int = Field(30, description="Days until due"),
        status: str = Field("DRAFT", description="Status: 'DRAFT' or 'AUTHORISED'"),
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
                        "Accept": "application/json",
                    },
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
                        "AccountCode": item.get("account_code", "200"),
                    }
                    for item in items
                ],
                "Status": status.upper(),
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
                        "Content-Type": "application/json",
                    },
                )
                error = _check_xero_response(response)
                if error:
                    return error
                created = response.json().get("Invoices", [{}])[0]

            return f"Invoice created: **{created.get('InvoiceNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in line_items."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_update_invoice",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_update_invoice(
        invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number (e.g., INV-6476)"),
        reference: Optional[str] = Field(None, description="Update invoice reference"),
        status: Optional[str] = Field(None, description="Update status: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'VOIDED'"),
        due_date: Optional[str] = Field(None, description="Update due date (YYYY-MM-DD format)"),
    ) -> str:
        """Update an existing Xero invoice (reference, status, or due date)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."

        try:
            token = await xero_config.get_access_token()

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
                        "Content-Type": "application/json",
                    },
                )
                error = _check_xero_response(response)
                if error:
                    return error
                updated = response.json().get("Invoices", [{}])[0]

            return f"Invoice **{updated.get('InvoiceNumber', invoice_id)}** updated."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # CONTACTS
    # =========================================================================

    @mcp.tool(
        name="xero_get_contacts",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_contacts(
        search: Optional[str] = Field(None, description="Search by name (partial match)"),
        is_customer: bool = Field(True, description="Filter to customers only"),
        is_supplier: bool = Field(False, description="Filter to suppliers only"),
        include_archived: bool = Field(False, description="Include archived contacts"),
        limit: int = Field(50, description="Max results (1-100)"),
    ) -> str:
        """List Xero contacts with ContactIDs. Use for updates or invoice creation."""
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
            if is_supplier:
                where_parts.append("IsSupplier==true")
            if not include_archived:
                where_parts.append('ContactStatus!="ARCHIVED"')
            if where_parts:
                params["where"] = " AND ".join(where_parts)

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.xero.com/api.xro/2.0/Contacts",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Xero-Tenant-Id": xero_config.tenant_id,
                        "Accept": "application/json",
                    },
                )
                error = _check_xero_response(response)
                if error:
                    return error
                contacts = response.json().get("Contacts", [])[:limit]

            if not contacts:
                return "No contacts found."

            results = []
            for c in contacts:
                contact_id = c.get("ContactID", "N/A")
                name = c.get("Name", "Unknown")
                first_name = c.get("FirstName", "")
                last_name = c.get("LastName", "")
                email = c.get("EmailAddress", "N/A")
                balance = c.get("Balances", {}).get("AccountsReceivable", {}).get("Outstanding", 0)

                person_name = f"{first_name} {last_name}".strip() if first_name or last_name else "N/A"
                results.append(f"- **{name}** (ID: `{contact_id}`)\n  Contact: {person_name} | Email: {email} | Outstanding: ${balance:,.2f}")

            return f"## Contacts ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # REPORTS
    # =========================================================================

    @mcp.tool(
        name="xero_aged_receivables",
        annotations={"readOnlyHint": True},
    )
    async def xero_aged_receivables(
        contact_name: Optional[str] = Field(None, description="Filter by contact name"),
        min_amount: float = Field(0, description="Minimum amount outstanding"),
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
                        "Accept": "application/json",
                    },
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
