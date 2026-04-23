"""Gorelo PSA integration tools."""

import os
import json
import logging
from typing import Optional

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)


class GoreloConfig:
    def __init__(self):
        self.api_key = os.getenv("GORELO_API_KEY", "")
        self.base_url = "https://api.usw.gorelo.io"
        self._secrets_loaded = False

    def _load_secrets(self) -> None:
        if self._secrets_loaded:
            return
        if not self.api_key:
            try:
                from app.core.config import get_secret_sync
                self.api_key = get_secret_sync("GORELO_API_KEY") or ""
            except Exception:
                pass
        self._secrets_loaded = True

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return bool(self.api_key)

    def headers(self):
        self._load_secrets()
        return {"X-API-Key": self.api_key, "Content-Type": "application/json", "Accept": "application/json"}


def register_gorelo_tools(mcp, config: "GoreloConfig") -> None:
    """Register all Gorelo tools with the MCP server."""

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_clients() -> str:
        """List all Gorelo clients/companies."""
        if not config.is_configured:
            return "Error: Gorelo not configured (missing GORELO_API_KEY)."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/clients", headers=config.headers())
                response.raise_for_status()
                clients = response.json()
            if not clients:
                return "No clients found."
            results = []
            for c in clients[:50]:
                results.append(f"- **{c.get('name', 'Unknown')}** (ID: `{c.get('id', 'N/A')}`)")
            return f"## Gorelo Clients ({len(clients)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_get_client(client_id: int = Field(..., description="Client ID")) -> str:
        """Get details for a specific Gorelo client."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/clients/{client_id}", headers=config.headers())
                response.raise_for_status()
                data = response.json()
            return f"## Client: {data.get('name', 'Unknown')}\n\n**ID:** {data.get('id')}\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_client_locations(client_id: int = Field(..., description="Client ID")) -> str:
        """List all locations for a specific Gorelo client."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/clients/{client_id}/locations", headers=config.headers())
                response.raise_for_status()
                locations = response.json()
            if not locations:
                return f"No locations found for client {client_id}."
            results = []
            for loc in locations:
                results.append(f"- **{loc.get('name', 'Unknown')}** (ID: `{loc.get('id', 'N/A')}`)")
            return f"## Locations for Client {client_id}\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_contacts(
        client_id: Optional[int] = Field(None, description="Filter by client ID"),
        contact_ids: Optional[str] = Field(None, description="Comma-separated contact IDs to retrieve")
    ) -> str:
        """List Gorelo contacts, optionally filtered by client."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            params = {}
            if client_id:
                params["clientid"] = client_id
            if contact_ids:
                params["ContactIds"] = contact_ids
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/contacts", params=params, headers=config.headers())
                response.raise_for_status()
                contacts = response.json()
            if not contacts:
                return "No contacts found."
            results = []
            for c in contacts[:50]:
                name = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or "Unknown"
                email = c.get('email', 'N/A')
                results.append(f"- **{name}** ({email}) - ID: `{c.get('id', 'N/A')}`")
            return f"## Gorelo Contacts ({len(contacts)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_get_contact(contact_id: int = Field(..., description="Contact ID")) -> str:
        """Get details for a specific Gorelo contact."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/contacts/{contact_id}", headers=config.headers())
                response.raise_for_status()
                data = response.json()
            name = f"{data.get('firstName', '')} {data.get('lastName', '')}".strip() or "Unknown"
            return f"## Contact: {name}\n\n**ID:** {data.get('id')}\n**Email:** {data.get('email', 'N/A')}\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_agents() -> str:
        """List all Gorelo agents (managed devices)."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/assets/agents", headers=config.headers())
                response.raise_for_status()
                agents = response.json()
            if not agents:
                return "No agents found."
            results = []
            for a in agents[:50]:
                name = a.get('hostname', a.get('name', 'Unknown'))
                status = a.get('status', 'N/A')
                results.append(f"- **{name}** (Status: {status}) - ID: `{a.get('id', 'N/A')}`")
            return f"## Gorelo Agents ({len(agents)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_get_agent(agent_id: str = Field(..., description="Agent UUID")) -> str:
        """Get details for a specific Gorelo agent."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/assets/agents/{agent_id}", headers=config.headers())
                response.raise_for_status()
                data = response.json()
            name = data.get('hostname', data.get('name', 'Unknown'))
            return f"## Agent: {name}\n\n**ID:** {data.get('id')}\n**Status:** {data.get('status', 'N/A')}\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False})
    async def gorelo_create_alert(
        name: str = Field(..., description="Alert name/title"),
        client_id: int = Field(..., description="Client ID"),
        severity: str = Field(..., description="Alert severity (e.g., 'low', 'medium', 'high', 'critical')"),
        service_provider_id: int = Field(..., description="Service provider ID")
    ) -> str:
        """Create a new alert in Gorelo."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            payload = {
                "name": name,
                "clientId": client_id,
                "severity": severity,
                "serviceProviderId": service_provider_id
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{config.base_url}/v1/alerts/", json=payload, headers=config.headers())
                response.raise_for_status()
            return f"Alert '{name}' created successfully for client {client_id}."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False})
    async def gorelo_create_client(
        name: str = Field(..., description="Client/company name"),
        address: Optional[str] = Field(None, description="Street address"),
        city: Optional[str] = Field(None, description="City"),
        state: Optional[str] = Field(None, description="State/province"),
        post_code: Optional[str] = Field(None, description="Postal/ZIP code"),
        country: Optional[str] = Field(None, description="Country"),
        phone: Optional[str] = Field(None, description="Phone number"),
        website: Optional[str] = Field(None, description="Website URL"),
        domain: Optional[str] = Field(None, description="Email domain (e.g., 'company.com')"),
        tax_id: Optional[str] = Field(None, description="Tax ID / ABN"),
        notes: Optional[str] = Field(None, description="Notes about the client")
    ) -> str:
        """Create a new client in Gorelo."""
        if not config.is_configured:
            return "Error: Gorelo not configured (missing GORELO_API_KEY)."
        try:
            payload = {"name": name}
            if address:
                payload["address"] = address
            if city:
                payload["city"] = city
            if state:
                payload["state"] = state
            if post_code:
                payload["postCode"] = post_code
            if country:
                payload["country"] = country
            if phone:
                payload["phone"] = phone
            if website:
                payload["website"] = website
            if domain:
                payload["domain"] = domain
            if tax_id:
                payload["taxId"] = tax_id
            if notes:
                payload["notes"] = notes
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{config.base_url}/v1/clients", json=payload, headers=config.headers())
                response.raise_for_status()
                data = response.json()
            return f"Client '{name}' created successfully (ID: {data.get('id', 'N/A')}).\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False})
    async def gorelo_update_client(
        client_id: int = Field(..., description="Client ID to update"),
        name: Optional[str] = Field(None, description="Updated client/company name"),
        address: Optional[str] = Field(None, description="Updated street address"),
        city: Optional[str] = Field(None, description="Updated city"),
        state: Optional[str] = Field(None, description="Updated state/province"),
        post_code: Optional[str] = Field(None, description="Updated postal/ZIP code"),
        country: Optional[str] = Field(None, description="Updated country"),
        phone: Optional[str] = Field(None, description="Updated phone number"),
        website: Optional[str] = Field(None, description="Updated website URL"),
        domain: Optional[str] = Field(None, description="Updated email domain"),
        tax_id: Optional[str] = Field(None, description="Updated Tax ID / ABN"),
        notes: Optional[str] = Field(None, description="Updated notes")
    ) -> str:
        """Update an existing client in Gorelo. Only provided fields will be updated."""
        if not config.is_configured:
            return "Error: Gorelo not configured (missing GORELO_API_KEY)."
        try:
            payload = {}
            if name is not None:
                payload["name"] = name
            if address is not None:
                payload["address"] = address
            if city is not None:
                payload["city"] = city
            if state is not None:
                payload["state"] = state
            if post_code is not None:
                payload["postCode"] = post_code
            if country is not None:
                payload["country"] = country
            if phone is not None:
                payload["phone"] = phone
            if website is not None:
                payload["website"] = website
            if domain is not None:
                payload["domain"] = domain
            if tax_id is not None:
                payload["taxId"] = tax_id
            if notes is not None:
                payload["notes"] = notes
            if not payload:
                return "Error: No fields provided to update."
            async with httpx.AsyncClient() as client:
                response = await client.patch(f"{config.base_url}/v1/clients/{client_id}", json=payload, headers=config.headers())
                response.raise_for_status()
                data = response.json()
            return f"Client {client_id} updated successfully.\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False})
    async def gorelo_create_contact(
        first_name: str = Field(..., description="Contact first name"),
        last_name: str = Field(..., description="Contact last name"),
        email: str = Field(..., description="Contact email address"),
        client_id: Optional[int] = Field(None, description="Client ID to associate the contact with"),
        client_location_id: Optional[int] = Field(None, description="Client location ID to associate the contact with"),
        phone: Optional[str] = Field(None, description="Phone number"),
        mobile: Optional[str] = Field(None, description="Mobile phone number"),
        title: Optional[str] = Field(None, description="Job title")
    ) -> str:
        """Create a new contact in Gorelo."""
        if not config.is_configured:
            return "Error: Gorelo not configured (missing GORELO_API_KEY)."
        try:
            payload = {
                "firstName": first_name,
                "lastName": last_name,
                "email": email
            }
            if client_id is not None:
                payload["clientId"] = client_id
            if client_location_id is not None:
                payload["clientLocationId"] = client_location_id
            if phone:
                payload["phone"] = phone
            if mobile:
                payload["mobile"] = mobile
            if title:
                payload["title"] = title
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{config.base_url}/v1/contacts", json=payload, headers=config.headers())
                response.raise_for_status()
                data = response.json()
            cname = f"{data.get('firstName', first_name)} {data.get('lastName', last_name)}".strip()
            return f"Contact '{cname}' created successfully (ID: {data.get('id', 'N/A')}).\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False})
    async def gorelo_update_contact(
        contact_id: int = Field(..., description="Contact ID to update"),
        first_name: Optional[str] = Field(None, description="Updated first name"),
        last_name: Optional[str] = Field(None, description="Updated last name"),
        email: Optional[str] = Field(None, description="Updated email address"),
        client_id: Optional[int] = Field(None, description="Updated client ID association"),
        client_location_id: Optional[int] = Field(None, description="Updated client location ID association"),
        phone: Optional[str] = Field(None, description="Updated phone number"),
        mobile: Optional[str] = Field(None, description="Updated mobile phone number"),
        title: Optional[str] = Field(None, description="Updated job title")
    ) -> str:
        """Update an existing contact in Gorelo. Only provided fields will be updated."""
        if not config.is_configured:
            return "Error: Gorelo not configured (missing GORELO_API_KEY)."
        try:
            payload = {}
            if first_name is not None:
                payload["firstName"] = first_name
            if last_name is not None:
                payload["lastName"] = last_name
            if email is not None:
                payload["email"] = email
            if client_id is not None:
                payload["clientId"] = client_id
            if client_location_id is not None:
                payload["clientLocationId"] = client_location_id
            if phone is not None:
                payload["phone"] = phone
            if mobile is not None:
                payload["mobile"] = mobile
            if title is not None:
                payload["title"] = title
            if not payload:
                return "Error: No fields provided to update."
            async with httpx.AsyncClient() as client:
                response = await client.patch(f"{config.base_url}/v1/contacts/{contact_id}", json=payload, headers=config.headers())
                response.raise_for_status()
                data = response.json()
            cname = f"{data.get('firstName', '')} {data.get('lastName', '')}".strip() or "Unknown"
            return f"Contact '{cname}' (ID: {contact_id}) updated successfully.\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_ticket_statuses() -> str:
        """List all Gorelo ticket statuses."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/tickets/statuses", headers=config.headers())
                response.raise_for_status()
                statuses = response.json()
            if not statuses:
                return "No ticket statuses found."
            results = []
            for s in statuses:
                results.append(f"- **{s.get('name', 'Unknown')}** (ID: `{s.get('id', 'N/A')}`)")
            return f"## Gorelo Ticket Statuses ({len(statuses)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_ticket_tags() -> str:
        """List all Gorelo ticket tags."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/tickets/tags", headers=config.headers())
                response.raise_for_status()
                tags = response.json()
            if not tags:
                return "No ticket tags found."
            results = []
            for t in tags:
                results.append(f"- **{t.get('name', 'Unknown')}** (ID: `{t.get('id', 'N/A')}`)")
            return f"## Gorelo Ticket Tags ({len(tags)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_ticket_types() -> str:
        """List all Gorelo ticket types."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/tickets/types", headers=config.headers())
                response.raise_for_status()
                types_ = response.json()
            if not types_:
                return "No ticket types found."
            results = []
            for t in types_:
                results.append(f"- **{t.get('name', 'Unknown')}** (ID: `{t.get('id', 'N/A')}`)")
            return f"## Gorelo Ticket Types ({len(types_)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False})
    async def gorelo_create_ticket(
        subject: str = Field(..., description="Ticket subject/title"),
        client_id: int = Field(..., description="Client ID the ticket belongs to"),
        description: Optional[str] = Field(None, description="Ticket description/body"),
        contact_id: Optional[int] = Field(None, description="Contact ID to associate with the ticket"),
        client_location_id: Optional[int] = Field(None, description="Client location ID"),
        type_id: Optional[int] = Field(None, description="Ticket type ID (see gorelo_list_ticket_types)"),
        status_id: Optional[int] = Field(None, description="Ticket status ID (see gorelo_list_ticket_statuses)"),
        priority: Optional[str] = Field(None, description="Ticket priority (e.g., 'low', 'medium', 'high', 'critical')"),
        assignee_user_id: Optional[int] = Field(None, description="User ID to assign the ticket to"),
        tag_ids: Optional[str] = Field(None, description="Comma-separated list of ticket tag IDs"),
        extra_fields: Optional[str] = Field(None, description="JSON string of additional fields to include in the payload")
    ) -> str:
        """Create a new ticket in Gorelo."""
        if not config.is_configured:
            return "Error: Gorelo not configured (missing GORELO_API_KEY)."
        try:
            payload = {"subject": subject, "clientId": client_id}
            if description is not None:
                payload["description"] = description
            if contact_id is not None:
                payload["contactId"] = contact_id
            if client_location_id is not None:
                payload["clientLocationId"] = client_location_id
            if type_id is not None:
                payload["typeId"] = type_id
            if status_id is not None:
                payload["statusId"] = status_id
            if priority is not None:
                payload["priority"] = priority
            if assignee_user_id is not None:
                payload["assigneeUserId"] = assignee_user_id
            if tag_ids:
                parsed_tags = []
                for t in tag_ids.split(","):
                    t = t.strip()
                    if not t:
                        continue
                    try:
                        parsed_tags.append(int(t))
                    except ValueError:
                        parsed_tags.append(t)
                payload["tagIds"] = parsed_tags
            if extra_fields:
                try:
                    extra = json.loads(extra_fields)
                    if isinstance(extra, dict):
                        payload.update(extra)
                    else:
                        return "Error: extra_fields must be a JSON object."
                except json.JSONDecodeError as e:
                    return f"Error: extra_fields is not valid JSON: {e}"
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{config.base_url}/v1/tickets", json=payload, headers=config.headers())
                response.raise_for_status()
                data = response.json() if response.content else {}
            ticket_id = data.get("id", "N/A") if isinstance(data, dict) else "N/A"
            return f"Ticket '{subject}' created successfully (ID: {ticket_id}).\n\n```json\n{json.dumps(data, indent=2)}\n```"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_organization_users() -> str:
        """List all users in the Gorelo organization."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/organization/users", headers=config.headers())
                response.raise_for_status()
                users = response.json()
            if not users:
                return "No organization users found."
            results = []
            for u in users[:100]:
                name = f"{u.get('firstName', '')} {u.get('lastName', '')}".strip() or u.get('name') or u.get('email', 'Unknown')
                email = u.get('email', 'N/A')
                results.append(f"- **{name}** ({email}) - ID: `{u.get('id', 'N/A')}`")
            return f"## Gorelo Organization Users ({len(users)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def gorelo_list_organization_groups() -> str:
        """List all groups in the Gorelo organization."""
        if not config.is_configured:
            return "Error: Gorelo not configured."
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config.base_url}/v1/organization/groups", headers=config.headers())
                response.raise_for_status()
                groups = response.json()
            if not groups:
                return "No organization groups found."
            results = []
            for g in groups:
                results.append(f"- **{g.get('name', 'Unknown')}** (ID: `{g.get('id', 'N/A')}`)")
            return f"## Gorelo Organization Groups ({len(groups)} total)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"
