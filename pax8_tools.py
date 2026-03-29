"""
Pax8 Integration Tools for Crowd IT MCP Server

This module provides Pax8 cloud marketplace capabilities via the Pax8 API.

Capabilities:
- Subscriptions: list and get details
- Companies: list and get details
- Products: list and get details with pricing

Authentication: Uses OAuth 2.0 client credentials flow with token caching.

Environment Variables:
    PAX8_CLIENT_ID: OAuth client ID
    PAX8_CLIENT_SECRET: OAuth client secret
"""

import os
import logging
from typing import Optional
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration and Authentication
# =============================================================================

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
                    "grant_type": "client_credentials",
                },
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            # Pax8 tokens are valid for 24 hours, refresh 1 hour early
            expires_in = data.get("expires_in", 86400)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 3600)
            return self._access_token


# =============================================================================
# Tool Registration
# =============================================================================

def register_pax8_tools(mcp, pax8_config: "Pax8Config"):
    """Register all Pax8 tools with the MCP server."""

    try:
        from pydantic import Field
    except ImportError:
        from dataclasses import field as Field

    # =========================================================================
    # SUBSCRIPTIONS
    # =========================================================================

    @mcp.tool(
        name="pax8_list_subscriptions",
        annotations={"readOnlyHint": True},
    )
    async def pax8_list_subscriptions(
        company_id: Optional[str] = Field(None, description="Filter by Pax8 company ID"),
        product_id: Optional[str] = Field(None, description="Filter by product ID"),
        status: Optional[str] = Field(None, description="Filter by status: Active, Cancelled, PendingManual, etc."),
        page: int = Field(0, description="Page number (0-indexed)"),
        size: int = Field(50, description="Page size (max 200)"),
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
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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

    @mcp.tool(
        name="pax8_get_subscription",
        annotations={"readOnlyHint": True},
    )
    async def pax8_get_subscription(
        subscription_id: str = Field(..., description="Pax8 subscription ID"),
    ) -> str:
        """Get detailed subscription information from Pax8."""
        if not pax8_config.is_configured:
            return "Error: Pax8 not configured."

        try:
            token = await pax8_config.get_access_token()

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{pax8_config.base_url}/subscriptions/{subscription_id}",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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

    # =========================================================================
    # COMPANIES
    # =========================================================================

    @mcp.tool(
        name="pax8_list_companies",
        annotations={"readOnlyHint": True},
    )
    async def pax8_list_companies(
        city: Optional[str] = Field(None, description="Filter by city"),
        country: Optional[str] = Field(None, description="Filter by country (e.g., 'AU', 'US')"),
        page: int = Field(0, description="Page number (0-indexed)"),
        size: int = Field(50, description="Page size (max 200)"),
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
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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

    @mcp.tool(
        name="pax8_get_company",
        annotations={"readOnlyHint": True},
    )
    async def pax8_get_company(
        company_id: str = Field(..., description="Pax8 company ID"),
    ) -> str:
        """Get detailed company information from Pax8."""
        if not pax8_config.is_configured:
            return "Error: Pax8 not configured."

        try:
            token = await pax8_config.get_access_token()

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{pax8_config.base_url}/companies/{company_id}",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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

    # =========================================================================
    # PRODUCTS
    # =========================================================================

    @mcp.tool(
        name="pax8_list_products",
        annotations={"readOnlyHint": True},
    )
    async def pax8_list_products(
        vendor_name: Optional[str] = Field(None, description="Filter by vendor name (e.g., 'Microsoft')"),
        page: int = Field(0, description="Page number (0-indexed)"),
        size: int = Field(50, description="Page size (max 200)"),
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
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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

    @mcp.tool(
        name="pax8_get_product",
        annotations={"readOnlyHint": True},
    )
    async def pax8_get_product(
        product_id: str = Field(..., description="Pax8 product ID (UUID)"),
    ) -> str:
        """
        Get detailed product information from Pax8 including pricing.

        Returns product details including name, vendor, pricing tiers, and provisioning info.
        Use this to check partner pricing for Microsoft 365, Exchange Online, and other products.
        """
        if not pax8_config.is_configured:
            return "Error: Pax8 not configured."

        try:
            token = await pax8_config.get_access_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            async with httpx.AsyncClient() as client:
                # Get product details
                response = await client.get(
                    f"{pax8_config.base_url}/products/{product_id}",
                    headers=headers,
                )
                response.raise_for_status()
                product = response.json()

                # Get product pricing
                pricing = []
                try:
                    pricing_response = await client.get(
                        f"{pax8_config.base_url}/products/{product_id}/pricing",
                        headers=headers,
                    )
                    if pricing_response.status_code == 200:
                        pricing_data = pricing_response.json()
                        pricing = pricing_data.get("content", []) if isinstance(pricing_data, dict) else pricing_data
                except Exception:
                    pass  # Pricing endpoint may not be available for all products

                # Get provisioning details
                provisioning = {}
                try:
                    prov_response = await client.get(
                        f"{pax8_config.base_url}/products/{product_id}/provisioning-details",
                        headers=headers,
                    )
                    if prov_response.status_code == 200:
                        provisioning = prov_response.json()
                except Exception:
                    pass

            # Format output
            lines = [
                f"## {product.get('name', 'Unknown Product')}",
                f"",
                f"**Product ID:** `{product_id}`",
                f"**Vendor:** {product.get('vendorName', 'Unknown')}",
                f"**SKU:** {product.get('sku', 'N/A')}",
            ]

            if product.get("shortDescription"):
                lines.append(f"**Description:** {product.get('shortDescription')}")

            # Billing info
            lines.append(f"")
            lines.append(f"### Billing")
            lines.append(f"- **Term:** {product.get('billingTerm', 'N/A')}")
            lines.append(f"- **Unit of Measurement:** {product.get('unitOfMeasurement', 'N/A')}")

            # Pricing
            if pricing:
                lines.append(f"")
                lines.append(f"### Pricing")
                for price in pricing[:5]:  # Limit to first 5 pricing tiers
                    if isinstance(price, dict):
                        partner_buy = price.get("partnerBuyPrice", price.get("price", "N/A"))
                        msrp = price.get("suggestedRetailPrice", price.get("msrp", "N/A"))
                        currency = price.get("currencyCode", "USD")
                        commitment = price.get("commitmentTermQuantity", "")
                        commitment_unit = price.get("commitmentTermUnit", "")
                        billing_term = price.get("billingTerm", "")

                        if commitment and commitment_unit:
                            lines.append(f"- **{commitment} {commitment_unit} ({billing_term}):** Partner: ${partner_buy} {currency} | MSRP: ${msrp} {currency}")
                        else:
                            lines.append(f"- **Partner Price:** ${partner_buy} {currency} | **MSRP:** ${msrp} {currency}")

            # Provisioning
            if provisioning:
                lines.append(f"")
                lines.append(f"### Provisioning")
                if provisioning.get("provisioningType"):
                    lines.append(f"- **Type:** {provisioning.get('provisioningType')}")
                if provisioning.get("minQuantity"):
                    lines.append(f"- **Min Quantity:** {provisioning.get('minQuantity')}")
                if provisioning.get("maxQuantity"):
                    lines.append(f"- **Max Quantity:** {provisioning.get('maxQuantity')}")

            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error: {str(e)}"
