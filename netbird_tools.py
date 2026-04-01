"""
NetBird Integration Tools for Crowd IT MCP Server

This module provides NetBird network management capabilities via the NetBird API.

Capabilities:
- Accounts: get and update account settings
- Users: list, create, update, delete, invite, approve/reject
- Tokens: list, create, get, delete (PATs)
- Peers: list, get, update, delete, accessible peers
- Setup Keys: list, create, get, update, delete
- Groups: list, create, get, update, delete
- Policies: list, create, get, update, delete
- Posture Checks: list, create, get, update, delete
- Routes: list, create, get, update, delete
- DNS: nameservers (list, create, get, update, delete), settings
- Networks: list, create, get, update, delete
- Network Resources: list, create, get, update, delete
- Network Routers: list, create, get, update, delete
- Events: audit, network traffic, proxy logs
- Geo Locations: countries and cities

Authentication: Token-based (Authorization: Token <TOKEN>)

Environment Variables:
    NETBIRD_API_TOKEN: API token from NetBird dashboard
    NETBIRD_API_URL: API base URL (defaults to https://api.netbird.io)
"""

import os
import json
import logging
from typing import Optional

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)


class NetBirdConfig:
    def __init__(self):
        self.api_token = os.getenv("NETBIRD_API_TOKEN", "")
        self.base_url = os.getenv("NETBIRD_API_URL", "https://api.netbird.io")
        self._secrets_loaded = False

    def _load_secrets(self) -> None:
        if self._secrets_loaded:
            return
        if not self.api_token:
            try:
                from app.core.config import get_secret_sync
                self.api_token = get_secret_sync("NETBIRD_API_TOKEN") or ""
            except Exception:
                pass
        self._secrets_loaded = True

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return bool(self.api_token)

    def headers(self):
        self._load_secrets()
        return {
            "Authorization": f"Token {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }


async def _nb_get(config: 'NetBirdConfig', path: str, params: dict = None) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(f"{config.base_url}{path}", headers=config.headers(), params=params)


async def _nb_post(config: 'NetBirdConfig', path: str, json_data: dict = None) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(f"{config.base_url}{path}", headers=config.headers(), json=json_data)


async def _nb_put(config: 'NetBirdConfig', path: str, json_data: dict) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.put(f"{config.base_url}{path}", headers=config.headers(), json=json_data)


async def _nb_delete(config: 'NetBirdConfig', path: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.delete(f"{config.base_url}{path}", headers=config.headers())


def _check_nb_response(response: httpx.Response) -> Optional[str]:
    if response.status_code >= 400:
        try:
            err = response.json()
            msg = err.get("message", "") or err.get("error", "") or str(err)
            return f"NetBird API Error: {response.status_code} - {msg}"
        except Exception:
            return f"NetBird API Error: {response.status_code} - {response.text}"
    return None


def register_netbird_tools(mcp, config: 'NetBirdConfig') -> None:
    """Register all NetBird tools with the MCP server."""

    NOT_CONFIGURED = "Error: NetBird not configured (missing NETBIRD_API_TOKEN)."

    # =========================================================================
    # ACCOUNTS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_accounts() -> str:
        """Get NetBird account details and settings."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/accounts")
            error = _check_nb_response(response)
            if error:
                return error
            accounts = response.json()
            if not accounts:
                return "No accounts found."
            results = []
            for a in accounts:
                s = a.get("settings", {})
                results.append(f"""**Account ID:** `{a.get('id', 'N/A')}`
**DNS Domain:** {s.get('dns_domain', 'N/A')}
**Peer Login Expiration:** {'Enabled' if s.get('peer_login_expiration_enabled') else 'Disabled'} ({s.get('peer_login_expiration', 0)}s)
**Peer Inactivity Expiration:** {'Enabled' if s.get('peer_inactivity_expiration_enabled') else 'Disabled'} ({s.get('peer_inactivity_expiration', 0)}s)
**Groups Propagation:** {'Enabled' if s.get('groups_propagation_enabled') else 'Disabled'}
**JWT Groups:** {'Enabled' if s.get('jwt_groups_enabled') else 'Disabled'}
**Routing Peer DNS Resolution:** {'Enabled' if s.get('routing_peer_dns_resolution_enabled') else 'Disabled'}""")
            return "## NetBird Accounts\n\n" + "\n\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_account(
        account_id: str = Field(..., description="Account ID"),
        settings: str = Field(..., description='JSON object of settings to update, e.g. {"peer_login_expiration_enabled": true, "peer_login_expiration": 86400, "dns_domain": "netbird.cloud"}'),
    ) -> str:
        """Update NetBird account settings."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            settings_data = json.loads(settings)
            response = await _nb_put(config, f"/api/accounts/{account_id}", {"settings": settings_data})
            error = _check_nb_response(response)
            if error:
                return error
            return f"Account `{account_id}` settings updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in settings."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # USERS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_users(
        service_user: Optional[bool] = Field(None, description="Filter: true for service users, false for regular users"),
    ) -> str:
        """List all NetBird users."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if service_user is not None:
                params["service_user"] = str(service_user).lower()
            response = await _nb_get(config, "/api/users", params)
            error = _check_nb_response(response)
            if error:
                return error
            users = response.json()
            if not users:
                return "No users found."
            results = []
            for u in users:
                groups = [g.get("name", "") for g in u.get("auto_groups", []) or []]
                results.append(f"- **{u.get('name', 'N/A')}** | Email: {u.get('email', 'N/A')} | Role: {u.get('role', 'N/A')} | Status: {u.get('status', 'N/A')} | Service: {u.get('is_service_user', False)} | ID: `{u.get('id', 'N/A')}`")
            return f"## NetBird Users ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_current_user() -> str:
        """Get the current authenticated NetBird user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/users/current")
            error = _check_nb_response(response)
            if error:
                return error
            u = response.json()
            return f"""## Current User

**Name:** {u.get('name', 'N/A')}
**Email:** {u.get('email', 'N/A')}
**Role:** {u.get('role', 'N/A')}
**Status:** {u.get('status', 'N/A')}
**ID:** `{u.get('id', 'N/A')}`
**Is Service User:** {u.get('is_service_user', False)}
**Is Blocked:** {u.get('is_blocked', False)}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_user(
        role: str = Field(..., description="Role: 'admin', 'user', or 'owner'"),
        auto_groups: str = Field(..., description='JSON array of group IDs: ["group-id-1"]'),
        is_service_user: bool = Field(False, description="Create as service user (no email needed)"),
        email: Optional[str] = Field(None, description="Email (required for regular users)"),
        name: Optional[str] = Field(None, description="User name"),
    ) -> str:
        """Create a NetBird user or service account."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            groups = json.loads(auto_groups)
            user_data = {
                "role": role,
                "auto_groups": groups,
                "is_service_user": is_service_user,
            }
            if email:
                user_data["email"] = email
            if name:
                user_data["name"] = name
            response = await _nb_post(config, "/api/users", user_data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"User created: **{created.get('name', 'N/A')}** (ID: `{created.get('id', 'N/A')}`) | Role: {created.get('role', '')}"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in auto_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_user(
        user_id: str = Field(..., description="User ID"),
        role: str = Field(..., description="Role: 'admin', 'user', or 'owner'"),
        auto_groups: str = Field(..., description='JSON array of group IDs'),
        is_blocked: bool = Field(False, description="Block the user"),
    ) -> str:
        """Update a NetBird user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            groups = json.loads(auto_groups)
            response = await _nb_put(config, f"/api/users/{user_id}", {
                "role": role,
                "auto_groups": groups,
                "is_blocked": is_blocked,
            })
            error = _check_nb_response(response)
            if error:
                return error
            return f"User `{user_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in auto_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_user(
        user_id: str = Field(..., description="User ID"),
    ) -> str:
        """Delete a NetBird user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/users/{user_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"User `{user_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def netbird_invite_user(
        user_id: str = Field(..., description="User ID to resend invitation to"),
    ) -> str:
        """Resend invitation to a NetBird user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_post(config, f"/api/users/{user_id}/invite")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Invitation resent to user `{user_id}`."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_approve_user(
        user_id: str = Field(..., description="User ID to approve"),
    ) -> str:
        """Approve a pending NetBird user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_post(config, f"/api/users/{user_id}/approve")
            error = _check_nb_response(response)
            if error:
                return error
            return f"User `{user_id}` approved."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_reject_user(
        user_id: str = Field(..., description="User ID to reject"),
    ) -> str:
        """Reject a pending NetBird user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/users/{user_id}/reject")
            error = _check_nb_response(response)
            if error:
                return error
            return f"User `{user_id}` rejected."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # TOKENS (Personal Access Tokens)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_tokens(
        user_id: str = Field(..., description="User ID"),
    ) -> str:
        """List all personal access tokens for a user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/users/{user_id}/tokens")
            error = _check_nb_response(response)
            if error:
                return error
            tokens = response.json()
            if not tokens:
                return "No tokens found."
            results = []
            for t in tokens:
                results.append(f"- **{t.get('name', 'N/A')}** | Created: {t.get('created_at', 'N/A')[:10]} | Expires: {t.get('expiration_date', 'N/A')[:10]} | Last Used: {t.get('last_used', 'Never')[:10] if t.get('last_used') else 'Never'} | ID: `{t.get('id', 'N/A')}`")
            return f"## Tokens ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_token(
        user_id: str = Field(..., description="User ID"),
        name: str = Field(..., description="Token name"),
        expires_in: int = Field(365, description="Expiry in days (1-365)"),
    ) -> str:
        """Create a personal access token for a user."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_post(config, f"/api/users/{user_id}/tokens", {
                "name": name,
                "expires_in": expires_in,
            })
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Token created: **{name}**\n\n**Token value (save now, shown once):** `{created.get('plain_token', 'N/A')}`\n\nExpires: {created.get('expiration_date', 'N/A')[:10]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_token(
        user_id: str = Field(..., description="User ID"),
        token_id: str = Field(..., description="Token ID"),
    ) -> str:
        """Get details of a specific token."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/users/{user_id}/tokens/{token_id}")
            error = _check_nb_response(response)
            if error:
                return error
            t = response.json()
            return f"""## Token: {t.get('name', 'N/A')}

**ID:** `{t.get('id', 'N/A')}`
**Created:** {t.get('created_at', 'N/A')[:10]}
**Expires:** {t.get('expiration_date', 'N/A')[:10]}
**Last Used:** {t.get('last_used', 'Never')[:10] if t.get('last_used') else 'Never'}
**Created By:** {t.get('created_by', 'N/A')}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_token(
        user_id: str = Field(..., description="User ID"),
        token_id: str = Field(..., description="Token ID"),
    ) -> str:
        """Delete a personal access token."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/users/{user_id}/tokens/{token_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Token `{token_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # PEERS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_peers(
        name: Optional[str] = Field(None, description="Filter by peer name"),
        ip: Optional[str] = Field(None, description="Filter by IP address"),
    ) -> str:
        """List all NetBird peers (devices)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if name:
                params["name"] = name
            if ip:
                params["ip"] = ip
            response = await _nb_get(config, "/api/peers", params)
            error = _check_nb_response(response)
            if error:
                return error
            peers = response.json()
            if not peers:
                return "No peers found."
            results = []
            for p in peers:
                groups = ", ".join(g.get("name", "") for g in (p.get("groups", []) or []))
                results.append(f"- **{p.get('name', 'N/A')}** | IP: {p.get('ip', 'N/A')} | OS: {p.get('os', 'N/A')} | Connected: {p.get('connected', False)} | Last Seen: {(p.get('last_seen', '') or '')[:19]} | Groups: {groups or 'None'} | ID: `{p.get('id', 'N/A')}`")
            return f"## NetBird Peers ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_peer(
        peer_id: str = Field(..., description="Peer ID"),
    ) -> str:
        """Get full details for a NetBird peer."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/peers/{peer_id}")
            error = _check_nb_response(response)
            if error:
                return error
            p = response.json()
            groups = ", ".join(g.get("name", "") for g in (p.get("groups", []) or []))
            return f"""## Peer: {p.get('name', 'N/A')}

**ID:** `{p.get('id', 'N/A')}`
**IP:** {p.get('ip', 'N/A')}
**DNS Label:** {p.get('dns_label', 'N/A')}
**Connected:** {p.get('connected', False)}
**Last Seen:** {(p.get('last_seen', '') or '')[:19]}
**OS:** {p.get('os', 'N/A')}
**Version:** {p.get('version', 'N/A')}
**Hostname:** {p.get('hostname', 'N/A')}
**UI Version:** {p.get('ui_version', 'N/A')}
**SSH Enabled:** {p.get('ssh_enabled', False)}
**Login Expiration Enabled:** {p.get('login_expiration_enabled', False)}
**Login Expired:** {p.get('login_expired', False)}
**Inactivity Expiration Enabled:** {p.get('inactivity_expiration_enabled', False)}
**Approval Required:** {p.get('approval_required', False)}
**Country Code:** {p.get('country_code', 'N/A')}
**City Name:** {p.get('city_name', 'N/A')}
**Groups:** {groups or 'None'}
**User:** {p.get('user', {}).get('name', 'N/A')} ({p.get('user', {}).get('email', 'N/A')})"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_peer(
        peer_id: str = Field(..., description="Peer ID"),
        name: str = Field(..., description="Peer name"),
        ssh_enabled: bool = Field(False, description="Enable SSH"),
        login_expiration_enabled: bool = Field(True, description="Enable login expiration"),
        inactivity_expiration_enabled: bool = Field(False, description="Enable inactivity expiration"),
        approval_required: Optional[bool] = Field(None, description="Require approval (cloud only)"),
    ) -> str:
        """Update a NetBird peer."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {
                "name": name,
                "ssh_enabled": ssh_enabled,
                "login_expiration_enabled": login_expiration_enabled,
                "inactivity_expiration_enabled": inactivity_expiration_enabled,
            }
            if approval_required is not None:
                data["approval_required"] = approval_required
            response = await _nb_put(config, f"/api/peers/{peer_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"Peer `{peer_id}` updated."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_peer(
        peer_id: str = Field(..., description="Peer ID"),
    ) -> str:
        """Delete a NetBird peer."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/peers/{peer_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Peer `{peer_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_accessible_peers(
        peer_id: str = Field(..., description="Peer ID"),
    ) -> str:
        """List peers accessible by a given peer."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/peers/{peer_id}/accessible-peers")
            error = _check_nb_response(response)
            if error:
                return error
            peers = response.json()
            if not peers:
                return f"No accessible peers found for `{peer_id}`."
            results = []
            for p in peers:
                results.append(f"- **{p.get('name', 'N/A')}** | IP: {p.get('ip', 'N/A')} | Connected: {p.get('connected', False)} | ID: `{p.get('id', 'N/A')}`")
            return f"## Accessible Peers ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # SETUP KEYS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_setup_keys() -> str:
        """List all NetBird setup keys."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/setup-keys")
            error = _check_nb_response(response)
            if error:
                return error
            keys = response.json()
            if not keys:
                return "No setup keys found."
            results = []
            for k in keys:
                results.append(f"- **{k.get('name', 'N/A')}** | Type: {k.get('type', 'N/A')} | Valid: {k.get('valid', False)} | Revoked: {k.get('revoked', False)} | Used: {k.get('used_times', 0)}/{k.get('usage_limit', 0)} | Ephemeral: {k.get('ephemeral', False)} | Expires: {(k.get('expires', '') or '')[:10]} | ID: `{k.get('id', 'N/A')}`")
            return f"## Setup Keys ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_setup_key(
        name: str = Field(..., description="Key name"),
        key_type: str = Field("reusable", description="Type: 'one-off' or 'reusable'"),
        expires_in: int = Field(86400, description="Expiry in seconds (86400-31536000)"),
        auto_groups: str = Field("[]", description='JSON array of group IDs'),
        usage_limit: int = Field(0, description="Usage limit (0=unlimited)"),
        ephemeral: bool = Field(False, description="Create ephemeral peers"),
    ) -> str:
        """Create a NetBird setup key for peer enrollment."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            groups = json.loads(auto_groups)
            response = await _nb_post(config, "/api/setup-keys", {
                "name": name,
                "type": key_type,
                "expires_in": expires_in,
                "auto_groups": groups,
                "usage_limit": usage_limit,
                "ephemeral": ephemeral,
            })
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Setup key created: **{name}**\n\n**Key:** `{created.get('key', 'N/A')}`\n**Type:** {key_type}\n**ID:** `{created.get('id', 'N/A')}`"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in auto_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_setup_key(
        key_id: str = Field(..., description="Setup Key ID"),
    ) -> str:
        """Get details of a setup key."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/setup-keys/{key_id}")
            error = _check_nb_response(response)
            if error:
                return error
            k = response.json()
            groups = ", ".join(g.get("name", "") for g in (k.get("auto_groups", []) or []))
            return f"""## Setup Key: {k.get('name', 'N/A')}

**ID:** `{k.get('id', 'N/A')}`
**Type:** {k.get('type', 'N/A')}
**Valid:** {k.get('valid', False)}
**Revoked:** {k.get('revoked', False)}
**Ephemeral:** {k.get('ephemeral', False)}
**Used:** {k.get('used_times', 0)} / {k.get('usage_limit', 0)}
**Expires:** {(k.get('expires', '') or '')[:19]}
**Auto Groups:** {groups or 'None'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_setup_key(
        key_id: str = Field(..., description="Setup Key ID"),
        revoked: bool = Field(False, description="Revoke the key"),
        auto_groups: str = Field(..., description='JSON array of group IDs'),
    ) -> str:
        """Update a setup key (revoke or change groups)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            groups = json.loads(auto_groups)
            response = await _nb_put(config, f"/api/setup-keys/{key_id}", {
                "revoked": revoked,
                "auto_groups": groups,
            })
            error = _check_nb_response(response)
            if error:
                return error
            return f"Setup key `{key_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in auto_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_setup_key(
        key_id: str = Field(..., description="Setup Key ID"),
    ) -> str:
        """Delete a setup key."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/setup-keys/{key_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Setup key `{key_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # GROUPS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_groups(
        name: Optional[str] = Field(None, description="Filter by exact group name"),
    ) -> str:
        """List all NetBird groups."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {}
            if name:
                params["name"] = name
            response = await _nb_get(config, "/api/groups", params)
            error = _check_nb_response(response)
            if error:
                return error
            groups = response.json()
            if not groups:
                return "No groups found."
            results = []
            for g in groups:
                peer_count = len(g.get("peers", []) or [])
                results.append(f"- **{g.get('name', 'N/A')}** | Peers: {peer_count} | ID: `{g.get('id', 'N/A')}`")
            return f"## NetBird Groups ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_group(
        group_id: str = Field(..., description="Group ID"),
    ) -> str:
        """Get group details including member peers."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/groups/{group_id}")
            error = _check_nb_response(response)
            if error:
                return error
            g = response.json()
            peers = g.get("peers", []) or []
            peer_lines = [f"  - {p.get('name', 'N/A')} (`{p.get('id', 'N/A')}`)" for p in peers]
            resources = g.get("resources", []) or []
            resource_lines = [f"  - {r.get('type', 'N/A')}: `{r.get('id', 'N/A')}`" for r in resources]
            return f"""## Group: {g.get('name', 'N/A')}

**ID:** `{g.get('id', 'N/A')}`
**Peers ({len(peers)}):**
{chr(10).join(peer_lines) if peer_lines else '  None'}

**Resources ({len(resources)}):**
{chr(10).join(resource_lines) if resource_lines else '  None'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_group(
        name: str = Field(..., description="Group name"),
        peers: Optional[str] = Field(None, description='JSON array of peer IDs: ["peer-id-1"]'),
    ) -> str:
        """Create a NetBird group."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {"name": name}
            if peers:
                data["peers"] = json.loads(peers)
            response = await _nb_post(config, "/api/groups", data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Group created: **{name}** (ID: `{created.get('id', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in peers."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_group(
        group_id: str = Field(..., description="Group ID"),
        name: str = Field(..., description="Group name"),
        peers: Optional[str] = Field(None, description='JSON array of peer IDs'),
    ) -> str:
        """Update a NetBird group (replaces all settings)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {"name": name}
            if peers:
                data["peers"] = json.loads(peers)
            response = await _nb_put(config, f"/api/groups/{group_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"Group `{group_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in peers."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_group(
        group_id: str = Field(..., description="Group ID"),
    ) -> str:
        """Delete a NetBird group."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/groups/{group_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Group `{group_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # POLICIES (ACL)
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_policies() -> str:
        """List all NetBird access control policies."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/policies")
            error = _check_nb_response(response)
            if error:
                return error
            policies = response.json()
            if not policies:
                return "No policies found."
            results = []
            for p in policies:
                rules_count = len(p.get("rules", []) or [])
                results.append(f"- **{p.get('name', 'N/A')}** | Enabled: {p.get('enabled', False)} | Rules: {rules_count} | ID: `{p.get('id', 'N/A')}`")
            return f"## Policies ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_policy(
        policy_id: str = Field(..., description="Policy ID"),
    ) -> str:
        """Get full policy details including rules."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/policies/{policy_id}")
            error = _check_nb_response(response)
            if error:
                return error
            p = response.json()
            rules = p.get("rules", []) or []
            rule_lines = []
            for r in rules:
                sources = [s.get("name", s) if isinstance(s, dict) else s for s in (r.get("sources", []) or [])]
                destinations = [d.get("name", d) if isinstance(d, dict) else d for d in (r.get("destinations", []) or [])]
                rule_lines.append(f"  - **{r.get('name', 'N/A')}** | Action: {r.get('action', 'N/A')} | Protocol: {r.get('protocol', 'all')} | Bidirectional: {r.get('bidirectional', False)} | Enabled: {r.get('enabled', False)}\n    Sources: {', '.join(str(s) for s in sources) or 'None'} -> Destinations: {', '.join(str(d) for d in destinations) or 'None'}")
            return f"""## Policy: {p.get('name', 'N/A')}

**ID:** `{p.get('id', 'N/A')}`
**Description:** {p.get('description', 'N/A')}
**Enabled:** {p.get('enabled', False)}

**Rules ({len(rules)}):**
{chr(10).join(rule_lines) if rule_lines else '  None'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_policy(
        name: str = Field(..., description="Policy name"),
        enabled: bool = Field(True, description="Enable the policy"),
        rules: str = Field(..., description='JSON array of rules: [{"name": "rule1", "enabled": true, "action": "accept", "bidirectional": true, "protocol": "all", "sources": ["group-id"], "destinations": ["group-id"]}]'),
        description: Optional[str] = Field(None, description="Policy description"),
    ) -> str:
        """Create a NetBird access control policy."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            rules_data = json.loads(rules)
            data = {"name": name, "enabled": enabled, "rules": rules_data}
            if description:
                data["description"] = description
            response = await _nb_post(config, "/api/policies", data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Policy created: **{name}** (ID: `{created.get('id', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in rules."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_policy(
        policy_id: str = Field(..., description="Policy ID"),
        name: str = Field(..., description="Policy name"),
        enabled: bool = Field(True, description="Enable the policy"),
        rules: str = Field(..., description='JSON array of rules'),
        description: Optional[str] = Field(None, description="Policy description"),
    ) -> str:
        """Update a NetBird policy (replaces all settings)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            rules_data = json.loads(rules)
            data = {"name": name, "enabled": enabled, "rules": rules_data}
            if description:
                data["description"] = description
            response = await _nb_put(config, f"/api/policies/{policy_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"Policy `{policy_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in rules."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_policy(
        policy_id: str = Field(..., description="Policy ID"),
    ) -> str:
        """Delete a NetBird policy."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/policies/{policy_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Policy `{policy_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # POSTURE CHECKS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_posture_checks() -> str:
        """List all NetBird posture checks."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/posture-checks")
            error = _check_nb_response(response)
            if error:
                return error
            checks = response.json()
            if not checks:
                return "No posture checks found."
            results = []
            for c in checks:
                check_types = list((c.get("checks", {}) or {}).keys())
                results.append(f"- **{c.get('name', 'N/A')}** | Types: {', '.join(check_types) or 'None'} | ID: `{c.get('id', 'N/A')}`")
            return f"## Posture Checks ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_posture_check(
        posture_check_id: str = Field(..., description="Posture Check ID"),
    ) -> str:
        """Get posture check details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/posture-checks/{posture_check_id}")
            error = _check_nb_response(response)
            if error:
                return error
            c = response.json()
            checks = c.get("checks", {}) or {}
            check_lines = []
            for check_type, check_val in checks.items():
                check_lines.append(f"  - **{check_type}:** {json.dumps(check_val, indent=2)}")
            return f"""## Posture Check: {c.get('name', 'N/A')}

**ID:** `{c.get('id', 'N/A')}`
**Description:** {c.get('description', 'N/A')}

**Checks:**
{chr(10).join(check_lines) if check_lines else '  None'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_posture_check(
        name: str = Field(..., description="Posture check name"),
        description: str = Field("", description="Description"),
        checks: str = Field(..., description='JSON object of checks, e.g. {"nb_version_check": {"min_version": "0.25.0"}, "geo_location_check": {"locations": [{"country_code": "AU"}], "action": "allow"}}'),
    ) -> str:
        """Create a NetBird posture check."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            checks_data = json.loads(checks)
            response = await _nb_post(config, "/api/posture-checks", {
                "name": name,
                "description": description,
                "checks": checks_data,
            })
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Posture check created: **{name}** (ID: `{created.get('id', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in checks."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_posture_check(
        posture_check_id: str = Field(..., description="Posture Check ID"),
        name: str = Field(..., description="Posture check name"),
        description: str = Field("", description="Description"),
        checks: str = Field(..., description='JSON object of checks'),
    ) -> str:
        """Update a NetBird posture check (replaces all settings)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            checks_data = json.loads(checks)
            response = await _nb_put(config, f"/api/posture-checks/{posture_check_id}", {
                "name": name,
                "description": description,
                "checks": checks_data,
            })
            error = _check_nb_response(response)
            if error:
                return error
            return f"Posture check `{posture_check_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in checks."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_posture_check(
        posture_check_id: str = Field(..., description="Posture Check ID"),
    ) -> str:
        """Delete a NetBird posture check."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/posture-checks/{posture_check_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Posture check `{posture_check_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ROUTES
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_routes() -> str:
        """List all NetBird network routes."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/routes")
            error = _check_nb_response(response)
            if error:
                return error
            routes = response.json()
            if not routes:
                return "No routes found."
            results = []
            for r in routes:
                target = r.get("network", '') or ', '.join(r.get("domains", []) or []) or 'N/A'
                results.append(f"- **{r.get('network_id', 'N/A')}** | Target: {target} | Enabled: {r.get('enabled', False)} | Masquerade: {r.get('masquerade', False)} | Metric: {r.get('metric', 'N/A')} | ID: `{r.get('id', 'N/A')}`")
            return f"## Routes ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_route(
        route_id: str = Field(..., description="Route ID"),
    ) -> str:
        """Get route details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/routes/{route_id}")
            error = _check_nb_response(response)
            if error:
                return error
            r = response.json()
            groups = ", ".join(g.get("name", g) if isinstance(g, dict) else str(g) for g in (r.get("groups", []) or []))
            peer_groups = ", ".join(str(pg) for pg in (r.get("peer_groups", []) or []))
            return f"""## Route: {r.get('network_id', 'N/A')}

**ID:** `{r.get('id', 'N/A')}`
**Description:** {r.get('description', 'N/A')}
**Network:** {r.get('network', 'N/A')}
**Domains:** {', '.join(r.get('domains', []) or []) or 'None'}
**Enabled:** {r.get('enabled', False)}
**Peer:** {r.get('peer', 'N/A')}
**Peer Groups:** {peer_groups or 'None'}
**Metric:** {r.get('metric', 'N/A')}
**Masquerade:** {r.get('masquerade', False)}
**Keep Route:** {r.get('keep_route', False)}
**Distribution Groups:** {groups or 'None'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_route(
        network_id: str = Field(..., description="Route identifier (1-40 chars)"),
        description: str = Field("", description="Route description"),
        enabled: bool = Field(True, description="Enable the route"),
        metric: int = Field(9999, description="Route metric (1-9999)"),
        masquerade: bool = Field(True, description="Enable masquerade"),
        groups: str = Field(..., description='JSON array of distribution group IDs'),
        keep_route: bool = Field(True, description="Keep route active when peer disconnects"),
        network: Optional[str] = Field(None, description="Network CIDR (e.g., '10.0.0.0/24') - use this OR domains"),
        domains: Optional[str] = Field(None, description='JSON array of domains (max 32) - use this OR network'),
        peer: Optional[str] = Field(None, description="Routing peer ID - use this OR peer_groups"),
        peer_groups: Optional[str] = Field(None, description='JSON array of peer group IDs - use this OR peer'),
    ) -> str:
        """Create a NetBird network route."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            groups_data = json.loads(groups)
            data = {
                "network_id": network_id,
                "description": description,
                "enabled": enabled,
                "metric": metric,
                "masquerade": masquerade,
                "groups": groups_data,
                "keep_route": keep_route,
            }
            if network:
                data["network"] = network
            if domains:
                data["domains"] = json.loads(domains)
            if peer:
                data["peer"] = peer
            if peer_groups:
                data["peer_groups"] = json.loads(peer_groups)

            response = await _nb_post(config, "/api/routes", data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Route created: **{network_id}** (ID: `{created.get('id', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in groups, domains, or peer_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_route(
        route_id: str = Field(..., description="Route ID"),
        network_id: str = Field(..., description="Route identifier"),
        description: str = Field("", description="Route description"),
        enabled: bool = Field(True, description="Enable the route"),
        metric: int = Field(9999, description="Route metric (1-9999)"),
        masquerade: bool = Field(True, description="Enable masquerade"),
        groups: str = Field(..., description='JSON array of distribution group IDs'),
        keep_route: bool = Field(True, description="Keep route active"),
        network: Optional[str] = Field(None, description="Network CIDR"),
        domains: Optional[str] = Field(None, description='JSON array of domains'),
        peer: Optional[str] = Field(None, description="Routing peer ID"),
        peer_groups: Optional[str] = Field(None, description='JSON array of peer group IDs'),
    ) -> str:
        """Update a NetBird route (replaces all settings)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            groups_data = json.loads(groups)
            data = {
                "network_id": network_id,
                "description": description,
                "enabled": enabled,
                "metric": metric,
                "masquerade": masquerade,
                "groups": groups_data,
                "keep_route": keep_route,
            }
            if network:
                data["network"] = network
            if domains:
                data["domains"] = json.loads(domains)
            if peer:
                data["peer"] = peer
            if peer_groups:
                data["peer_groups"] = json.loads(peer_groups)

            response = await _nb_put(config, f"/api/routes/{route_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"Route `{route_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in groups, domains, or peer_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_route(
        route_id: str = Field(..., description="Route ID"),
    ) -> str:
        """Delete a NetBird route."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/routes/{route_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Route `{route_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # DNS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_dns_nameservers() -> str:
        """List all DNS nameserver groups."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/dns/nameservers")
            error = _check_nb_response(response)
            if error:
                return error
            nsgroups = response.json()
            if not nsgroups:
                return "No DNS nameserver groups found."
            results = []
            for ns in nsgroups:
                servers = ", ".join(f"{s.get('ip', '')}:{s.get('port', 53)}" for s in (ns.get("nameservers", []) or []))
                domains = ", ".join(ns.get("domains", []) or []) or "All"
                results.append(f"- **{ns.get('name', 'N/A')}** | Servers: {servers} | Domains: {domains} | Primary: {ns.get('primary', False)} | Enabled: {ns.get('enabled', False)} | ID: `{ns.get('id', 'N/A')}`")
            return f"## DNS Nameserver Groups ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_dns_nameserver(
        nsgroup_id: str = Field(..., description="Nameserver Group ID"),
    ) -> str:
        """Get DNS nameserver group details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/dns/nameservers/{nsgroup_id}")
            error = _check_nb_response(response)
            if error:
                return error
            ns = response.json()
            servers = []
            for s in ns.get("nameservers", []) or []:
                servers.append(f"  - {s.get('ip', 'N/A')}:{s.get('port', 53)} ({s.get('ns_type', 'udp')})")
            groups = ", ".join(str(g) for g in (ns.get("groups", []) or []))
            return f"""## DNS Nameserver Group: {ns.get('name', 'N/A')}

**ID:** `{ns.get('id', 'N/A')}`
**Description:** {ns.get('description', 'N/A')}
**Enabled:** {ns.get('enabled', False)}
**Primary:** {ns.get('primary', False)}
**Domains:** {', '.join(ns.get('domains', []) or []) or 'All'}
**Search Domains Enabled:** {ns.get('search_domains_enabled', False)}
**Distribution Groups:** {groups or 'None'}

**Nameservers:**
{chr(10).join(servers) if servers else '  None'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_dns_nameserver(
        name: str = Field(..., description="Nameserver group name"),
        nameservers: str = Field(..., description='JSON array: [{"ip": "8.8.8.8", "ns_type": "udp", "port": 53}]'),
        enabled: bool = Field(True, description="Enable the nameserver group"),
        groups: str = Field(..., description='JSON array of distribution group IDs'),
        primary: bool = Field(False, description="Set as primary nameserver"),
        domains: str = Field("[]", description='JSON array of domains (empty = all)'),
        search_domains_enabled: bool = Field(False, description="Enable search domains"),
        description: str = Field("", description="Description"),
    ) -> str:
        """Create a DNS nameserver group."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {
                "name": name,
                "description": description,
                "nameservers": json.loads(nameservers),
                "enabled": enabled,
                "groups": json.loads(groups),
                "primary": primary,
                "domains": json.loads(domains),
                "search_domains_enabled": search_domains_enabled,
            }
            response = await _nb_post(config, "/api/dns/nameservers", data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"DNS nameserver group created: **{name}** (ID: `{created.get('id', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in nameservers, groups, or domains."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_dns_nameserver(
        nsgroup_id: str = Field(..., description="Nameserver Group ID"),
        name: str = Field(..., description="Nameserver group name"),
        nameservers: str = Field(..., description='JSON array of nameservers'),
        enabled: bool = Field(True, description="Enable"),
        groups: str = Field(..., description='JSON array of group IDs'),
        primary: bool = Field(False, description="Primary"),
        domains: str = Field("[]", description='JSON array of domains'),
        search_domains_enabled: bool = Field(False, description="Enable search domains"),
        description: str = Field("", description="Description"),
    ) -> str:
        """Update a DNS nameserver group (replaces all settings)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {
                "name": name,
                "description": description,
                "nameservers": json.loads(nameservers),
                "enabled": enabled,
                "groups": json.loads(groups),
                "primary": primary,
                "domains": json.loads(domains),
                "search_domains_enabled": search_domains_enabled,
            }
            response = await _nb_put(config, f"/api/dns/nameservers/{nsgroup_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"DNS nameserver group `{nsgroup_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_dns_nameserver(
        nsgroup_id: str = Field(..., description="Nameserver Group ID"),
    ) -> str:
        """Delete a DNS nameserver group."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/dns/nameservers/{nsgroup_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"DNS nameserver group `{nsgroup_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_dns_settings() -> str:
        """Get DNS settings (disabled management groups)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/dns/settings")
            error = _check_nb_response(response)
            if error:
                return error
            settings = response.json()
            groups = settings.get("disabled_management_groups", []) or []
            return f"## DNS Settings\n\n**Disabled Management Groups:** {', '.join(str(g) for g in groups) or 'None'}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_dns_settings(
        disabled_management_groups: str = Field(..., description='JSON array of group IDs to disable DNS management for'),
    ) -> str:
        """Update DNS settings."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            groups = json.loads(disabled_management_groups)
            response = await _nb_put(config, "/api/dns/settings", {
                "disabled_management_groups": groups,
            })
            error = _check_nb_response(response)
            if error:
                return error
            return "DNS settings updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # NETWORKS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_networks() -> str:
        """List all NetBird networks."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/networks")
            error = _check_nb_response(response)
            if error:
                return error
            networks = response.json()
            if not networks:
                return "No networks found."
            results = []
            for n in networks:
                results.append(f"- **{n.get('name', 'N/A')}** | Description: {n.get('description', 'N/A')} | ID: `{n.get('id', 'N/A')}`")
            return f"## Networks ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_network(
        network_id: str = Field(..., description="Network ID"),
    ) -> str:
        """Get network details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/networks/{network_id}")
            error = _check_nb_response(response)
            if error:
                return error
            n = response.json()
            return f"""## Network: {n.get('name', 'N/A')}

**ID:** `{n.get('id', 'N/A')}`
**Description:** {n.get('description', 'N/A')}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_network(
        name: str = Field(..., description="Network name"),
        description: Optional[str] = Field(None, description="Network description"),
    ) -> str:
        """Create a NetBird network."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {"name": name}
            if description:
                data["description"] = description
            response = await _nb_post(config, "/api/networks", data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Network created: **{name}** (ID: `{created.get('id', 'N/A')}`)"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_network(
        network_id: str = Field(..., description="Network ID"),
        name: str = Field(..., description="Network name"),
        description: Optional[str] = Field(None, description="Network description"),
    ) -> str:
        """Update a NetBird network."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {"name": name}
            if description:
                data["description"] = description
            response = await _nb_put(config, f"/api/networks/{network_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"Network `{network_id}` updated."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_network(
        network_id: str = Field(..., description="Network ID"),
    ) -> str:
        """Delete a NetBird network."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/networks/{network_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Network `{network_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # NETWORK RESOURCES
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_network_resources(
        network_id: str = Field(..., description="Network ID"),
    ) -> str:
        """List resources in a NetBird network."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/networks/{network_id}/resources")
            error = _check_nb_response(response)
            if error:
                return error
            resources = response.json()
            if not resources:
                return "No resources found in this network."
            results = []
            for r in resources:
                groups = ", ".join(str(g) for g in (r.get("groups", []) or []))
                results.append(f"- **{r.get('name', 'N/A')}** | Address: {r.get('address', 'N/A')} | Enabled: {r.get('enabled', False)} | Groups: {groups or 'None'} | ID: `{r.get('id', 'N/A')}`")
            return f"## Network Resources ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_network_resource(
        network_id: str = Field(..., description="Network ID"),
        resource_id: str = Field(..., description="Resource ID"),
    ) -> str:
        """Get network resource details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/networks/{network_id}/resources/{resource_id}")
            error = _check_nb_response(response)
            if error:
                return error
            r = response.json()
            groups = ", ".join(str(g) for g in (r.get("groups", []) or []))
            return f"""## Network Resource: {r.get('name', 'N/A')}

**ID:** `{r.get('id', 'N/A')}`
**Address:** {r.get('address', 'N/A')}
**Description:** {r.get('description', 'N/A')}
**Enabled:** {r.get('enabled', False)}
**Groups:** {groups or 'None'}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_network_resource(
        network_id: str = Field(..., description="Network ID"),
        name: str = Field(..., description="Resource name"),
        address: str = Field(..., description="Resource address (IP, subnet, or domain)"),
        enabled: bool = Field(True, description="Enable the resource"),
        groups: str = Field(..., description='JSON array of group IDs'),
        description: Optional[str] = Field(None, description="Description"),
    ) -> str:
        """Create a network resource."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {
                "name": name,
                "address": address,
                "enabled": enabled,
                "groups": json.loads(groups),
            }
            if description:
                data["description"] = description
            response = await _nb_post(config, f"/api/networks/{network_id}/resources", data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Network resource created: **{name}** (ID: `{created.get('id', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_network_resource(
        network_id: str = Field(..., description="Network ID"),
        resource_id: str = Field(..., description="Resource ID"),
        name: str = Field(..., description="Resource name"),
        address: str = Field(..., description="Resource address"),
        enabled: bool = Field(True, description="Enable the resource"),
        groups: str = Field(..., description='JSON array of group IDs'),
        description: Optional[str] = Field(None, description="Description"),
    ) -> str:
        """Update a network resource."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {
                "name": name,
                "address": address,
                "enabled": enabled,
                "groups": json.loads(groups),
            }
            if description:
                data["description"] = description
            response = await _nb_put(config, f"/api/networks/{network_id}/resources/{resource_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"Network resource `{resource_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_network_resource(
        network_id: str = Field(..., description="Network ID"),
        resource_id: str = Field(..., description="Resource ID"),
    ) -> str:
        """Delete a network resource."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/networks/{network_id}/resources/{resource_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Network resource `{resource_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # NETWORK ROUTERS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_network_routers(
        network_id: str = Field(..., description="Network ID"),
    ) -> str:
        """List routers in a NetBird network."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/networks/{network_id}/routers")
            error = _check_nb_response(response)
            if error:
                return error
            routers = response.json()
            if not routers:
                return "No routers found in this network."
            results = []
            for r in routers:
                results.append(f"- Peer: {r.get('peer', 'N/A')} | Metric: {r.get('metric', 'N/A')} | Masquerade: {r.get('masquerade', False)} | Enabled: {r.get('enabled', False)} | ID: `{r.get('id', 'N/A')}`")
            return f"## Network Routers ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_all_network_routers() -> str:
        """List all routers across all networks."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/networks/routers")
            error = _check_nb_response(response)
            if error:
                return error
            routers = response.json()
            if not routers:
                return "No network routers found."
            results = []
            for r in routers:
                results.append(f"- Peer: {r.get('peer', 'N/A')} | Network: {r.get('network_id', 'N/A')} | Metric: {r.get('metric', 'N/A')} | Enabled: {r.get('enabled', False)} | ID: `{r.get('id', 'N/A')}`")
            return f"## All Network Routers ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_network_router(
        network_id: str = Field(..., description="Network ID"),
        router_id: str = Field(..., description="Router ID"),
    ) -> str:
        """Get network router details."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/networks/{network_id}/routers/{router_id}")
            error = _check_nb_response(response)
            if error:
                return error
            r = response.json()
            peer_groups = ", ".join(str(pg) for pg in (r.get("peer_groups", []) or []))
            return f"""## Network Router

**ID:** `{r.get('id', 'N/A')}`
**Peer:** {r.get('peer', 'N/A')}
**Peer Groups:** {peer_groups or 'None'}
**Metric:** {r.get('metric', 'N/A')}
**Masquerade:** {r.get('masquerade', False)}
**Enabled:** {r.get('enabled', False)}"""
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_create_network_router(
        network_id: str = Field(..., description="Network ID"),
        metric: int = Field(9999, description="Router metric (1-9999)"),
        masquerade: bool = Field(True, description="Enable masquerade"),
        enabled: bool = Field(True, description="Enable the router"),
        peer: Optional[str] = Field(None, description="Peer ID (use this OR peer_groups)"),
        peer_groups: Optional[str] = Field(None, description='JSON array of peer group IDs (use this OR peer)'),
    ) -> str:
        """Create a network router."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {
                "metric": metric,
                "masquerade": masquerade,
                "enabled": enabled,
            }
            if peer:
                data["peer"] = peer
            if peer_groups:
                data["peer_groups"] = json.loads(peer_groups)
            response = await _nb_post(config, f"/api/networks/{network_id}/routers", data)
            error = _check_nb_response(response)
            if error:
                return error
            created = response.json()
            return f"Network router created (ID: `{created.get('id', 'N/A')}`)"
        except json.JSONDecodeError:
            return "Error: Invalid JSON in peer_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
    async def netbird_update_network_router(
        network_id: str = Field(..., description="Network ID"),
        router_id: str = Field(..., description="Router ID"),
        metric: int = Field(9999, description="Router metric (1-9999)"),
        masquerade: bool = Field(True, description="Enable masquerade"),
        enabled: bool = Field(True, description="Enable the router"),
        peer: Optional[str] = Field(None, description="Peer ID"),
        peer_groups: Optional[str] = Field(None, description='JSON array of peer group IDs'),
    ) -> str:
        """Update a network router."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            data = {
                "metric": metric,
                "masquerade": masquerade,
                "enabled": enabled,
            }
            if peer:
                data["peer"] = peer
            if peer_groups:
                data["peer_groups"] = json.loads(peer_groups)
            response = await _nb_put(config, f"/api/networks/{network_id}/routers/{router_id}", data)
            error = _check_nb_response(response)
            if error:
                return error
            return f"Network router `{router_id}` updated."
        except json.JSONDecodeError:
            return "Error: Invalid JSON in peer_groups."
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
    async def netbird_delete_network_router(
        network_id: str = Field(..., description="Network ID"),
        router_id: str = Field(..., description="Router ID"),
    ) -> str:
        """Delete a network router."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_delete(config, f"/api/networks/{network_id}/routers/{router_id}")
            error = _check_nb_response(response)
            if error:
                return error
            return f"Network router `{router_id}` deleted."
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # EVENTS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_audit_events() -> str:
        """List recent audit events (activity log)."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/events/audit")
            error = _check_nb_response(response)
            if error:
                return error
            events = response.json()
            if not events:
                return "No audit events found."
            results = []
            for e in (events[:50] if isinstance(events, list) else []):
                initiator = e.get("initiator_name", "") or e.get("initiator_email", "System")
                results.append(f"- **{e.get('activity', 'N/A')}** | By: {initiator} | Target: {e.get('target_id', 'N/A')} | {(e.get('timestamp', '') or '')[:19]}")
            return f"## Audit Events ({len(results)} shown)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_network_traffic_events(
        page: int = Field(1, description="Page number"),
        page_size: int = Field(50, description="Results per page"),
        search: Optional[str] = Field(None, description="Search term"),
        protocol: Optional[int] = Field(None, description="Protocol number (6=TCP, 17=UDP)"),
        start_date: Optional[str] = Field(None, description="Start date (ISO 8601)"),
        end_date: Optional[str] = Field(None, description="End date (ISO 8601)"),
    ) -> str:
        """List network traffic events."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"page": page, "page_size": page_size}
            if search:
                params["search"] = search
            if protocol is not None:
                params["protocol"] = protocol
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            response = await _nb_get(config, "/api/events/network-traffic", params)
            error = _check_nb_response(response)
            if error:
                return error
            data = response.json()
            events = data if isinstance(data, list) else data.get("data", [])
            if not events:
                return "No network traffic events found."
            results = []
            for e in events[:50]:
                results.append(f"- {e.get('source_ip', 'N/A')}:{e.get('source_port', '')} -> {e.get('dest_ip', 'N/A')}:{e.get('dest_port', '')} | Proto: {e.get('protocol', 'N/A')} | Type: {e.get('type', 'N/A')} | {(e.get('timestamp', '') or '')[:19]}")
            return f"## Network Traffic Events ({len(results)} shown)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_get_proxy_events(
        page: int = Field(1, description="Page number"),
        page_size: int = Field(50, description="Results per page"),
        search: Optional[str] = Field(None, description="Search term"),
        host: Optional[str] = Field(None, description="Filter by host"),
        method: Optional[str] = Field(None, description="Filter by HTTP method"),
        status_code: Optional[int] = Field(None, description="Filter by status code"),
        start_date: Optional[str] = Field(None, description="Start date (RFC3339)"),
        end_date: Optional[str] = Field(None, description="End date (RFC3339)"),
    ) -> str:
        """List reverse proxy access log events."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            params = {"page": page, "page_size": page_size}
            if search:
                params["search"] = search
            if host:
                params["host"] = host
            if method:
                params["method"] = method
            if status_code is not None:
                params["status_code"] = status_code
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            response = await _nb_get(config, "/api/events/proxy", params)
            error = _check_nb_response(response)
            if error:
                return error
            data = response.json()
            events = data if isinstance(data, list) else data.get("data", [])
            if not events:
                return "No proxy events found."
            results = []
            for e in events[:50]:
                results.append(f"- {e.get('method', '')} {e.get('host', '')}{e.get('path', '')} | Status: {e.get('status_code', 'N/A')} | User: {e.get('user_email', 'N/A')} | Source: {e.get('source_ip', 'N/A')} | {(e.get('timestamp', '') or '')[:19]}")
            return f"## Proxy Events ({len(results)} shown)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # GEO LOCATIONS
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_countries() -> str:
        """List all available country codes for geo-location posture checks."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, "/api/locations/countries")
            error = _check_nb_response(response)
            if error:
                return error
            countries = response.json()
            if not countries:
                return "No countries found."
            results = [f"- **{c.get('country_code', 'N/A')}** - {c.get('country_name', 'N/A')}" for c in countries]
            return f"## Countries ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def netbird_list_cities(
        country_code: str = Field(..., description="Country code (ISO 3166-1 alpha-2, e.g., 'AU')"),
    ) -> str:
        """List cities in a country for geo-location posture checks."""
        if not config.is_configured:
            return NOT_CONFIGURED
        try:
            response = await _nb_get(config, f"/api/locations/countries/{country_code}/cities")
            error = _check_nb_response(response)
            if error:
                return error
            cities = response.json()
            if not cities:
                return f"No cities found for {country_code}."
            results = [f"- {c.get('city_name', 'N/A')}" for c in cities]
            return f"## Cities in {country_code} ({len(results)} found)\n\n" + "\n".join(results)
        except Exception as e:
            return f"Error: {str(e)}"
