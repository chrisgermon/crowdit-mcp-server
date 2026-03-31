"""
Xero Integration Tools for Crowd IT MCP Server

This module provides comprehensive Xero accounting capabilities via the Xero API.

Capabilities:
- OAuth authentication flow (start and complete)
- Invoices: list, get details, create, update, void, email
- Contacts: list, get, create, update, archive
- Payments: list, get, create, delete
- Credit Notes: list, get, create
- Bank Transactions: list, get, create
- Purchase Orders: list, get, create, update
- Quotes: list, get, create, update
- Accounts (Chart of Accounts): list, get, create, update
- Items: list, get, create, update, delete
- Manual Journals: list, get, create
- Employees: list, get, create, update
- Tax Rates: list
- Currencies: list
- Tracking Categories: list
- Branding Themes: list
- Organisation: get details
- Overpayments & Prepayments: list
- Attachments: list, get, upload
- Reports: Aged Receivables, Aged Payables, P&L, Balance Sheet, Trial Balance,
  Bank Summary, Budget Summary, Executive Summary

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

XERO_API_BASE = "https://api.xero.com/api.xro/2.0"


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

    url = f'{XERO_API_BASE}/Invoices?where=InvoiceNumber=="{invoice_id}"'

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise Exception(f"Xero API Error: {response.status_code} - {response.text}")
        data = response.json()

    invoices = data.get("Invoices", [])
    if not invoices:
        raise Exception(f"Invoice '{invoice_id}' not found")

    return invoices[0]["InvoiceID"]


async def _xero_get(token: str, tenant_id: str, endpoint: str, params: dict = None) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(
            f"{XERO_API_BASE}/{endpoint}",
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Xero-Tenant-Id": tenant_id,
                "Accept": "application/json",
            },
        )


async def _xero_post(token: str, tenant_id: str, endpoint: str, json_data: dict) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(
            f"{XERO_API_BASE}/{endpoint}",
            json=json_data,
            headers={
                "Authorization": f"Bearer {token}",
                "Xero-Tenant-Id": tenant_id,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )


async def _xero_put(token: str, tenant_id: str, endpoint: str, json_data: dict) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.put(
            f"{XERO_API_BASE}/{endpoint}",
            json=json_data,
            headers={
                "Authorization": f"Bearer {token}",
                "Xero-Tenant-Id": tenant_id,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )


async def _xero_delete(token: str, tenant_id: str, endpoint: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.delete(
            f"{XERO_API_BASE}/{endpoint}",
            headers={
                "Authorization": f"Bearer {token}",
                "Xero-Tenant-Id": tenant_id,
                "Accept": "application/json",
            },
        )


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
    # ORGANISATION
    # =========================================================================

    @mcp.tool(
        name="xero_get_organisation",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_organisation() -> str:
        """Get Xero organisation details including name, tax number, addresses, and settings."""
        if not xero_config.is_configured:
            return "Error: Xero not configured. Run xero_auth_start to connect."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "Organisation")
            error = _check_xero_response(response)
            if error:
                return error
            orgs = response.json().get("Organisations", [])
            if not orgs:
                return "No organisation data found."
            org = orgs[0]
            addresses = org.get("Addresses", [])
            addr_lines = []
            for a in addresses:
                addr_type = a.get("AddressType", "")
                parts = [a.get("AddressLine1", ""), a.get("City", ""), a.get("Region", ""), a.get("PostalCode", ""), a.get("Country", "")]
                addr_str = ", ".join(p for p in parts if p)
                if addr_str:
                    addr_lines.append(f"  {addr_type}: {addr_str}")
            return f"""## Organisation Details

**Name:** {org.get('Name', 'N/A')}
**Legal Name:** {org.get('LegalName', 'N/A')}
**Short Code:** {org.get('ShortCode', 'N/A')}
**Tax Number:** {org.get('TaxNumber', 'N/A')}
**Registration Number:** {org.get('RegistrationNumber', 'N/A')}
**Base Currency:** {org.get('BaseCurrency', 'N/A')}
**Country Code:** {org.get('CountryCode', 'N/A')}
**Timezone:** {org.get('Timezone', 'N/A')}
**Organisation Type:** {org.get('OrganisationType', 'N/A')}
**Financial Year End:** Day {org.get('FinancialYearEndDay', 'N/A')}, Month {org.get('FinancialYearEndMonth', 'N/A')}
**Sales Tax Basis:** {org.get('SalesTaxBasis', 'N/A')}
**Sales Tax Period:** {org.get('SalesTaxPeriod', 'N/A')}
**Edition:** {org.get('Edition', 'N/A')}
**Class:** {org.get('Class', 'N/A')}

**Addresses:**
{chr(10).join(addr_lines) if addr_lines else '  None'}"""
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
        invoice_type: Optional[str] = Field(None, description="Filter: 'ACCREC' (sales) or 'ACCPAY' (bills)"),
        days: int = Field(90, description="Invoices from last N days"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """Get Xero invoices with filters. Use invoice_type='ACCPAY' for bills."""
        if not xero_config.is_configured:
            return "Error: Xero not configured. Run xero_auth_start to connect."

        try:
            token = await xero_config.get_access_token()

            where_parts = []
            if status:
                where_parts.append(f'Status=="{status.upper()}"')
            if invoice_type:
                where_parts.append(f'Type=="{invoice_type.upper()}"')

            since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            where_parts.append(f'Date>=DateTime({since_date.replace("-", ",")})')

            params = {"order": "Date DESC"}
            if where_parts:
                params["where"] = " AND ".join(where_parts)

            response = await _xero_get(token, xero_config.tenant_id, "Invoices", params)
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
                inv_type = inv.get("Type", "N/A")
                total = inv.get("Total", 0)
                due = inv.get("AmountDue", 0)
                date_str = inv.get("DateString", "")[:10]

                results.append(f"**{inv_num}** - {contact}\n  Type: {inv_type} | Status: {status_val} | Total: ${total:,.2f} | Due: ${due:,.2f} | Date: {date_str}")

            return f"Found {len(results)} invoice(s):\n\n" + "\n\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_invoice",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_invoice(
        invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number"),
    ) -> str:
        """Get full invoice details including line items."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."

        try:
            token = await xero_config.get_access_token()
            invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)

            response = await _xero_get(token, xero_config.tenant_id, f"Invoices/{invoice_guid}")
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

            payments = inv.get("Payments", [])
            payment_lines = []
            for p in payments:
                payment_lines.append(f"- ${p.get('Amount', 0):,.2f} on {p.get('Date', '')[:10]}")

            return f"""# Invoice {inv.get('InvoiceNumber', 'N/A')}

**Contact:** {inv.get('Contact', {}).get('Name', 'Unknown')}
**Type:** {inv.get('Type', 'N/A')}
**Status:** {inv.get('Status', 'N/A')}
**Date:** {inv.get('DateString', '')[:10]}
**Due Date:** {inv.get('DueDateString', '')[:10]}
**Reference:** {inv.get('Reference', 'N/A')}
**Currency:** {inv.get('CurrencyCode', 'N/A')}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Subtotal:** ${inv.get('SubTotal', 0):,.2f}
**Tax:** ${inv.get('TotalTax', 0):,.2f}
**Total:** ${inv.get('Total', 0):,.2f}
**Amount Due:** ${inv.get('AmountDue', 0):,.2f}
**Amount Paid:** ${inv.get('AmountPaid', 0):,.2f}

## Payments
{chr(10).join(payment_lines) if payment_lines else 'No payments recorded'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_invoice",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_invoice(
        contact_name: str = Field(..., description="Contact/customer name (must exist in Xero)"),
        line_items: str = Field(..., description='JSON array of line items: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]'),
        invoice_type: str = Field("ACCREC", description="Type: 'ACCREC' (sales invoice) or 'ACCPAY' (bill)"),
        reference: Optional[str] = Field(None, description="Invoice reference"),
        due_days: int = Field(30, description="Days until due"),
        status: str = Field("DRAFT", description="Status: 'DRAFT' or 'AUTHORISED'"),
        currency_code: Optional[str] = Field(None, description="Currency code (e.g., 'AUD', 'USD')"),
        branding_theme_id: Optional[str] = Field(None, description="Branding theme ID (GUID)"),
    ) -> str:
        """Create a new Xero invoice (sales) or bill (purchase)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."

        try:
            token = await xero_config.get_access_token()
            items = json.loads(line_items)

            response = await _xero_get(
                token, xero_config.tenant_id, "Contacts",
                {"where": f'Name.Contains("{contact_name}")'},
            )
            error = _check_xero_response(response)
            if error:
                return error
            contacts = response.json().get("Contacts", [])

            if not contacts:
                return f"Error: Contact '{contact_name}' not found in Xero."

            contact_id = contacts[0]["ContactID"]

            invoice_data = {
                "Type": invoice_type.upper(),
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
            if currency_code:
                invoice_data["CurrencyCode"] = currency_code
            if branding_theme_id:
                invoice_data["BrandingThemeID"] = branding_theme_id

            response = await _xero_post(token, xero_config.tenant_id, "Invoices", {"Invoices": [invoice_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("Invoices", [{}])[0]

            return f"Invoice created: **{created.get('InvoiceNumber', 'N/A')}** ({invoice_type.upper()}) for ${created.get('Total', 0):,.2f}"
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

            response = await _xero_post(token, xero_config.tenant_id, "Invoices", {"Invoices": [update_data]})
            error = _check_xero_response(response)
            if error:
                return error
            updated = response.json().get("Invoices", [{}])[0]

            return f"Invoice **{updated.get('InvoiceNumber', invoice_id)}** updated."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_void_invoice",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def xero_void_invoice(
        invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number"),
    ) -> str:
        """Void a Xero invoice. Invoice must be AUTHORISED with no payments."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)
            response = await _xero_post(
                token, xero_config.tenant_id, "Invoices",
                {"Invoices": [{"InvoiceID": invoice_guid, "Status": "VOIDED"}]},
            )
            error = _check_xero_response(response)
            if error:
                return error
            return f"Invoice **{invoice_id}** voided."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_email_invoice",
        annotations={"readOnlyHint": False, "openWorldHint": True},
    )
    async def xero_email_invoice(
        invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number"),
    ) -> str:
        """Email an AUTHORISED invoice to the contact. Invoice must be AUTHORISED."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{XERO_API_BASE}/Invoices/{invoice_guid}/Email",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Xero-Tenant-Id": xero_config.tenant_id,
                        "Accept": "application/json",
                    },
                )
            error = _check_xero_response(response)
            if error:
                return error
            return f"Invoice **{invoice_id}** emailed to contact."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_invoice_history",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_invoice_history(
        invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number"),
    ) -> str:
        """Get the history/notes for an invoice."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)
            response = await _xero_get(token, xero_config.tenant_id, f"Invoices/{invoice_guid}/History")
            error = _check_xero_response(response)
            if error:
                return error
            history = response.json().get("HistoryRecords", [])
            if not history:
                return f"No history found for invoice {invoice_id}."
            results = []
            for h in history:
                results.append(f"- **{h.get('DateUTCString', '')}** - {h.get('User', 'System')}: {h.get('Details', 'N/A')}")
            return f"## Invoice {invoice_id} History\n\n" + "\n".join(results)
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

            response = await _xero_get(token, xero_config.tenant_id, "Contacts", params)
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

    @mcp.tool(
        name="xero_get_contact",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_contact(
        contact_id: str = Field(..., description="Contact ID (GUID)"),
    ) -> str:
        """Get full details for a single Xero contact."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"Contacts/{contact_id}")
            error = _check_xero_response(response)
            if error:
                return error
            c = response.json().get("Contacts", [{}])[0]

            phones = []
            for p in c.get("Phones", []):
                if p.get("PhoneNumber"):
                    phones.append(f"  {p.get('PhoneType', '')}: {p.get('PhoneCountryCode', '')} {p.get('PhoneAreaCode', '')} {p.get('PhoneNumber', '')}")

            addresses = []
            for a in c.get("Addresses", []):
                parts = [a.get("AddressLine1", ""), a.get("City", ""), a.get("Region", ""), a.get("PostalCode", ""), a.get("Country", "")]
                addr_str = ", ".join(p for p in parts if p)
                if addr_str:
                    addresses.append(f"  {a.get('AddressType', '')}: {addr_str}")

            return f"""## Contact: {c.get('Name', 'N/A')}

**Contact ID:** `{c.get('ContactID', 'N/A')}`
**Status:** {c.get('ContactStatus', 'N/A')}
**First Name:** {c.get('FirstName', 'N/A')}
**Last Name:** {c.get('LastName', 'N/A')}
**Email:** {c.get('EmailAddress', 'N/A')}
**Tax Number:** {c.get('TaxNumber', 'N/A')}
**Account Number:** {c.get('AccountNumber', 'N/A')}
**Is Customer:** {c.get('IsCustomer', False)}
**Is Supplier:** {c.get('IsSupplier', False)}
**Default Currency:** {c.get('DefaultCurrency', 'N/A')}

**Phones:**
{chr(10).join(phones) if phones else '  None'}

**Addresses:**
{chr(10).join(addresses) if addresses else '  None'}

**Outstanding AR:** ${c.get('Balances', {}).get('AccountsReceivable', {}).get('Outstanding', 0):,.2f}
**Overdue AR:** ${c.get('Balances', {}).get('AccountsReceivable', {}).get('Overdue', 0):,.2f}
**Outstanding AP:** ${c.get('Balances', {}).get('AccountsPayable', {}).get('Outstanding', 0):,.2f}
**Overdue AP:** ${c.get('Balances', {}).get('AccountsPayable', {}).get('Overdue', 0):,.2f}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_contact",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_contact(
        name: str = Field(..., description="Contact/company name"),
        first_name: Optional[str] = Field(None, description="First name"),
        last_name: Optional[str] = Field(None, description="Last name"),
        email: Optional[str] = Field(None, description="Email address"),
        phone: Optional[str] = Field(None, description="Phone number"),
        account_number: Optional[str] = Field(None, description="Account number"),
        tax_number: Optional[str] = Field(None, description="Tax/ABN number"),
        is_customer: bool = Field(True, description="Set as customer"),
        is_supplier: bool = Field(False, description="Set as supplier"),
    ) -> str:
        """Create a new Xero contact."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            contact_data = {"Name": name}
            if first_name:
                contact_data["FirstName"] = first_name
            if last_name:
                contact_data["LastName"] = last_name
            if email:
                contact_data["EmailAddress"] = email
            if phone:
                contact_data["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
            if account_number:
                contact_data["AccountNumber"] = account_number
            if tax_number:
                contact_data["TaxNumber"] = tax_number

            response = await _xero_post(token, xero_config.tenant_id, "Contacts", {"Contacts": [contact_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("Contacts", [{}])[0]
            return f"Contact created: **{created.get('Name', 'N/A')}** (ID: `{created.get('ContactID', 'N/A')}`)"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_update_contact",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_update_contact(
        contact_id: str = Field(..., description="Contact ID (GUID)"),
        name: Optional[str] = Field(None, description="Update name"),
        first_name: Optional[str] = Field(None, description="Update first name"),
        last_name: Optional[str] = Field(None, description="Update last name"),
        email: Optional[str] = Field(None, description="Update email"),
        phone: Optional[str] = Field(None, description="Update phone number"),
        account_number: Optional[str] = Field(None, description="Update account number"),
        tax_number: Optional[str] = Field(None, description="Update tax/ABN number"),
    ) -> str:
        """Update an existing Xero contact."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            update_data = {"ContactID": contact_id}
            if name:
                update_data["Name"] = name
            if first_name:
                update_data["FirstName"] = first_name
            if last_name:
                update_data["LastName"] = last_name
            if email:
                update_data["EmailAddress"] = email
            if phone:
                update_data["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
            if account_number:
                update_data["AccountNumber"] = account_number
            if tax_number:
                update_data["TaxNumber"] = tax_number

            if len(update_data) == 1:
                return "Error: No updates specified."

            response = await _xero_post(token, xero_config.tenant_id, "Contacts", {"Contacts": [update_data]})
            error = _check_xero_response(response)
            if error:
                return error
            updated = response.json().get("Contacts", [{}])[0]
            return f"Contact **{updated.get('Name', contact_id)}** updated."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_archive_contact",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def xero_archive_contact(
        contact_id: str = Field(..., description="Contact ID (GUID)"),
    ) -> str:
        """Archive a Xero contact."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_post(
                token, xero_config.tenant_id, "Contacts",
                {"Contacts": [{"ContactID": contact_id, "ContactStatus": "ARCHIVED"}]},
            )
            error = _check_xero_response(response)
            if error:
                return error
            return f"Contact `{contact_id}` archived."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_contact_activity",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_contact_activity(
        contact_id: str = Field(..., description="Contact ID (GUID)"),
    ) -> str:
        """Get recent invoices and bills for a contact (CIS activity)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(
                token, xero_config.tenant_id, "Invoices",
                {"where": f'Contact.ContactID==guid("{contact_id}")', "order": "Date DESC"},
            )
            error = _check_xero_response(response)
            if error:
                return error
            invoices = response.json().get("Invoices", [])[:20]
            if not invoices:
                return "No invoices found for this contact."
            results = []
            for inv in invoices:
                results.append(f"- **{inv.get('InvoiceNumber', 'N/A')}** ({inv.get('Type', '')}) | {inv.get('Status', '')} | ${inv.get('Total', 0):,.2f} | Due: ${inv.get('AmountDue', 0):,.2f} | {inv.get('DateString', '')[:10]}")
            return f"## Contact Activity ({len(results)} invoices)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # PAYMENTS
    # =========================================================================

    @mcp.tool(
        name="xero_get_payments",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_payments(
        status: Optional[str] = Field(None, description="Filter: 'AUTHORISED' or 'DELETED'"),
        days: int = Field(90, description="Payments from last N days"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List Xero payments with filters."""
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

            response = await _xero_get(token, xero_config.tenant_id, "Payments", params)
            error = _check_xero_response(response)
            if error:
                return error
            payments = response.json().get("Payments", [])[:limit]
            if not payments:
                return "No payments found."
            results = []
            for p in payments:
                inv = p.get("Invoice", {})
                results.append(f"- **${p.get('Amount', 0):,.2f}** | Invoice: {inv.get('InvoiceNumber', 'N/A')} | Account: {p.get('Account', {}).get('Code', 'N/A')} | Date: {p.get('DateString', '')[:10]} | Status: {p.get('Status', '')}")
            return f"## Payments ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_payment",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_payment(
        payment_id: str = Field(..., description="Payment ID (GUID)"),
    ) -> str:
        """Get full payment details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"Payments/{payment_id}")
            error = _check_xero_response(response)
            if error:
                return error
            p = response.json().get("Payments", [{}])[0]
            inv = p.get("Invoice", {})
            return f"""## Payment Details

**Payment ID:** `{p.get('PaymentID', 'N/A')}`
**Amount:** ${p.get('Amount', 0):,.2f}
**Date:** {p.get('DateString', '')[:10]}
**Status:** {p.get('Status', 'N/A')}
**Reference:** {p.get('Reference', 'N/A')}
**Payment Type:** {p.get('PaymentType', 'N/A')}
**Invoice:** {inv.get('InvoiceNumber', 'N/A')} (ID: `{inv.get('InvoiceID', 'N/A')}`)
**Account:** {p.get('Account', {}).get('Name', 'N/A')} ({p.get('Account', {}).get('Code', 'N/A')})
**Currency:** {p.get('CurrencyCode', 'N/A')}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_payment",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_payment(
        invoice_id: str = Field(..., description="Invoice ID (GUID) or Invoice Number"),
        account_code: str = Field(..., description="Bank account code (e.g., '090')"),
        amount: float = Field(..., description="Payment amount"),
        date: Optional[str] = Field(None, description="Payment date (YYYY-MM-DD, defaults to today)"),
        reference: Optional[str] = Field(None, description="Payment reference"),
    ) -> str:
        """Create a payment against an invoice."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            invoice_guid = await _resolve_invoice_id(invoice_id, token, xero_config.tenant_id)

            payment_data = {
                "Invoice": {"InvoiceID": invoice_guid},
                "Account": {"Code": account_code},
                "Amount": amount,
                "Date": date or datetime.now().strftime("%Y-%m-%d"),
            }
            if reference:
                payment_data["Reference"] = reference

            response = await _xero_put(token, xero_config.tenant_id, "Payments", {"Payments": [payment_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("Payments", [{}])[0]
            return f"Payment of **${amount:,.2f}** created against invoice {invoice_id}. Payment ID: `{created.get('PaymentID', 'N/A')}`"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_delete_payment",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def xero_delete_payment(
        payment_id: str = Field(..., description="Payment ID (GUID)"),
    ) -> str:
        """Delete (reverse) a payment. Sets status to DELETED."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_post(
                token, xero_config.tenant_id, f"Payments/{payment_id}",
                {"Status": "DELETED"},
            )
            error = _check_xero_response(response)
            if error:
                return error
            return f"Payment `{payment_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # CREDIT NOTES
    # =========================================================================

    @mcp.tool(
        name="xero_get_credit_notes",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_credit_notes(
        status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'PAID', 'VOIDED'"),
        contact_name: Optional[str] = Field(None, description="Filter by contact name (partial match)"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List Xero credit notes."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {"order": "Date DESC"}
            where_parts = []
            if status:
                where_parts.append(f'Status=="{status.upper()}"')
            if where_parts:
                params["where"] = " AND ".join(where_parts)

            response = await _xero_get(token, xero_config.tenant_id, "CreditNotes", params)
            error = _check_xero_response(response)
            if error:
                return error
            notes = response.json().get("CreditNotes", [])
            if contact_name:
                notes = [n for n in notes if contact_name.lower() in n.get("Contact", {}).get("Name", "").lower()]
            notes = notes[:limit]
            if not notes:
                return "No credit notes found."
            results = []
            for cn in notes:
                results.append(f"- **{cn.get('CreditNoteNumber', 'N/A')}** | {cn.get('Contact', {}).get('Name', 'Unknown')} | {cn.get('Status', '')} | Total: ${cn.get('Total', 0):,.2f} | Remaining: ${cn.get('RemainingCredit', 0):,.2f} | {cn.get('DateString', '')[:10]}")
            return f"## Credit Notes ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_credit_note",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_credit_note(
        credit_note_id: str = Field(..., description="Credit Note ID (GUID)"),
    ) -> str:
        """Get full credit note details including line items."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"CreditNotes/{credit_note_id}")
            error = _check_xero_response(response)
            if error:
                return error
            cn = response.json().get("CreditNotes", [{}])[0]
            lines = []
            for item in cn.get("LineItems", []):
                lines.append(f"- {item.get('Description', 'N/A')} (Qty: {item.get('Quantity', 0)}) - ${item.get('LineAmount', 0):,.2f}")
            return f"""## Credit Note {cn.get('CreditNoteNumber', 'N/A')}

**Contact:** {cn.get('Contact', {}).get('Name', 'Unknown')}
**Type:** {cn.get('Type', 'N/A')}
**Status:** {cn.get('Status', 'N/A')}
**Date:** {cn.get('DateString', '')[:10]}
**Currency:** {cn.get('CurrencyCode', 'N/A')}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Subtotal:** ${cn.get('SubTotal', 0):,.2f}
**Tax:** ${cn.get('TotalTax', 0):,.2f}
**Total:** ${cn.get('Total', 0):,.2f}
**Remaining Credit:** ${cn.get('RemainingCredit', 0):,.2f}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_credit_note",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_credit_note(
        contact_name: str = Field(..., description="Contact name (must exist in Xero)"),
        line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]'),
        credit_note_type: str = Field("ACCRECCREDIT", description="Type: 'ACCRECCREDIT' (customer) or 'ACCPAYCREDIT' (supplier)"),
        reference: Optional[str] = Field(None, description="Reference"),
        status: str = Field("DRAFT", description="Status: 'DRAFT' or 'AUTHORISED'"),
    ) -> str:
        """Create a credit note."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            items = json.loads(line_items)

            response = await _xero_get(
                token, xero_config.tenant_id, "Contacts",
                {"where": f'Name.Contains("{contact_name}")'},
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
                        "AccountCode": item.get("account_code", "200"),
                    }
                    for item in items
                ],
                "Status": status.upper(),
            }
            if reference:
                cn_data["Reference"] = reference

            response = await _xero_put(token, xero_config.tenant_id, "CreditNotes", {"CreditNotes": [cn_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("CreditNotes", [{}])[0]
            return f"Credit note created: **{created.get('CreditNoteNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in line_items."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # BANK TRANSACTIONS
    # =========================================================================

    @mcp.tool(
        name="xero_get_bank_transactions",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_bank_transactions(
        bank_account_code: Optional[str] = Field(None, description="Bank account code filter"),
        status: Optional[str] = Field(None, description="Filter: 'AUTHORISED' or 'DELETED'"),
        transaction_type: Optional[str] = Field(None, description="Filter: 'SPEND', 'RECEIVE', 'SPEND-TRANSFER', 'RECEIVE-TRANSFER'"),
        days: int = Field(30, description="Transactions from last N days"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List bank transactions (spend/receive money)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            where_parts = []
            if status:
                where_parts.append(f'Status=="{status.upper()}"')
            if transaction_type:
                where_parts.append(f'Type=="{transaction_type.upper()}"')
            since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            where_parts.append(f'Date>=DateTime({since_date.replace("-", ",")})')
            params = {"order": "Date DESC"}
            if where_parts:
                params["where"] = " AND ".join(where_parts)

            response = await _xero_get(token, xero_config.tenant_id, "BankTransactions", params)
            error = _check_xero_response(response)
            if error:
                return error
            txns = response.json().get("BankTransactions", [])
            if bank_account_code:
                txns = [t for t in txns if t.get("BankAccount", {}).get("Code", "") == bank_account_code]
            txns = txns[:limit]
            if not txns:
                return "No bank transactions found."
            results = []
            for t in txns:
                contact = t.get("Contact", {}).get("Name", "N/A")
                results.append(f"- **{t.get('Type', '')}** | {contact} | ${t.get('Total', 0):,.2f} | Bank: {t.get('BankAccount', {}).get('Name', 'N/A')} | {t.get('DateString', '')[:10]} | {t.get('Status', '')}")
            return f"## Bank Transactions ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_bank_transaction",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_bank_transaction(
        transaction_id: str = Field(..., description="Bank Transaction ID (GUID)"),
    ) -> str:
        """Get full bank transaction details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"BankTransactions/{transaction_id}")
            error = _check_xero_response(response)
            if error:
                return error
            t = response.json().get("BankTransactions", [{}])[0]
            lines = []
            for item in t.get("LineItems", []):
                lines.append(f"- {item.get('Description', 'N/A')} | ${item.get('LineAmount', 0):,.2f} | Account: {item.get('AccountCode', 'N/A')}")
            return f"""## Bank Transaction

**ID:** `{t.get('BankTransactionID', 'N/A')}`
**Type:** {t.get('Type', 'N/A')}
**Contact:** {t.get('Contact', {}).get('Name', 'N/A')}
**Bank Account:** {t.get('BankAccount', {}).get('Name', 'N/A')} ({t.get('BankAccount', {}).get('Code', 'N/A')})
**Date:** {t.get('DateString', '')[:10]}
**Status:** {t.get('Status', 'N/A')}
**Reference:** {t.get('Reference', 'N/A')}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Subtotal:** ${t.get('SubTotal', 0):,.2f}
**Tax:** ${t.get('TotalTax', 0):,.2f}
**Total:** ${t.get('Total', 0):,.2f}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_bank_transaction",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_bank_transaction(
        transaction_type: str = Field(..., description="Type: 'SPEND' or 'RECEIVE'"),
        contact_name: str = Field(..., description="Contact name (must exist in Xero)"),
        bank_account_code: str = Field(..., description="Bank account code (e.g., '090')"),
        line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "400"}]'),
        reference: Optional[str] = Field(None, description="Reference"),
        date: Optional[str] = Field(None, description="Date (YYYY-MM-DD, defaults to today)"),
    ) -> str:
        """Create a bank transaction (spend or receive money)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            items = json.loads(line_items)

            response = await _xero_get(
                token, xero_config.tenant_id, "Contacts",
                {"where": f'Name.Contains("{contact_name}")'},
            )
            error = _check_xero_response(response)
            if error:
                return error
            contacts = response.json().get("Contacts", [])
            if not contacts:
                return f"Error: Contact '{contact_name}' not found."

            txn_data = {
                "Type": transaction_type.upper(),
                "Contact": {"ContactID": contacts[0]["ContactID"]},
                "BankAccount": {"Code": bank_account_code},
                "Date": date or datetime.now().strftime("%Y-%m-%d"),
                "LineItems": [
                    {
                        "Description": item.get("description", ""),
                        "Quantity": item.get("quantity", 1),
                        "UnitAmount": item.get("unit_amount", 0),
                        "AccountCode": item.get("account_code", "400"),
                    }
                    for item in items
                ],
            }
            if reference:
                txn_data["Reference"] = reference

            response = await _xero_put(token, xero_config.tenant_id, "BankTransactions", {"BankTransactions": [txn_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("BankTransactions", [{}])[0]
            return f"Bank transaction created: **{transaction_type.upper()}** of ${created.get('Total', 0):,.2f} (ID: `{created.get('BankTransactionID', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in line_items."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # BANK TRANSFERS
    # =========================================================================

    @mcp.tool(
        name="xero_get_bank_transfers",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_bank_transfers(
        days: int = Field(30, description="Transfers from last N days"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List bank transfers between accounts."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "BankTransfers", {"order": "Date DESC"})
            error = _check_xero_response(response)
            if error:
                return error
            transfers = response.json().get("BankTransfers", [])[:limit]
            if not transfers:
                return "No bank transfers found."
            results = []
            for t in transfers:
                results.append(f"- ${t.get('Amount', 0):,.2f} | From: {t.get('FromBankAccount', {}).get('Name', 'N/A')} -> To: {t.get('ToBankAccount', {}).get('Name', 'N/A')} | {t.get('DateString', '')[:10]}")
            return f"## Bank Transfers ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_bank_transfer",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_bank_transfer(
        from_account_code: str = Field(..., description="Source bank account code"),
        to_account_code: str = Field(..., description="Destination bank account code"),
        amount: float = Field(..., description="Transfer amount"),
        date: Optional[str] = Field(None, description="Transfer date (YYYY-MM-DD, defaults to today)"),
    ) -> str:
        """Create a bank transfer between two bank accounts."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            transfer_data = {
                "FromBankAccount": {"Code": from_account_code},
                "ToBankAccount": {"Code": to_account_code},
                "Amount": amount,
                "Date": date or datetime.now().strftime("%Y-%m-%d"),
            }
            response = await _xero_put(token, xero_config.tenant_id, "BankTransfers", {"BankTransfers": [transfer_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("BankTransfers", [{}])[0]
            return f"Bank transfer of **${amount:,.2f}** created from {from_account_code} to {to_account_code}. ID: `{created.get('BankTransferID', 'N/A')}`"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # PURCHASE ORDERS
    # =========================================================================

    @mcp.tool(
        name="xero_get_purchase_orders",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_purchase_orders(
        status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'BILLED', 'DELETED'"),
        days: int = Field(90, description="POs from last N days"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List purchase orders."""
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

            response = await _xero_get(token, xero_config.tenant_id, "PurchaseOrders", params)
            error = _check_xero_response(response)
            if error:
                return error
            pos = response.json().get("PurchaseOrders", [])[:limit]
            if not pos:
                return "No purchase orders found."
            results = []
            for po in pos:
                results.append(f"- **{po.get('PurchaseOrderNumber', 'N/A')}** | {po.get('Contact', {}).get('Name', 'Unknown')} | {po.get('Status', '')} | Total: ${po.get('Total', 0):,.2f} | {po.get('DateString', '')[:10]}")
            return f"## Purchase Orders ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_purchase_order",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_purchase_order(
        purchase_order_id: str = Field(..., description="Purchase Order ID (GUID)"),
    ) -> str:
        """Get full purchase order details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"PurchaseOrders/{purchase_order_id}")
            error = _check_xero_response(response)
            if error:
                return error
            po = response.json().get("PurchaseOrders", [{}])[0]
            lines = []
            for item in po.get("LineItems", []):
                lines.append(f"- {item.get('Description', 'N/A')} (Qty: {item.get('Quantity', 0)}) - ${item.get('LineAmount', 0):,.2f}")
            return f"""## Purchase Order {po.get('PurchaseOrderNumber', 'N/A')}

**Contact:** {po.get('Contact', {}).get('Name', 'Unknown')}
**Status:** {po.get('Status', 'N/A')}
**Date:** {po.get('DateString', '')[:10]}
**Delivery Date:** {po.get('DeliveryDateString', 'N/A')[:10] if po.get('DeliveryDateString') else 'N/A'}
**Reference:** {po.get('Reference', 'N/A')}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Subtotal:** ${po.get('SubTotal', 0):,.2f}
**Tax:** ${po.get('TotalTax', 0):,.2f}
**Total:** ${po.get('Total', 0):,.2f}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_purchase_order",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_purchase_order(
        contact_name: str = Field(..., description="Supplier name (must exist in Xero)"),
        line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "300"}]'),
        reference: Optional[str] = Field(None, description="Reference"),
        delivery_date: Optional[str] = Field(None, description="Delivery date (YYYY-MM-DD)"),
        status: str = Field("DRAFT", description="Status: 'DRAFT' or 'SUBMITTED'"),
    ) -> str:
        """Create a purchase order."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            items = json.loads(line_items)

            response = await _xero_get(
                token, xero_config.tenant_id, "Contacts",
                {"where": f'Name.Contains("{contact_name}")'},
            )
            error = _check_xero_response(response)
            if error:
                return error
            contacts = response.json().get("Contacts", [])
            if not contacts:
                return f"Error: Contact '{contact_name}' not found."

            po_data = {
                "Contact": {"ContactID": contacts[0]["ContactID"]},
                "Date": datetime.now().strftime("%Y-%m-%d"),
                "LineItems": [
                    {
                        "Description": item.get("description", ""),
                        "Quantity": item.get("quantity", 1),
                        "UnitAmount": item.get("unit_amount", 0),
                        "AccountCode": item.get("account_code", "300"),
                    }
                    for item in items
                ],
                "Status": status.upper(),
            }
            if reference:
                po_data["Reference"] = reference
            if delivery_date:
                po_data["DeliveryDate"] = delivery_date

            response = await _xero_put(token, xero_config.tenant_id, "PurchaseOrders", {"PurchaseOrders": [po_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("PurchaseOrders", [{}])[0]
            return f"Purchase order created: **{created.get('PurchaseOrderNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in line_items."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_update_purchase_order",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_update_purchase_order(
        purchase_order_id: str = Field(..., description="Purchase Order ID (GUID)"),
        status: Optional[str] = Field(None, description="Update status: 'DRAFT', 'SUBMITTED', 'AUTHORISED', 'DELETED'"),
        reference: Optional[str] = Field(None, description="Update reference"),
        delivery_date: Optional[str] = Field(None, description="Update delivery date (YYYY-MM-DD)"),
    ) -> str:
        """Update a purchase order."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            update_data = {"PurchaseOrderID": purchase_order_id}
            if status:
                update_data["Status"] = status.upper()
            if reference:
                update_data["Reference"] = reference
            if delivery_date:
                update_data["DeliveryDate"] = delivery_date
            if len(update_data) == 1:
                return "Error: No updates specified."

            response = await _xero_post(token, xero_config.tenant_id, "PurchaseOrders", {"PurchaseOrders": [update_data]})
            error = _check_xero_response(response)
            if error:
                return error
            return f"Purchase order `{purchase_order_id}` updated."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # QUOTES
    # =========================================================================

    @mcp.tool(
        name="xero_get_quotes",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_quotes(
        status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'SENT', 'ACCEPTED', 'DECLINED', 'INVOICED'"),
        contact_name: Optional[str] = Field(None, description="Filter by contact name"),
        days: int = Field(90, description="Quotes from last N days"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List quotes/estimates."""
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

            response = await _xero_get(token, xero_config.tenant_id, "Quotes", params)
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
                results.append(f"- **{q.get('QuoteNumber', 'N/A')}** | {q.get('Contact', {}).get('Name', 'Unknown')} | {q.get('Status', '')} | Total: ${q.get('Total', 0):,.2f} | {q.get('DateString', '')[:10]}")
            return f"## Quotes ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_quote",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_quote(
        quote_id: str = Field(..., description="Quote ID (GUID)"),
    ) -> str:
        """Get full quote details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"Quotes/{quote_id}")
            error = _check_xero_response(response)
            if error:
                return error
            q = response.json().get("Quotes", [{}])[0]
            lines = []
            for item in q.get("LineItems", []):
                lines.append(f"- {item.get('Description', 'N/A')} (Qty: {item.get('Quantity', 0)}) - ${item.get('LineAmount', 0):,.2f}")
            return f"""## Quote {q.get('QuoteNumber', 'N/A')}

**Contact:** {q.get('Contact', {}).get('Name', 'Unknown')}
**Status:** {q.get('Status', 'N/A')}
**Date:** {q.get('DateString', '')[:10]}
**Expiry Date:** {q.get('ExpiryDateString', 'N/A')[:10] if q.get('ExpiryDateString') else 'N/A'}
**Title:** {q.get('Title', 'N/A')}
**Summary:** {q.get('Summary', 'N/A')}
**Reference:** {q.get('Reference', 'N/A')}

## Line Items
{chr(10).join(lines) if lines else 'No line items'}

**Subtotal:** ${q.get('SubTotal', 0):,.2f}
**Tax:** ${q.get('TotalTax', 0):,.2f}
**Total:** ${q.get('Total', 0):,.2f}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_quote",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_quote(
        contact_name: str = Field(..., description="Contact name (must exist in Xero)"),
        line_items: str = Field(..., description='JSON array: [{"description": "...", "quantity": 1, "unit_amount": 100.00, "account_code": "200"}]'),
        title: Optional[str] = Field(None, description="Quote title"),
        summary: Optional[str] = Field(None, description="Quote summary"),
        reference: Optional[str] = Field(None, description="Reference"),
        expiry_days: int = Field(30, description="Days until quote expires"),
        status: str = Field("DRAFT", description="Status: 'DRAFT' or 'SENT'"),
    ) -> str:
        """Create a quote/estimate."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            items = json.loads(line_items)

            response = await _xero_get(
                token, xero_config.tenant_id, "Contacts",
                {"where": f'Name.Contains("{contact_name}")'},
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
                        "AccountCode": item.get("account_code", "200"),
                    }
                    for item in items
                ],
                "Status": status.upper(),
            }
            if title:
                quote_data["Title"] = title
            if summary:
                quote_data["Summary"] = summary
            if reference:
                quote_data["Reference"] = reference

            response = await _xero_put(token, xero_config.tenant_id, "Quotes", {"Quotes": [quote_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("Quotes", [{}])[0]
            return f"Quote created: **{created.get('QuoteNumber', 'N/A')}** for ${created.get('Total', 0):,.2f}"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in line_items."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_update_quote",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_update_quote(
        quote_id: str = Field(..., description="Quote ID (GUID)"),
        status: Optional[str] = Field(None, description="Update status: 'DRAFT', 'SENT', 'ACCEPTED', 'DECLINED'"),
        title: Optional[str] = Field(None, description="Update title"),
        summary: Optional[str] = Field(None, description="Update summary"),
        expiry_date: Optional[str] = Field(None, description="Update expiry date (YYYY-MM-DD)"),
    ) -> str:
        """Update a quote."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            update_data = {"QuoteID": quote_id}
            if status:
                update_data["Status"] = status.upper()
            if title:
                update_data["Title"] = title
            if summary:
                update_data["Summary"] = summary
            if expiry_date:
                update_data["ExpiryDate"] = expiry_date
            if len(update_data) == 1:
                return "Error: No updates specified."

            response = await _xero_post(token, xero_config.tenant_id, "Quotes", {"Quotes": [update_data]})
            error = _check_xero_response(response)
            if error:
                return error
            return f"Quote `{quote_id}` updated."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ACCOUNTS (Chart of Accounts)
    # =========================================================================

    @mcp.tool(
        name="xero_get_accounts",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_accounts(
        account_type: Optional[str] = Field(None, description="Filter: 'BANK', 'REVENUE', 'EXPENSE', 'CURRENT', 'FIXED', 'EQUITY', etc."),
        account_class: Optional[str] = Field(None, description="Filter: 'ASSET', 'EQUITY', 'EXPENSE', 'LIABILITY', 'REVENUE'"),
    ) -> str:
        """List chart of accounts. Use to find account codes for transactions."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            where_parts = []
            if account_type:
                where_parts.append(f'Type=="{account_type.upper()}"')
            if account_class:
                where_parts.append(f'Class=="{account_class.upper()}"')
            params = {"order": "Code"}
            if where_parts:
                params["where"] = " AND ".join(where_parts)

            response = await _xero_get(token, xero_config.tenant_id, "Accounts", params)
            error = _check_xero_response(response)
            if error:
                return error
            accounts = response.json().get("Accounts", [])
            if not accounts:
                return "No accounts found."
            results = []
            for a in accounts:
                status_val = a.get("Status", "")
                if status_val == "ARCHIVED":
                    continue
                results.append(f"- **{a.get('Code', 'N/A')}** - {a.get('Name', 'N/A')} | Type: {a.get('Type', '')} | Class: {a.get('Class', '')} | Tax: {a.get('TaxType', 'N/A')}")
            return f"## Chart of Accounts ({len(results)} active)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_account",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_account(
        account_id: str = Field(..., description="Account ID (GUID)"),
    ) -> str:
        """Get full account details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"Accounts/{account_id}")
            error = _check_xero_response(response)
            if error:
                return error
            a = response.json().get("Accounts", [{}])[0]
            return f"""## Account: {a.get('Name', 'N/A')}

**Account ID:** `{a.get('AccountID', 'N/A')}`
**Code:** {a.get('Code', 'N/A')}
**Type:** {a.get('Type', 'N/A')}
**Class:** {a.get('Class', 'N/A')}
**Status:** {a.get('Status', 'N/A')}
**Tax Type:** {a.get('TaxType', 'N/A')}
**Description:** {a.get('Description', 'N/A')}
**Bank Account Number:** {a.get('BankAccountNumber', 'N/A')}
**Currency Code:** {a.get('CurrencyCode', 'N/A')}
**Enable Payments:** {a.get('EnablePaymentsToAccount', False)}
**Show In Expense Claims:** {a.get('ShowInExpenseClaims', False)}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_account",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_account(
        code: str = Field(..., description="Account code (e.g., '610')"),
        name: str = Field(..., description="Account name"),
        account_type: str = Field(..., description="Type: 'BANK', 'REVENUE', 'EXPENSE', 'CURRENT', 'FIXED', 'EQUITY', etc."),
        description: Optional[str] = Field(None, description="Account description"),
        tax_type: Optional[str] = Field(None, description="Tax type (e.g., 'OUTPUT', 'INPUT', 'NONE')"),
    ) -> str:
        """Create a new account in the chart of accounts."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            account_data = {
                "Code": code,
                "Name": name,
                "Type": account_type.upper(),
            }
            if description:
                account_data["Description"] = description
            if tax_type:
                account_data["TaxType"] = tax_type

            response = await _xero_put(token, xero_config.tenant_id, "Accounts", account_data)
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("Accounts", [{}])[0]
            return f"Account created: **{created.get('Code', 'N/A')}** - {created.get('Name', 'N/A')} (ID: `{created.get('AccountID', 'N/A')}`)"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_update_account",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_update_account(
        account_id: str = Field(..., description="Account ID (GUID)"),
        name: Optional[str] = Field(None, description="Update name"),
        code: Optional[str] = Field(None, description="Update code"),
        description: Optional[str] = Field(None, description="Update description"),
        tax_type: Optional[str] = Field(None, description="Update tax type"),
        status: Optional[str] = Field(None, description="Update status: 'ACTIVE' or 'ARCHIVED'"),
    ) -> str:
        """Update an account."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            update_data = {"AccountID": account_id}
            if name:
                update_data["Name"] = name
            if code:
                update_data["Code"] = code
            if description is not None:
                update_data["Description"] = description
            if tax_type:
                update_data["TaxType"] = tax_type
            if status:
                update_data["Status"] = status.upper()
            if len(update_data) == 1:
                return "Error: No updates specified."

            response = await _xero_post(token, xero_config.tenant_id, f"Accounts/{account_id}", update_data)
            error = _check_xero_response(response)
            if error:
                return error
            return f"Account `{account_id}` updated."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ITEMS
    # =========================================================================

    @mcp.tool(
        name="xero_get_items",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_items(
        search: Optional[str] = Field(None, description="Search by name or code"),
        limit: int = Field(50, description="Max results"),
    ) -> str:
        """List inventory items."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {}
            if search:
                params["where"] = f'Name.Contains("{search}") OR Code.Contains("{search}")'

            response = await _xero_get(token, xero_config.tenant_id, "Items", params)
            error = _check_xero_response(response)
            if error:
                return error
            items = response.json().get("Items", [])[:limit]
            if not items:
                return "No items found."
            results = []
            for i in items:
                price = i.get("SalesDetails", {}).get("UnitPrice", 0)
                cost = i.get("PurchaseDetails", {}).get("UnitPrice", 0)
                results.append(f"- **{i.get('Code', 'N/A')}** - {i.get('Name', 'N/A')} | Sale: ${price:,.2f} | Cost: ${cost:,.2f}")
            return f"## Items ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_item",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_item(
        item_id: str = Field(..., description="Item ID (GUID) or Item Code"),
    ) -> str:
        """Get full item details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"Items/{item_id}")
            error = _check_xero_response(response)
            if error:
                return error
            i = response.json().get("Items", [{}])[0]
            sales = i.get("SalesDetails", {})
            purchase = i.get("PurchaseDetails", {})
            return f"""## Item: {i.get('Name', 'N/A')}

**Item ID:** `{i.get('ItemID', 'N/A')}`
**Code:** {i.get('Code', 'N/A')}
**Description:** {i.get('Description', 'N/A')}
**Is Sold:** {i.get('IsSold', False)}
**Is Purchased:** {i.get('IsPurchased', False)}
**Is Tracked:** {i.get('IsTrackedAsInventory', False)}

**Sales Details:**
  Unit Price: ${sales.get('UnitPrice', 0):,.2f}
  Account Code: {sales.get('AccountCode', 'N/A')}
  Tax Type: {sales.get('TaxType', 'N/A')}

**Purchase Details:**
  Unit Price: ${purchase.get('UnitPrice', 0):,.2f}
  Account Code: {purchase.get('AccountCode', 'N/A')}
  Tax Type: {purchase.get('TaxType', 'N/A')}

**Quantity On Hand:** {i.get('QuantityOnHand', 'N/A')}
**Total Cost Pool:** ${i.get('TotalCostPool', 0):,.2f}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_item",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_item(
        code: str = Field(..., description="Item code"),
        name: str = Field(..., description="Item name"),
        description: Optional[str] = Field(None, description="Item description"),
        sales_unit_price: Optional[float] = Field(None, description="Sales unit price"),
        sales_account_code: Optional[str] = Field(None, description="Sales account code (e.g., '200')"),
        purchase_unit_price: Optional[float] = Field(None, description="Purchase unit price"),
        purchase_account_code: Optional[str] = Field(None, description="Purchase account code (e.g., '300')"),
    ) -> str:
        """Create an inventory item."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            item_data = {"Code": code, "Name": name}
            if description:
                item_data["Description"] = description
            if sales_unit_price is not None or sales_account_code:
                item_data["SalesDetails"] = {}
                if sales_unit_price is not None:
                    item_data["SalesDetails"]["UnitPrice"] = sales_unit_price
                if sales_account_code:
                    item_data["SalesDetails"]["AccountCode"] = sales_account_code
            if purchase_unit_price is not None or purchase_account_code:
                item_data["PurchaseDetails"] = {}
                if purchase_unit_price is not None:
                    item_data["PurchaseDetails"]["UnitPrice"] = purchase_unit_price
                if purchase_account_code:
                    item_data["PurchaseDetails"]["AccountCode"] = purchase_account_code

            response = await _xero_put(token, xero_config.tenant_id, "Items", {"Items": [item_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("Items", [{}])[0]
            return f"Item created: **{created.get('Code', 'N/A')}** - {created.get('Name', 'N/A')}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_update_item",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_update_item(
        item_id: str = Field(..., description="Item ID (GUID)"),
        name: Optional[str] = Field(None, description="Update name"),
        code: Optional[str] = Field(None, description="Update code"),
        description: Optional[str] = Field(None, description="Update description"),
        sales_unit_price: Optional[float] = Field(None, description="Update sales price"),
        purchase_unit_price: Optional[float] = Field(None, description="Update purchase price"),
    ) -> str:
        """Update an item."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            update_data = {"ItemID": item_id}
            if name:
                update_data["Name"] = name
            if code:
                update_data["Code"] = code
            if description is not None:
                update_data["Description"] = description
            if sales_unit_price is not None:
                update_data["SalesDetails"] = {"UnitPrice": sales_unit_price}
            if purchase_unit_price is not None:
                update_data["PurchaseDetails"] = {"UnitPrice": purchase_unit_price}
            if len(update_data) == 1:
                return "Error: No updates specified."

            response = await _xero_post(token, xero_config.tenant_id, f"Items/{item_id}", {"Items": [update_data]})
            error = _check_xero_response(response)
            if error:
                return error
            return f"Item `{item_id}` updated."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_delete_item",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def xero_delete_item(
        item_id: str = Field(..., description="Item ID (GUID)"),
    ) -> str:
        """Delete an item. Item must not be used on any transactions."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_delete(token, xero_config.tenant_id, f"Items/{item_id}")
            error = _check_xero_response(response)
            if error:
                return error
            return f"Item `{item_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # MANUAL JOURNALS
    # =========================================================================

    @mcp.tool(
        name="xero_get_manual_journals",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_manual_journals(
        status: Optional[str] = Field(None, description="Filter: 'DRAFT', 'POSTED', 'VOIDED'"),
        days: int = Field(90, description="Journals from last N days"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List manual journals."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            where_parts = []
            if status:
                where_parts.append(f'Status=="{status.upper()}"')
            params = {"order": "Date DESC"}
            if where_parts:
                params["where"] = " AND ".join(where_parts)

            response = await _xero_get(token, xero_config.tenant_id, "ManualJournals", params)
            error = _check_xero_response(response)
            if error:
                return error
            journals = response.json().get("ManualJournals", [])[:limit]
            if not journals:
                return "No manual journals found."
            results = []
            for j in journals:
                results.append(f"- **{j.get('Narration', 'N/A')[:60]}** | {j.get('Status', '')} | {j.get('DateString', '')[:10]} | ID: `{j.get('ManualJournalID', '')[:8]}...`")
            return f"## Manual Journals ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_manual_journal",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_manual_journal(
        journal_id: str = Field(..., description="Manual Journal ID (GUID)"),
    ) -> str:
        """Get full manual journal details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"ManualJournals/{journal_id}")
            error = _check_xero_response(response)
            if error:
                return error
            j = response.json().get("ManualJournals", [{}])[0]
            lines = []
            for line in j.get("JournalLines", []):
                dr = line.get("DebitAmount", 0)
                cr = line.get("CreditAmount", 0)
                lines.append(f"- {line.get('AccountCode', 'N/A')} | {line.get('Description', 'N/A')} | Debit: ${dr:,.2f} | Credit: ${cr:,.2f}")
            return f"""## Manual Journal

**ID:** `{j.get('ManualJournalID', 'N/A')}`
**Narration:** {j.get('Narration', 'N/A')}
**Status:** {j.get('Status', 'N/A')}
**Date:** {j.get('DateString', '')[:10]}

## Journal Lines
{chr(10).join(lines) if lines else 'No lines'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_manual_journal",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_manual_journal(
        narration: str = Field(..., description="Journal narration/description"),
        journal_lines: str = Field(..., description='JSON array: [{"account_code": "200", "description": "...", "debit_amount": 100.00}, {"account_code": "400", "description": "...", "credit_amount": 100.00}]'),
        date: Optional[str] = Field(None, description="Journal date (YYYY-MM-DD, defaults to today)"),
        status: str = Field("DRAFT", description="Status: 'DRAFT' or 'POSTED'"),
    ) -> str:
        """Create a manual journal. Debits must equal credits."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            lines = json.loads(journal_lines)

            journal_data = {
                "Narration": narration,
                "Date": date or datetime.now().strftime("%Y-%m-%d"),
                "Status": status.upper(),
                "JournalLines": [
                    {
                        "AccountCode": line.get("account_code", ""),
                        "Description": line.get("description", ""),
                        **({"DebitAmount": line["debit_amount"]} if "debit_amount" in line else {}),
                        **({"CreditAmount": line["credit_amount"]} if "credit_amount" in line else {}),
                    }
                    for line in lines
                ],
            }

            response = await _xero_put(token, xero_config.tenant_id, "ManualJournals", {"ManualJournals": [journal_data]})
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("ManualJournals", [{}])[0]
            return f"Manual journal created: **{narration[:50]}** (ID: `{created.get('ManualJournalID', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in journal_lines."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # EMPLOYEES
    # =========================================================================

    @mcp.tool(
        name="xero_get_employees",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_employees(
        search: Optional[str] = Field(None, description="Search by name"),
        status: Optional[str] = Field(None, description="Filter: 'ACTIVE' or 'ARCHIVED'"),
        limit: int = Field(50, description="Max results"),
    ) -> str:
        """List employees."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            where_parts = []
            if search:
                where_parts.append(f'(FirstName.Contains("{search}") OR LastName.Contains("{search}"))')
            if status:
                where_parts.append(f'Status=="{status.upper()}"')
            params = {}
            if where_parts:
                params["where"] = " AND ".join(where_parts)

            response = await _xero_get(token, xero_config.tenant_id, "Employees", params)
            error = _check_xero_response(response)
            if error:
                return error
            employees = response.json().get("Employees", [])[:limit]
            if not employees:
                return "No employees found."
            results = []
            for e in employees:
                results.append(f"- **{e.get('FirstName', '')} {e.get('LastName', '')}** | Status: {e.get('Status', '')} | ID: `{e.get('EmployeeID', 'N/A')}`")
            return f"## Employees ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_employee",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_employee(
        employee_id: str = Field(..., description="Employee ID (GUID)"),
    ) -> str:
        """Get full employee details."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"Employees/{employee_id}")
            error = _check_xero_response(response)
            if error:
                return error
            emp = response.json().get("Employees", [{}])[0]
            return f"""## Employee: {emp.get('FirstName', '')} {emp.get('LastName', '')}

**Employee ID:** `{emp.get('EmployeeID', 'N/A')}`
**Status:** {emp.get('Status', 'N/A')}
**External Link:** {emp.get('ExternalLink', {}).get('Url', 'N/A')}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_employee",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_employee(
        first_name: str = Field(..., description="First name"),
        last_name: str = Field(..., description="Last name"),
    ) -> str:
        """Create a new employee."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_put(
                token, xero_config.tenant_id, "Employees",
                {"Employees": [{"FirstName": first_name, "LastName": last_name}]},
            )
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("Employees", [{}])[0]
            return f"Employee created: **{first_name} {last_name}** (ID: `{created.get('EmployeeID', 'N/A')}`)"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_update_employee",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_update_employee(
        employee_id: str = Field(..., description="Employee ID (GUID)"),
        first_name: Optional[str] = Field(None, description="Update first name"),
        last_name: Optional[str] = Field(None, description="Update last name"),
        status: Optional[str] = Field(None, description="Update status: 'ACTIVE' or 'ARCHIVED'"),
    ) -> str:
        """Update an employee."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            update_data = {"EmployeeID": employee_id}
            if first_name:
                update_data["FirstName"] = first_name
            if last_name:
                update_data["LastName"] = last_name
            if status:
                update_data["Status"] = status.upper()
            if len(update_data) == 1:
                return "Error: No updates specified."

            response = await _xero_post(token, xero_config.tenant_id, "Employees", {"Employees": [update_data]})
            error = _check_xero_response(response)
            if error:
                return error
            return f"Employee `{employee_id}` updated."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # TAX RATES
    # =========================================================================

    @mcp.tool(
        name="xero_get_tax_rates",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_tax_rates() -> str:
        """List all tax rates configured in Xero."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "TaxRates")
            error = _check_xero_response(response)
            if error:
                return error
            rates = response.json().get("TaxRates", [])
            if not rates:
                return "No tax rates found."
            results = []
            for r in rates:
                if r.get("Status") == "DELETED":
                    continue
                results.append(f"- **{r.get('Name', 'N/A')}** | Type: {r.get('TaxType', 'N/A')} | Rate: {r.get('EffectiveRate', 0)}% | Status: {r.get('Status', '')}")
            return f"## Tax Rates ({len(results)} active)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # CURRENCIES
    # =========================================================================

    @mcp.tool(
        name="xero_get_currencies",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_currencies() -> str:
        """List currencies enabled in the Xero organisation."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "Currencies")
            error = _check_xero_response(response)
            if error:
                return error
            currencies = response.json().get("Currencies", [])
            if not currencies:
                return "No currencies found."
            results = [f"- **{c.get('Code', 'N/A')}** - {c.get('Description', 'N/A')}" for c in currencies]
            return f"## Currencies ({len(results)} enabled)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # TRACKING CATEGORIES
    # =========================================================================

    @mcp.tool(
        name="xero_get_tracking_categories",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_tracking_categories() -> str:
        """List tracking categories and their options."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "TrackingCategories")
            error = _check_xero_response(response)
            if error:
                return error
            categories = response.json().get("TrackingCategories", [])
            if not categories:
                return "No tracking categories found."
            results = []
            for cat in categories:
                options = [o.get("Name", "") for o in cat.get("Options", [])]
                results.append(f"- **{cat.get('Name', 'N/A')}** (Status: {cat.get('Status', '')})\n  Options: {', '.join(options) if options else 'None'}")
            return f"## Tracking Categories\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # BRANDING THEMES
    # =========================================================================

    @mcp.tool(
        name="xero_get_branding_themes",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_branding_themes() -> str:
        """List available branding themes for invoices/quotes."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "BrandingThemes")
            error = _check_xero_response(response)
            if error:
                return error
            themes = response.json().get("BrandingThemes", [])
            if not themes:
                return "No branding themes found."
            results = [f"- **{t.get('Name', 'N/A')}** | ID: `{t.get('BrandingThemeID', 'N/A')}` | Sort Order: {t.get('SortOrder', 'N/A')}" for t in themes]
            return f"## Branding Themes\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # OVERPAYMENTS & PREPAYMENTS
    # =========================================================================

    @mcp.tool(
        name="xero_get_overpayments",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_overpayments(
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List overpayments."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "Overpayments", {"order": "Date DESC"})
            error = _check_xero_response(response)
            if error:
                return error
            items = response.json().get("Overpayments", [])[:limit]
            if not items:
                return "No overpayments found."
            results = []
            for o in items:
                results.append(f"- **${o.get('Total', 0):,.2f}** | {o.get('Contact', {}).get('Name', 'N/A')} | Remaining: ${o.get('RemainingCredit', 0):,.2f} | {o.get('DateString', '')[:10]} | {o.get('Status', '')}")
            return f"## Overpayments ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_get_prepayments",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_prepayments(
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List prepayments."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "Prepayments", {"order": "Date DESC"})
            error = _check_xero_response(response)
            if error:
                return error
            items = response.json().get("Prepayments", [])[:limit]
            if not items:
                return "No prepayments found."
            results = []
            for p in items:
                results.append(f"- **${p.get('Total', 0):,.2f}** | {p.get('Contact', {}).get('Name', 'N/A')} | Remaining: ${p.get('RemainingCredit', 0):,.2f} | {p.get('DateString', '')[:10]} | {p.get('Status', '')}")
            return f"## Prepayments ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # JOURNALS (System)
    # =========================================================================

    @mcp.tool(
        name="xero_get_journals",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_journals(
        offset: int = Field(0, description="Journal number offset to start from"),
        limit: int = Field(20, description="Max results (max 100)"),
    ) -> str:
        """List system journals (read-only audit trail)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {"offset": offset}
            response = await _xero_get(token, xero_config.tenant_id, "Journals", params)
            error = _check_xero_response(response)
            if error:
                return error
            journals = response.json().get("Journals", [])[:limit]
            if not journals:
                return "No journals found."
            results = []
            for j in journals:
                results.append(f"- **#{j.get('JournalNumber', 'N/A')}** | {j.get('JournalDate', '')[:10]} | Source: {j.get('SourceType', 'N/A')} | ID: `{j.get('JournalID', '')[:8]}...`")
            return f"## System Journals ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # LINKED TRANSACTIONS
    # =========================================================================

    @mcp.tool(
        name="xero_get_linked_transactions",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_linked_transactions(
        source_transaction_id: Optional[str] = Field(None, description="Source transaction ID to filter by"),
        contact_id: Optional[str] = Field(None, description="Contact ID to filter by"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List linked transactions (bill-to-invoice links)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {}
            if source_transaction_id:
                params["SourceTransactionID"] = source_transaction_id
            if contact_id:
                params["ContactID"] = contact_id

            response = await _xero_get(token, xero_config.tenant_id, "LinkedTransactions", params)
            error = _check_xero_response(response)
            if error:
                return error
            links = response.json().get("LinkedTransactions", [])[:limit]
            if not links:
                return "No linked transactions found."
            results = []
            for lt in links:
                results.append(f"- Source: `{lt.get('SourceTransactionID', 'N/A')[:8]}...` -> Target: `{lt.get('TargetTransactionID', 'N/A')[:8] if lt.get('TargetTransactionID') else 'None'}...` | Status: {lt.get('Status', '')}")
            return f"## Linked Transactions ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # EXPENSE CLAIMS (DEPRECATED but still in API)
    # =========================================================================

    @mcp.tool(
        name="xero_get_expense_claims",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_expense_claims(
        status: Optional[str] = Field(None, description="Filter: 'SUBMITTED', 'AUTHORISED', 'PAID'"),
        limit: int = Field(20, description="Max results"),
    ) -> str:
        """List expense claims."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {}
            if status:
                params["where"] = f'Status=="{status.upper()}"'

            response = await _xero_get(token, xero_config.tenant_id, "ExpenseClaims", params)
            error = _check_xero_response(response)
            if error:
                return error
            claims = response.json().get("ExpenseClaims", [])[:limit]
            if not claims:
                return "No expense claims found."
            results = []
            for c in claims:
                user = c.get("User", {})
                results.append(f"- **${c.get('Total', 0):,.2f}** | {user.get('FirstName', '')} {user.get('LastName', '')} | Status: {c.get('Status', '')} | Due: ${c.get('AmountDue', 0):,.2f}")
            return f"## Expense Claims ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # REPEATING INVOICES
    # =========================================================================

    @mcp.tool(
        name="xero_get_repeating_invoices",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_repeating_invoices() -> str:
        """List repeating (recurring) invoice templates."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "RepeatingInvoices")
            error = _check_xero_response(response)
            if error:
                return error
            invoices = response.json().get("RepeatingInvoices", [])
            if not invoices:
                return "No repeating invoices found."
            results = []
            for ri in invoices:
                schedule = ri.get("Schedule", {})
                results.append(f"- **{ri.get('Contact', {}).get('Name', 'N/A')}** | {ri.get('Type', '')} | Total: ${ri.get('Total', 0):,.2f} | Every {schedule.get('Period', '?')} {schedule.get('Unit', '')} | Next: {schedule.get('NextScheduledDate', 'N/A')[:10] if schedule.get('NextScheduledDate') else 'N/A'} | Status: {ri.get('Status', '')}")
            return f"## Repeating Invoices ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # CONTACT GROUPS
    # =========================================================================

    @mcp.tool(
        name="xero_get_contact_groups",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_contact_groups() -> str:
        """List contact groups."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "ContactGroups")
            error = _check_xero_response(response)
            if error:
                return error
            groups = response.json().get("ContactGroups", [])
            if not groups:
                return "No contact groups found."
            results = []
            for g in groups:
                contacts = g.get("Contacts", [])
                results.append(f"- **{g.get('Name', 'N/A')}** | {len(contacts)} contacts | Status: {g.get('Status', '')} | ID: `{g.get('ContactGroupID', 'N/A')}`")
            return f"## Contact Groups\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_create_contact_group",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_create_contact_group(
        name: str = Field(..., description="Group name"),
    ) -> str:
        """Create a contact group."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_put(
                token, xero_config.tenant_id, "ContactGroups",
                {"ContactGroups": [{"Name": name}]},
            )
            error = _check_xero_response(response)
            if error:
                return error
            created = response.json().get("ContactGroups", [{}])[0]
            return f"Contact group created: **{name}** (ID: `{created.get('ContactGroupID', 'N/A')}`)"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_add_contacts_to_group",
        annotations={"readOnlyHint": False, "destructiveHint": False},
    )
    async def xero_add_contacts_to_group(
        group_id: str = Field(..., description="Contact Group ID (GUID)"),
        contact_ids: str = Field(..., description='JSON array of Contact IDs: ["id1", "id2"]'),
    ) -> str:
        """Add contacts to a contact group."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            ids = json.loads(contact_ids)
            contacts = [{"ContactID": cid} for cid in ids]
            response = await _xero_put(
                token, xero_config.tenant_id, f"ContactGroups/{group_id}/Contacts",
                {"Contacts": contacts},
            )
            error = _check_xero_response(response)
            if error:
                return error
            return f"Added {len(ids)} contact(s) to group `{group_id}`."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in contact_ids."
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
            response = await _xero_get(token, xero_config.tenant_id, "Reports/AgedReceivablesByContact")
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

    @mcp.tool(
        name="xero_aged_payables",
        annotations={"readOnlyHint": True},
    )
    async def xero_aged_payables(
        contact_name: Optional[str] = Field(None, description="Filter by contact name"),
        min_amount: float = Field(0, description="Minimum amount outstanding"),
    ) -> str:
        """Get aged payables report - what you owe and for how long."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "Reports/AgedPayablesByContact")
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

    @mcp.tool(
        name="xero_profit_and_loss",
        annotations={"readOnlyHint": True},
    )
    async def xero_profit_and_loss(
        from_date: Optional[str] = Field(None, description="Start date (YYYY-MM-DD, defaults to start of financial year)"),
        to_date: Optional[str] = Field(None, description="End date (YYYY-MM-DD, defaults to today)"),
        periods: int = Field(1, description="Number of periods to compare"),
        timeframe: str = Field("MONTH", description="Period type: 'MONTH', 'QUARTER', 'YEAR'"),
    ) -> str:
        """Get Profit & Loss report."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {"periods": periods, "timeframe": timeframe}
            if from_date:
                params["fromDate"] = from_date
            if to_date:
                params["toDate"] = to_date

            response = await _xero_get(token, xero_config.tenant_id, "Reports/ProfitAndLoss", params)
            error = _check_xero_response(response)
            if error:
                return error
            report = response.json().get("Reports", [{}])[0]
            return _format_report(report, "Profit & Loss")
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_balance_sheet",
        annotations={"readOnlyHint": True},
    )
    async def xero_balance_sheet(
        date: Optional[str] = Field(None, description="Report date (YYYY-MM-DD, defaults to today)"),
        periods: int = Field(1, description="Number of periods to compare"),
        timeframe: str = Field("MONTH", description="Period type: 'MONTH', 'QUARTER', 'YEAR'"),
    ) -> str:
        """Get Balance Sheet report."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {"periods": periods, "timeframe": timeframe}
            if date:
                params["date"] = date

            response = await _xero_get(token, xero_config.tenant_id, "Reports/BalanceSheet", params)
            error = _check_xero_response(response)
            if error:
                return error
            report = response.json().get("Reports", [{}])[0]
            return _format_report(report, "Balance Sheet")
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_trial_balance",
        annotations={"readOnlyHint": True},
    )
    async def xero_trial_balance(
        date: Optional[str] = Field(None, description="Report date (YYYY-MM-DD, defaults to today)"),
    ) -> str:
        """Get Trial Balance report."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {}
            if date:
                params["date"] = date

            response = await _xero_get(token, xero_config.tenant_id, "Reports/TrialBalance", params)
            error = _check_xero_response(response)
            if error:
                return error
            report = response.json().get("Reports", [{}])[0]
            return _format_report(report, "Trial Balance")
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_bank_summary",
        annotations={"readOnlyHint": True},
    )
    async def xero_bank_summary(
        from_date: Optional[str] = Field(None, description="Start date (YYYY-MM-DD)"),
        to_date: Optional[str] = Field(None, description="End date (YYYY-MM-DD)"),
    ) -> str:
        """Get Bank Summary report showing balances across all bank accounts."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {}
            if from_date:
                params["fromDate"] = from_date
            if to_date:
                params["toDate"] = to_date

            response = await _xero_get(token, xero_config.tenant_id, "Reports/BankSummary", params)
            error = _check_xero_response(response)
            if error:
                return error
            report = response.json().get("Reports", [{}])[0]
            return _format_report(report, "Bank Summary")
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_budget_summary",
        annotations={"readOnlyHint": True},
    )
    async def xero_budget_summary(
        date: Optional[str] = Field(None, description="Report date (YYYY-MM-DD)"),
        periods: int = Field(12, description="Number of periods"),
        timeframe: str = Field("MONTH", description="Period type: 'MONTH', 'QUARTER'"),
    ) -> str:
        """Get Budget Summary report."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {"periods": periods, "timeframe": timeframe}
            if date:
                params["date"] = date

            response = await _xero_get(token, xero_config.tenant_id, "Reports/BudgetSummary", params)
            error = _check_xero_response(response)
            if error:
                return error
            report = response.json().get("Reports", [{}])[0]
            return _format_report(report, "Budget Summary")
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="xero_executive_summary",
        annotations={"readOnlyHint": True},
    )
    async def xero_executive_summary(
        date: Optional[str] = Field(None, description="Report date (YYYY-MM-DD)"),
    ) -> str:
        """Get Executive Summary report with key financial metrics."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            params = {}
            if date:
                params["date"] = date

            response = await _xero_get(token, xero_config.tenant_id, "Reports/ExecutiveSummary", params)
            error = _check_xero_response(response)
            if error:
                return error
            report = response.json().get("Reports", [{}])[0]
            return _format_report(report, "Executive Summary")
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ATTACHMENTS
    # =========================================================================

    @mcp.tool(
        name="xero_get_attachments",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_attachments(
        endpoint: str = Field(..., description="Entity type: 'Invoices', 'CreditNotes', 'BankTransactions', 'Contacts', 'Accounts', 'ManualJournals', 'PurchaseOrders', 'Quotes'"),
        entity_id: str = Field(..., description="Entity ID (GUID)"),
    ) -> str:
        """List attachments on a Xero entity."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, f"{endpoint}/{entity_id}/Attachments")
            error = _check_xero_response(response)
            if error:
                return error
            attachments = response.json().get("Attachments", [])
            if not attachments:
                return f"No attachments found on {endpoint}/{entity_id}."
            results = []
            for a in attachments:
                results.append(f"- **{a.get('FileName', 'N/A')}** | {a.get('MimeType', '')} | Size: {a.get('ContentLength', 0)} bytes | ID: `{a.get('AttachmentID', 'N/A')}`")
            return f"## Attachments ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # USERS
    # =========================================================================

    @mcp.tool(
        name="xero_get_users",
        annotations={"readOnlyHint": True},
    )
    async def xero_get_users() -> str:
        """List users in the Xero organisation."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            token = await xero_config.get_access_token()
            response = await _xero_get(token, xero_config.tenant_id, "Users")
            error = _check_xero_response(response)
            if error:
                return error
            users = response.json().get("Users", [])
            if not users:
                return "No users found."
            results = []
            for u in users:
                results.append(f"- **{u.get('FirstName', '')} {u.get('LastName', '')}** | Email: {u.get('EmailAddress', 'N/A')} | Role: {u.get('OrganisationRole', 'N/A')} | Status: {u.get('IsSubscriber', False)}")
            return f"## Xero Users ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"


# =============================================================================
# Report formatting helper
# =============================================================================

def _format_report(report: dict, title: str) -> str:
    """Format a Xero report into readable markdown."""
    lines = [f"## {title}"]

    report_title = report.get("ReportName", "")
    if report_title:
        lines.append(f"**{report_title}**")

    report_date = report.get("ReportDate", "")
    if report_date:
        lines.append(f"*Date: {report_date}*")

    lines.append("")

    for row_group in report.get("Rows", []):
        row_type = row_group.get("RowType", "")

        if row_type == "Header":
            cells = row_group.get("Cells", [])
            headers = [c.get("Value", "") for c in cells]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        elif row_type == "Section":
            title_val = row_group.get("Title", "")
            if title_val:
                lines.append(f"\n### {title_val}")

            for row in row_group.get("Rows", []):
                cells = row.get("Cells", [])
                values = [c.get("Value", "") for c in cells]
                if row.get("RowType") == "SummaryRow":
                    lines.append(f"| **{values[0]}** | " + " | ".join(f"**{v}**" for v in values[1:]) + " |")
                else:
                    lines.append("| " + " | ".join(values) + " |")

        elif row_type == "Row":
            cells = row_group.get("Cells", [])
            values = [c.get("Value", "") for c in cells]
            lines.append("| " + " | ".join(values) + " |")

        elif row_type == "SummaryRow":
            cells = row_group.get("Cells", [])
            values = [c.get("Value", "") for c in cells]
            lines.append(f"| **{values[0]}** | " + " | ".join(f"**{v}**" for v in values[1:]) + " |")

    return "\n".join(lines)
