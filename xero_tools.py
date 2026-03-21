"""
Xero Accounting Integration Tools for Crowd IT MCP Server

This module provides comprehensive Xero accounting capabilities including
invoicing, contacts, bills, payments, credit notes, quotes, purchase orders,
bank transactions, and financial reports.

Authentication: Uses OAuth2 authorization_code flow with refresh tokens.
The refresh token is automatically rotated on each use (Xero rotates tokens).

Environment Variables / Secrets:
    XERO_CLIENT_ID: Xero OAuth2 app client ID
    XERO_CLIENT_SECRET: Xero OAuth2 app client secret
    XERO_TENANT_ID: Xero organisation tenant ID
    XERO_REFRESH_TOKEN: OAuth2 refresh token (rotated automatically)
"""

import os
import json
import logging
from typing import Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

XERO_API_BASE = "https://api.xero.com/api.xro/2.0"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"


# =============================================================================
# Configuration and Authentication
# =============================================================================

class XeroConfig:
    """Xero API configuration using OAuth2 with refresh tokens."""

    def __init__(self):
        self.client_id = os.getenv("XERO_CLIENT_ID", "")
        self._client_secret: Optional[str] = None
        self._tenant_id: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    def _get_secret(self, name: str) -> str:
        try:
            from app.core.config import get_secret_sync
            val = get_secret_sync(name)
            if val:
                return val
        except Exception:
            pass
        return os.getenv(name, "")

    @property
    def client_secret(self) -> str:
        if not self._client_secret:
            self._client_secret = self._get_secret("XERO_CLIENT_SECRET")
        return self._client_secret

    @property
    def tenant_id(self) -> str:
        if not self._tenant_id:
            self._tenant_id = self._get_secret("XERO_TENANT_ID")
        return self._tenant_id

    @property
    def refresh_token(self) -> str:
        if not self._refresh_token:
            self._refresh_token = self._get_secret("XERO_REFRESH_TOKEN")
        return self._refresh_token

    @refresh_token.setter
    def refresh_token(self, value: str):
        self._refresh_token = value

    @property
    def is_configured(self) -> bool:
        return all([self.client_id, self.client_secret, self.tenant_id, self.refresh_token])

    async def get_access_token(self) -> str:
        """Get access token, refreshing if needed. Xero rotates refresh tokens."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        import httpx
        import base64

        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                XERO_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {auth_header}",
                },
            )
            response.raise_for_status()
            data = response.json()

        self._access_token = data["access_token"]
        expires_in = data.get("expires_in", 1800)
        self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

        # Xero rotates refresh tokens — store the new one
        new_refresh = data.get("refresh_token")
        if new_refresh:
            self._refresh_token = new_refresh
            # Persist to Secret Manager if on Cloud Run
            try:
                on_cloud_run = bool(os.getenv("K_SERVICE"))
                if on_cloud_run:
                    from app.core.config import _update_secret
                    _update_secret("XERO_REFRESH_TOKEN", new_refresh)
            except Exception as e:
                logger.warning(f"Could not persist rotated Xero refresh token: {e}")

        return self._access_token

    async def xero_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_body: dict = None,
    ) -> Any:
        """Make a Xero API request."""
        import httpx

        token = await self.get_access_token()
        url = f"{XERO_API_BASE}{endpoint}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method,
                url=url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Xero-Tenant-Id": self.tenant_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                params=params,
                json=json_body,
            )
            response.raise_for_status()
            if response.status_code == 204:
                return {"status": "success"}
            return response.json()


# =============================================================================
# Helper utilities
# =============================================================================

def _fmt(data: Any) -> str:
    """Format API response as indented JSON string."""
    return json.dumps(data, indent=2, default=str)


def _date_param(days: int = 0, date_str: str = None) -> str:
    """Return an ISO date string. If date_str is given use it, else offset from today."""
    if date_str:
        return date_str
    d = datetime.utcnow() - timedelta(days=days)
    return d.strftime("%Y-%m-%dT00:00:00")


# =============================================================================
# Tool Registration
# =============================================================================

def register_xero_tools(mcp, xero_config: "XeroConfig"):
    """Register all Xero tools with the MCP server."""

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_contacts",
        annotations={
            "title": "List Xero Contacts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_contacts(
        is_customer: bool = False,
        is_supplier: bool = False,
        search: str = "",
        page: int = 1,
    ) -> str:
        """List Xero contacts with optional filtering by customer/supplier status or name search."""
        if not xero_config.is_configured:
            return "Error: Xero not configured. Set XERO_CLIENT_ID, XERO_CLIENT_SECRET, XERO_TENANT_ID, and XERO_REFRESH_TOKEN."
        try:
            params: dict[str, Any] = {"page": page}
            where_clauses = []
            if is_customer:
                where_clauses.append('IsCustomer==true')
            if is_supplier:
                where_clauses.append('IsSupplier==true')
            if search:
                where_clauses.append(f'Name.Contains("{search}")')
            if where_clauses:
                params["where"] = "&&".join(where_clauses)

            data = await xero_config.xero_request("GET", "/Contacts", params=params)
            contacts = data.get("Contacts", [])
            summary = []
            for c in contacts:
                summary.append({
                    "ContactID": c.get("ContactID"),
                    "Name": c.get("Name"),
                    "EmailAddress": c.get("EmailAddress"),
                    "IsCustomer": c.get("IsCustomer"),
                    "IsSupplier": c.get("IsSupplier"),
                    "AccountsReceivableOutstanding": c.get("Balances", {}).get("AccountsReceivable", {}).get("Outstanding"),
                    "AccountsPayableOutstanding": c.get("Balances", {}).get("AccountsPayable", {}).get("Outstanding"),
                })
            return _fmt({"count": len(summary), "page": page, "contacts": summary})
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_get_contact",
        annotations={
            "title": "Get Xero Contact Detail",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_contact(contact_id: str) -> str:
        """Get detailed information for a specific Xero contact by ID."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            data = await xero_config.xero_request("GET", f"/Contacts/{contact_id}")
            contacts = data.get("Contacts", [])
            if not contacts:
                return f"Error: Contact {contact_id} not found."
            return _fmt(contacts[0])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_create_contact",
        annotations={
            "title": "Create Xero Contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def xero_create_contact(
        name: str,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
        phone: str = "",
        is_customer: bool = True,
        is_supplier: bool = False,
    ) -> str:
        """Create a new contact in Xero."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            contact: dict[str, Any] = {"Name": name}
            if email:
                contact["EmailAddress"] = email
            if first_name:
                contact["FirstName"] = first_name
            if last_name:
                contact["LastName"] = last_name
            if phone:
                contact["Phones"] = [{"PhoneType": "DEFAULT", "PhoneNumber": phone}]
            contact["IsCustomer"] = is_customer
            contact["IsSupplier"] = is_supplier

            data = await xero_config.xero_request("POST", "/Contacts", json_body={"Contacts": [contact]})
            return _fmt(data.get("Contacts", [{}])[0])
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_invoices",
        annotations={
            "title": "List Xero Invoices",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_invoices(
        status: str = "",
        days: int = 90,
        contact_id: str = "",
        invoice_type: str = "",
        page: int = 1,
    ) -> str:
        """List Xero invoices. Filter by status (DRAFT, SUBMITTED, AUTHORISED, PAID, VOIDED), days back, contact, or type (ACCREC for sales, ACCPAY for bills)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {"page": page}
            where_clauses = []
            if status:
                where_clauses.append(f'Status=="{status}"')
            if days:
                since = _date_param(days=days)
                where_clauses.append(f'Date>DateTime({since[:4]},{since[5:7].lstrip("0")},{since[8:10].lstrip("0")})')
            if contact_id:
                where_clauses.append(f'Contact.ContactID==Guid("{contact_id}")')
            if invoice_type:
                where_clauses.append(f'Type=="{invoice_type}"')
            if where_clauses:
                params["where"] = "&&".join(where_clauses)

            data = await xero_config.xero_request("GET", "/Invoices", params=params)
            invoices = data.get("Invoices", [])
            summary = []
            for inv in invoices:
                summary.append({
                    "InvoiceID": inv.get("InvoiceID"),
                    "InvoiceNumber": inv.get("InvoiceNumber"),
                    "Type": inv.get("Type"),
                    "Contact": inv.get("Contact", {}).get("Name"),
                    "Date": inv.get("DateString"),
                    "DueDate": inv.get("DueDateString"),
                    "Status": inv.get("Status"),
                    "SubTotal": inv.get("SubTotal"),
                    "Total": inv.get("Total"),
                    "AmountDue": inv.get("AmountDue"),
                    "AmountPaid": inv.get("AmountPaid"),
                    "CurrencyCode": inv.get("CurrencyCode"),
                })
            return _fmt({"count": len(summary), "page": page, "invoices": summary})
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_get_invoice",
        annotations={
            "title": "Get Xero Invoice Detail",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_invoice(invoice_id: str) -> str:
        """Get full details for a specific Xero invoice including line items."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            data = await xero_config.xero_request("GET", f"/Invoices/{invoice_id}")
            invoices = data.get("Invoices", [])
            if not invoices:
                return f"Error: Invoice {invoice_id} not found."
            return _fmt(invoices[0])
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_create_invoice",
        annotations={
            "title": "Create Xero Invoice",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def xero_create_invoice(
        contact_id: str,
        line_items: str,
        invoice_type: str = "ACCREC",
        due_date: str = "",
        reference: str = "",
        status: str = "DRAFT",
    ) -> str:
        """Create a new invoice in Xero. line_items is a JSON array of objects with Description, Quantity, UnitAmount, AccountCode. invoice_type is ACCREC (sales) or ACCPAY (bill)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            items = json.loads(line_items)
            invoice: dict[str, Any] = {
                "Type": invoice_type,
                "Contact": {"ContactID": contact_id},
                "LineItems": items,
                "Status": status,
            }
            if due_date:
                invoice["DueDate"] = due_date
            if reference:
                invoice["Reference"] = reference

            data = await xero_config.xero_request("POST", "/Invoices", json_body={"Invoices": [invoice]})
            return _fmt(data.get("Invoices", [{}])[0])
        except json.JSONDecodeError:
            return "Error: line_items must be a valid JSON array."
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_update_invoice_status",
        annotations={
            "title": "Update Xero Invoice Status",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_update_invoice_status(invoice_id: str, status: str) -> str:
        """Update the status of a Xero invoice. Valid statuses: DRAFT, SUBMITTED, AUTHORISED, VOIDED."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            data = await xero_config.xero_request(
                "POST",
                f"/Invoices/{invoice_id}",
                json_body={"Invoices": [{"InvoiceID": invoice_id, "Status": status}]},
            )
            return _fmt(data.get("Invoices", [{}])[0])
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_payments",
        annotations={
            "title": "List Xero Payments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_payments(days: int = 90, page: int = 1) -> str:
        """List payments recorded in Xero within the specified number of days."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {"page": page}
            if days:
                since = _date_param(days=days)
                params["where"] = f'Date>DateTime({since[:4]},{since[5:7].lstrip("0")},{since[8:10].lstrip("0")})'
            data = await xero_config.xero_request("GET", "/Payments", params=params)
            payments = data.get("Payments", [])
            summary = []
            for p in payments:
                summary.append({
                    "PaymentID": p.get("PaymentID"),
                    "Date": p.get("DateString"),
                    "Amount": p.get("Amount"),
                    "Status": p.get("Status"),
                    "Invoice": p.get("Invoice", {}).get("InvoiceNumber"),
                    "Account": p.get("Account", {}).get("Name"),
                })
            return _fmt({"count": len(summary), "page": page, "payments": summary})
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_create_payment",
        annotations={
            "title": "Create Xero Payment",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def xero_create_payment(
        invoice_id: str,
        account_code: str,
        amount: float,
        date: str = "",
        reference: str = "",
    ) -> str:
        """Record a payment against a Xero invoice. account_code is the bank account code (e.g. '090')."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            payment: dict[str, Any] = {
                "Invoice": {"InvoiceID": invoice_id},
                "Account": {"Code": account_code},
                "Amount": amount,
            }
            if date:
                payment["Date"] = date
            if reference:
                payment["Reference"] = reference

            data = await xero_config.xero_request("POST", "/Payments", json_body={"Payments": [payment]})
            return _fmt(data.get("Payments", [{}])[0])
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Credit Notes
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_credit_notes",
        annotations={
            "title": "List Xero Credit Notes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_credit_notes(status: str = "", page: int = 1) -> str:
        """List credit notes in Xero. Optionally filter by status (DRAFT, SUBMITTED, AUTHORISED, PAID, VOIDED)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {"page": page}
            if status:
                params["where"] = f'Status=="{status}"'
            data = await xero_config.xero_request("GET", "/CreditNotes", params=params)
            notes = data.get("CreditNotes", [])
            summary = []
            for cn in notes:
                summary.append({
                    "CreditNoteID": cn.get("CreditNoteID"),
                    "CreditNoteNumber": cn.get("CreditNoteNumber"),
                    "Type": cn.get("Type"),
                    "Contact": cn.get("Contact", {}).get("Name"),
                    "Date": cn.get("DateString"),
                    "Status": cn.get("Status"),
                    "Total": cn.get("Total"),
                    "RemainingCredit": cn.get("RemainingCredit"),
                })
            return _fmt({"count": len(summary), "page": page, "credit_notes": summary})
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_quotes",
        annotations={
            "title": "List Xero Quotes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_quotes(status: str = "", page: int = 1) -> str:
        """List quotes in Xero. Optionally filter by status (DRAFT, SENT, ACCEPTED, DECLINED)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {"page": page}
            if status:
                params["where"] = f'Status=="{status}"'
            data = await xero_config.xero_request("GET", "/Quotes", params=params)
            quotes = data.get("Quotes", [])
            summary = []
            for q in quotes:
                summary.append({
                    "QuoteID": q.get("QuoteID"),
                    "QuoteNumber": q.get("QuoteNumber"),
                    "Contact": q.get("Contact", {}).get("Name"),
                    "Date": q.get("DateString"),
                    "ExpiryDate": q.get("ExpiryDateString"),
                    "Status": q.get("Status"),
                    "SubTotal": q.get("SubTotal"),
                    "Total": q.get("Total"),
                })
            return _fmt({"count": len(summary), "page": page, "quotes": summary})
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_create_quote",
        annotations={
            "title": "Create Xero Quote",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def xero_create_quote(
        contact_id: str,
        line_items: str,
        expiry_date: str = "",
        reference: str = "",
        title: str = "",
        summary: str = "",
    ) -> str:
        """Create a new quote in Xero. line_items is a JSON array of objects with Description, Quantity, UnitAmount, AccountCode."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            items = json.loads(line_items)
            quote: dict[str, Any] = {
                "Contact": {"ContactID": contact_id},
                "LineItems": items,
                "Status": "DRAFT",
            }
            if expiry_date:
                quote["ExpiryDate"] = expiry_date
            if reference:
                quote["Reference"] = reference
            if title:
                quote["Title"] = title
            if summary:
                quote["Summary"] = summary

            data = await xero_config.xero_request("POST", "/Quotes", json_body={"Quotes": [quote]})
            return _fmt(data.get("Quotes", [{}])[0])
        except json.JSONDecodeError:
            return "Error: line_items must be a valid JSON array."
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Purchase Orders
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_purchase_orders",
        annotations={
            "title": "List Xero Purchase Orders",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_purchase_orders(status: str = "", page: int = 1) -> str:
        """List purchase orders in Xero. Optionally filter by status (DRAFT, SUBMITTED, AUTHORISED, BILLED, DELETED)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {"page": page}
            if status:
                params["where"] = f'Status=="{status}"'
            data = await xero_config.xero_request("GET", "/PurchaseOrders", params=params)
            orders = data.get("PurchaseOrders", [])
            result = []
            for po in orders:
                result.append({
                    "PurchaseOrderID": po.get("PurchaseOrderID"),
                    "PurchaseOrderNumber": po.get("PurchaseOrderNumber"),
                    "Contact": po.get("Contact", {}).get("Name"),
                    "Date": po.get("DateString"),
                    "DeliveryDate": po.get("DeliveryDateString"),
                    "Status": po.get("Status"),
                    "SubTotal": po.get("SubTotal"),
                    "Total": po.get("Total"),
                })
            return _fmt({"count": len(result), "page": page, "purchase_orders": result})
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Bank Transactions
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_bank_transactions",
        annotations={
            "title": "List Xero Bank Transactions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_bank_transactions(
        days: int = 90,
        bank_account_id: str = "",
        status: str = "",
        page: int = 1,
    ) -> str:
        """List bank transactions in Xero. Filter by days back, bank account, or status (AUTHORISED, DELETED)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {"page": page}
            where_clauses = []
            if days:
                since = _date_param(days=days)
                where_clauses.append(f'Date>DateTime({since[:4]},{since[5:7].lstrip("0")},{since[8:10].lstrip("0")})')
            if bank_account_id:
                where_clauses.append(f'BankAccount.AccountID==Guid("{bank_account_id}")')
            if status:
                where_clauses.append(f'Status=="{status}"')
            if where_clauses:
                params["where"] = "&&".join(where_clauses)

            data = await xero_config.xero_request("GET", "/BankTransactions", params=params)
            txns = data.get("BankTransactions", [])
            result = []
            for t in txns:
                result.append({
                    "BankTransactionID": t.get("BankTransactionID"),
                    "Type": t.get("Type"),
                    "Contact": t.get("Contact", {}).get("Name"),
                    "Date": t.get("DateString"),
                    "Status": t.get("Status"),
                    "SubTotal": t.get("SubTotal"),
                    "Total": t.get("Total"),
                    "BankAccount": t.get("BankAccount", {}).get("Name"),
                    "IsReconciled": t.get("IsReconciled"),
                })
            return _fmt({"count": len(result), "page": page, "bank_transactions": result})
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Accounts (Chart of Accounts)
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_accounts",
        annotations={
            "title": "List Xero Accounts (Chart of Accounts)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_accounts(account_type: str = "", account_class: str = "") -> str:
        """List accounts in Xero's chart of accounts. Optionally filter by type (BANK, REVENUE, EXPENSE, etc.) or class (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {}
            where_clauses = []
            if account_type:
                where_clauses.append(f'Type=="{account_type}"')
            if account_class:
                where_clauses.append(f'Class=="{account_class}"')
            if where_clauses:
                params["where"] = "&&".join(where_clauses)

            data = await xero_config.xero_request("GET", "/Accounts", params=params)
            accounts = data.get("Accounts", [])
            result = []
            for a in accounts:
                result.append({
                    "AccountID": a.get("AccountID"),
                    "Code": a.get("Code"),
                    "Name": a.get("Name"),
                    "Type": a.get("Type"),
                    "Class": a.get("Class"),
                    "Status": a.get("Status"),
                    "BankAccountNumber": a.get("BankAccountNumber"),
                    "TaxType": a.get("TaxType"),
                })
            return _fmt({"count": len(result), "accounts": result})
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Financial Reports
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_profit_and_loss",
        annotations={
            "title": "Xero Profit and Loss Report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_profit_and_loss(
        from_date: str = "",
        to_date: str = "",
        periods: int = 0,
        timeframe: str = "",
    ) -> str:
        """Get Profit and Loss report from Xero. Dates in YYYY-MM-DD format. timeframe: MONTH, QUARTER, YEAR."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {}
            if from_date:
                params["fromDate"] = from_date
            if to_date:
                params["toDate"] = to_date
            if periods:
                params["periods"] = periods
            if timeframe:
                params["timeframe"] = timeframe
            data = await xero_config.xero_request("GET", "/Reports/ProfitAndLoss", params=params)
            return _fmt(data)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_get_balance_sheet",
        annotations={
            "title": "Xero Balance Sheet Report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_balance_sheet(date: str = "", periods: int = 0, timeframe: str = "") -> str:
        """Get Balance Sheet report from Xero. date in YYYY-MM-DD format. timeframe: MONTH, QUARTER, YEAR."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {}
            if date:
                params["date"] = date
            if periods:
                params["periods"] = periods
            if timeframe:
                params["timeframe"] = timeframe
            data = await xero_config.xero_request("GET", "/Reports/BalanceSheet", params=params)
            return _fmt(data)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_get_trial_balance",
        annotations={
            "title": "Xero Trial Balance Report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_trial_balance(date: str = "") -> str:
        """Get Trial Balance report from Xero. date in YYYY-MM-DD format."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {}
            if date:
                params["date"] = date
            data = await xero_config.xero_request("GET", "/Reports/TrialBalance", params=params)
            return _fmt(data)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_get_aged_receivables",
        annotations={
            "title": "Xero Aged Receivables Report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_aged_receivables(date: str = "", contact_id: str = "") -> str:
        """Get Aged Receivables report from Xero showing outstanding customer invoices by age."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {}
            if date:
                params["date"] = date
            if contact_id:
                params["contactID"] = contact_id
            data = await xero_config.xero_request("GET", "/Reports/AgedReceivablesByContact", params=params)
            return _fmt(data)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(
        name="xero_get_aged_payables",
        annotations={
            "title": "Xero Aged Payables Report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_aged_payables(date: str = "", contact_id: str = "") -> str:
        """Get Aged Payables report from Xero showing outstanding supplier bills by age."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            params: dict[str, Any] = {}
            if date:
                params["date"] = date
            if contact_id:
                params["contactID"] = contact_id
            data = await xero_config.xero_request("GET", "/Reports/AgedPayablesByContact", params=params)
            return _fmt(data)
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Items (Products/Services)
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_items",
        annotations={
            "title": "List Xero Items",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_items() -> str:
        """List all items (products/services) in Xero."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            data = await xero_config.xero_request("GET", "/Items")
            items = data.get("Items", [])
            result = []
            for item in items:
                result.append({
                    "ItemID": item.get("ItemID"),
                    "Code": item.get("Code"),
                    "Name": item.get("Name"),
                    "Description": item.get("Description"),
                    "PurchaseDescription": item.get("PurchaseDescription"),
                    "UnitPrice": item.get("SalesDetails", {}).get("UnitPrice"),
                    "AccountCode": item.get("SalesDetails", {}).get("AccountCode"),
                    "IsTrackedAsInventory": item.get("IsTrackedAsInventory"),
                    "QuantityOnHand": item.get("QuantityOnHand"),
                })
            return _fmt({"count": len(result), "items": result})
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Tax Rates
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_tax_rates",
        annotations={
            "title": "List Xero Tax Rates",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_tax_rates() -> str:
        """List all tax rates configured in Xero."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            data = await xero_config.xero_request("GET", "/TaxRates")
            rates = data.get("TaxRates", [])
            result = []
            for r in rates:
                result.append({
                    "Name": r.get("Name"),
                    "TaxType": r.get("TaxType"),
                    "EffectiveRate": r.get("EffectiveRate"),
                    "Status": r.get("Status"),
                    "CanApplyToAssets": r.get("CanApplyToAssets"),
                    "CanApplyToEquity": r.get("CanApplyToEquity"),
                    "CanApplyToExpenses": r.get("CanApplyToExpenses"),
                    "CanApplyToLiabilities": r.get("CanApplyToLiabilities"),
                    "CanApplyToRevenue": r.get("CanApplyToRevenue"),
                })
            return _fmt({"count": len(result), "tax_rates": result})
        except Exception as e:
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Organisation
    # ------------------------------------------------------------------

    @mcp.tool(
        name="xero_get_organisation",
        annotations={
            "title": "Get Xero Organisation Info",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def xero_get_organisation() -> str:
        """Get details about the connected Xero organisation."""
        if not xero_config.is_configured:
            return "Error: Xero not configured."
        try:
            data = await xero_config.xero_request("GET", "/Organisation")
            orgs = data.get("Organisations", [])
            if not orgs:
                return "Error: No organisation data returned."
            org = orgs[0]
            return _fmt({
                "Name": org.get("Name"),
                "LegalName": org.get("LegalName"),
                "ShortCode": org.get("ShortCode"),
                "OrganisationType": org.get("OrganisationType"),
                "BaseCurrency": org.get("BaseCurrency"),
                "CountryCode": org.get("CountryCode"),
                "Timezone": org.get("Timezone"),
                "FinancialYearEndDay": org.get("FinancialYearEndDay"),
                "FinancialYearEndMonth": org.get("FinancialYearEndMonth"),
                "SalesTaxBasis": org.get("SalesTaxBasis"),
                "SalesTaxPeriod": org.get("SalesTaxPeriod"),
                "Edition": org.get("Edition"),
                "OrganisationStatus": org.get("OrganisationStatus"),
            })
        except Exception as e:
            return f"Error: {e}"

    logger.info("Xero tools registered successfully (25 tools)")
