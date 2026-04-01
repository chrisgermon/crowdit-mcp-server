"""
Microsoft Partner Center Integration Tools for Crowd IT MCP Server

This module provides Microsoft Partner Center capabilities via the Partner Center REST API.

Capabilities:
- Customers: list, get details, list users
- Subscriptions: list, get details, update (quantity/status)
- Orders: list, get details
- Licenses: list customer licenses, list user licenses
- Service Requests: list, get details
- Invoices: list, get details, get line items
- Offers/Products: list offers, get offer details
- Usage: customer usage summary, subscription usage records

Authentication: Uses OAuth 2.0 client credentials flow with token caching.

Environment Variables:
    PARTNER_CENTER_TENANT_ID: Azure AD tenant ID
    PARTNER_CENTER_CLIENT_ID: Application (client) ID
    PARTNER_CENTER_CLIENT_SECRET: Client secret
"""

import os
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration and Authentication
# =============================================================================

class PartnerCenterConfig:
    def __init__(self):
        self.tenant_id = os.getenv("PARTNER_CENTER_TENANT_ID", "")
        self.client_id = os.getenv("PARTNER_CENTER_CLIENT_ID", "")
        self._client_secret = ""
        self._access_token = None
        self._token_expiry = None
        self._secrets_loaded = False

    def _load_secrets(self):
        if self._secrets_loaded:
            return
        if not self.tenant_id:
            try:
                from app.core.config import get_secret_sync
                self.tenant_id = get_secret_sync("PARTNER_CENTER_TENANT_ID") or self.tenant_id
            except Exception:
                pass
        if not self.client_id:
            try:
                from app.core.config import get_secret_sync
                self.client_id = get_secret_sync("PARTNER_CENTER_CLIENT_ID") or self.client_id
            except Exception:
                pass
        if not self._client_secret:
            try:
                from app.core.config import get_secret_sync
                self._client_secret = get_secret_sync("PARTNER_CENTER_CLIENT_SECRET") or ""
            except Exception:
                pass
        self._secrets_loaded = True

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return all([self.tenant_id, self.client_id, self._client_secret])

    async def get_access_token(self) -> str:
        """Get valid access token, requesting new one if expired."""
        from datetime import datetime, timedelta

        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        self._load_secrets()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "resource": "https://graph.windows.net",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expiry = datetime.now() + timedelta(
                seconds=int(data.get("expires_in", 3600)) - 60
            )
            return self._access_token


# =============================================================================
# Helper Functions
# =============================================================================

BASE_URL = "https://api.partnercenter.microsoft.com"


async def _pc_request(config, method, path, params=None, json_data=None):
    """Make an authenticated request to the Partner Center API."""
    token = await config.get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "MS-RequestId": "",
        "MS-CorrelationId": "",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(
            method,
            f"{BASE_URL}{path}",
            headers=headers,
            params=params,
            json=json_data,
        )
        return resp


def _check_pc_response(response):
    """Check Partner Center API response for errors."""
    if response.status_code >= 400:
        try:
            data = response.json()
            msg = data.get("description", "") or data.get("message", "") or str(data)
            return f"Partner Center API Error: {response.status_code} - {msg}"
        except Exception:
            return f"Partner Center API Error: {response.status_code} - {response.text}"
    return None


# =============================================================================
# Tool Registration
# =============================================================================

def register_partner_center_tools(mcp, config: "PartnerCenterConfig") -> None:
    """Register all Microsoft Partner Center tools with the MCP server."""

    try:
        from pydantic import Field
    except ImportError:
        from dataclasses import field as Field

    NOT_CONFIGURED_MSG = (
        "Error: Partner Center not configured. "
        "Set PARTNER_CENTER_TENANT_ID, PARTNER_CENTER_CLIENT_ID, "
        "and PARTNER_CENTER_CLIENT_SECRET environment variables."
    )

    # =========================================================================
    # CUSTOMERS
    # =========================================================================

    @mcp.tool(
        name="partner_list_customers",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_customers(
        filter: Optional[str] = Field(None, description="OData filter expression (e.g., startswith(CompanyProfile/CompanyName,'Contoso'))"),
        page_size: int = Field(100, description="Number of results per page (max 500)"),
    ) -> str:
        """List customers from Microsoft Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            params = {"size": min(max(1, page_size), 500)}
            if filter:
                params["filter"] = filter

            resp = await _pc_request(config, "GET", "/v1/customers", params=params)
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_customer",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_customer(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
    ) -> str:
        """Get detailed customer information from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(config, "GET", f"/v1/customers/{customer_id}")
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_list_customer_users",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_customer_users(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
    ) -> str:
        """List users for a customer in Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(config, "GET", f"/v1/customers/{customer_id}/users")
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # SUBSCRIPTIONS
    # =========================================================================

    @mcp.tool(
        name="partner_list_subscriptions",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_subscriptions(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
    ) -> str:
        """List all subscriptions for a customer in Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(config, "GET", f"/v1/customers/{customer_id}/subscriptions")
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_subscription",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_subscription(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
        subscription_id: str = Field(..., description="Subscription ID"),
    ) -> str:
        """Get detailed subscription information from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/customers/{customer_id}/subscriptions/{subscription_id}",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_update_subscription",
    )
    async def partner_update_subscription(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
        subscription_id: str = Field(..., description="Subscription ID"),
        quantity: Optional[int] = Field(None, description="New seat/license quantity"),
        status: Optional[str] = Field(None, description="New status (e.g., 'active', 'suspended')"),
    ) -> str:
        """Update a subscription in Partner Center (change quantity or status).

        First fetches the current subscription, applies changes, then sends the update.
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            # First get the current subscription
            get_resp = await _pc_request(
                config, "GET",
                f"/v1/customers/{customer_id}/subscriptions/{subscription_id}",
            )
            error = _check_pc_response(get_resp)
            if error:
                return error

            sub_data = get_resp.json()

            # Apply requested changes
            if quantity is not None:
                sub_data["quantity"] = quantity
            if status is not None:
                sub_data["status"] = status

            # Send the update
            resp = await _pc_request(
                config, "PATCH",
                f"/v1/customers/{customer_id}/subscriptions/{subscription_id}",
                json_data=sub_data,
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ORDERS
    # =========================================================================

    @mcp.tool(
        name="partner_list_orders",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_orders(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
    ) -> str:
        """List all orders for a customer in Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(config, "GET", f"/v1/customers/{customer_id}/orders")
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_order",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_order(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
        order_id: str = Field(..., description="Order ID"),
    ) -> str:
        """Get detailed order information from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/customers/{customer_id}/orders/{order_id}",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # LICENSES
    # =========================================================================

    @mcp.tool(
        name="partner_list_customer_licenses",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_customer_licenses(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
    ) -> str:
        """List subscribed SKUs (license summary) for a customer."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/customers/{customer_id}/subscribedskus",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_list_user_licenses",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_user_licenses(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
        user_id: str = Field(..., description="User ID within the customer tenant"),
    ) -> str:
        """List licenses assigned to a specific user within a customer tenant."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/customers/{customer_id}/users/{user_id}/licenses",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # SERVICE REQUESTS
    # =========================================================================

    @mcp.tool(
        name="partner_list_service_requests",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_service_requests() -> str:
        """List service requests (support tickets) from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(config, "GET", "/v1/servicerequests")
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_service_request",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_service_request(
        service_request_id: str = Field(..., description="Service request ID"),
    ) -> str:
        """Get detailed service request information from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/servicerequests/{service_request_id}",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # INVOICES
    # =========================================================================

    @mcp.tool(
        name="partner_list_invoices",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_invoices() -> str:
        """List invoices from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(config, "GET", "/v1/invoices")
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_invoice",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_invoice(
        invoice_id: str = Field(..., description="Invoice ID"),
    ) -> str:
        """Get detailed invoice information from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(config, "GET", f"/v1/invoices/{invoice_id}")
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_invoice_line_items",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_invoice_line_items(
        invoice_id: str = Field(..., description="Invoice ID"),
        billing_provider: str = Field(..., description="Billing provider (e.g., 'Office', 'Azure', 'OneTime')"),
        invoice_line_item_type: str = Field(..., description="Line item type (e.g., 'BillingLineItems', 'UsageLineItems')"),
    ) -> str:
        """Get invoice line items from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/invoices/{invoice_id}/lineitems/{billing_provider}/{invoice_line_item_type}",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # OFFERS / PRODUCTS
    # =========================================================================

    @mcp.tool(
        name="partner_list_offers",
        annotations={"readOnlyHint": True},
    )
    async def partner_list_offers(
        country: str = Field("US", description="Country code for offer availability (e.g., 'US', 'AU', 'GB')"),
    ) -> str:
        """List available offers from Partner Center catalog."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET", "/v1/offers",
                params={"country": country},
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_offer",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_offer(
        offer_id: str = Field(..., description="Offer ID"),
        country: str = Field("US", description="Country code for offer availability (e.g., 'US', 'AU', 'GB')"),
    ) -> str:
        """Get detailed offer information from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/offers/{offer_id}",
                params={"country": country},
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # USAGE
    # =========================================================================

    @mcp.tool(
        name="partner_get_customer_usage",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_customer_usage(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
    ) -> str:
        """Get usage summary for a customer from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/customers/{customer_id}/usagesummary",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(
        name="partner_get_subscription_usage",
        annotations={"readOnlyHint": True},
    )
    async def partner_get_subscription_usage(
        customer_id: str = Field(..., description="Partner Center customer (tenant) ID"),
        subscription_id: str = Field(..., description="Subscription ID"),
    ) -> str:
        """Get usage records for a specific subscription from Partner Center."""
        if not config.is_configured:
            return NOT_CONFIGURED_MSG

        try:
            resp = await _pc_request(
                config, "GET",
                f"/v1/customers/{customer_id}/subscriptions/{subscription_id}/usagerecords",
            )
            error = _check_pc_response(resp)
            if error:
                return error

            data = resp.json()
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"
