"""
Microsoft Teams Integration Tools for Crowd IT MCP Server

This module provides Microsoft Teams management capabilities using the
Microsoft Graph API, designed for AI-driven team collaboration.

Capabilities:
- List and get teams
- List, get, and create channels
- List, send, and reply to channel messages
- List chats and chat messages, send chat messages
- List team and channel members
- Get user presence/availability

Authentication: Uses OAuth2 client_credentials flow with an Azure AD app
registration. Reuses the same credentials as email (EMAIL_TENANT_ID,
EMAIL_CLIENT_ID, EMAIL_CLIENT_SECRET) but can be overridden with TEAMS_*
env vars if a separate app registration is needed.

Required Application Permissions (admin consent):
    Team.ReadBasic.All, Channel.ReadBasic.All, ChannelMessage.Send,
    Chat.ReadWrite.All, ChannelMember.Read.All, ChatMessage.Read.All,
    Presence.Read.All, Group.Read.All, User.Read.All

Environment Variables:
    TEAMS_TENANT_ID: Azure AD tenant ID (falls back to EMAIL_TENANT_ID)
    TEAMS_CLIENT_ID: Azure AD Application (client) ID (falls back to EMAIL_CLIENT_ID)
    TEAMS_CLIENT_SECRET: Azure AD Application client secret (falls back to EMAIL_CLIENT_SECRET)
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration and Authentication
# =============================================================================

class TeamsConfig:
    """Microsoft Teams config using Graph API with client_credentials flow.

    Reuses the same Azure AD app as email (EMAIL_TENANT_ID, EMAIL_CLIENT_ID,
    EMAIL_CLIENT_SECRET) but can be overridden with TEAMS_* env vars if a
    separate app is needed.
    """

    def __init__(self):
        self.tenant_id = os.getenv("TEAMS_TENANT_ID", "") or os.getenv("EMAIL_TENANT_ID", "")
        self.client_id = os.getenv("TEAMS_CLIENT_ID", "") or os.getenv("EMAIL_CLIENT_ID", "")
        self._client_secret = ""
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._secrets_loaded = False

    def _load_secrets(self):
        if self._secrets_loaded:
            return
        # Try Teams-specific secrets first, fall back to Email secrets
        for prefix in ("TEAMS", "EMAIL"):
            if not self._client_secret:
                try:
                    from app.core.config import get_secret_sync
                    self._client_secret = get_secret_sync(f"{prefix}_CLIENT_SECRET") or ""
                except Exception:
                    pass
            if not self._client_secret:
                self._client_secret = os.getenv(f"{prefix}_CLIENT_SECRET", "")
        if not self.tenant_id:
            try:
                from app.core.config import get_secret_sync
                self.tenant_id = get_secret_sync("EMAIL_TENANT_ID") or ""
            except Exception:
                pass
        self._secrets_loaded = True

    @property
    def is_configured(self) -> bool:
        self._load_secrets()
        return all([self.tenant_id, self.client_id, self._client_secret])

    async def get_access_token(self) -> str:
        """Get access token using client_credentials flow."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        self._load_secrets()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self._client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expiry = datetime.now() + timedelta(
                seconds=data.get("expires_in", 3600) - 60
            )
            return self._access_token

    async def graph_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        json_body: dict = None,
    ) -> httpx.Response:
        """Make a Microsoft Graph API request and return the raw Response."""
        token = await self.get_access_token()
        url = f"https://graph.microsoft.com/v1.0{endpoint}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                params=params,
                json=json_body,
            )
            return resp


# =============================================================================
# Helpers
# =============================================================================

async def _collect_pages(
    config: TeamsConfig,
    method: str,
    endpoint: str,
    params: dict = None,
    max_pages: int = 5,
) -> List[dict]:
    """Follow @odata.nextLink pagination and collect all value items."""
    all_items: List[dict] = []
    pages = 0
    next_url: Optional[str] = None

    while pages < max_pages:
        if next_url:
            # nextLink is an absolute URL
            token = await config.get_access_token()
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    next_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
        else:
            resp = await config.graph_request(method, endpoint, params=params)

        if resp.status_code >= 400:
            raise Exception(
                f"Graph API error {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        all_items.extend(data.get("value", []))
        next_url = data.get("@odata.nextLink")
        pages += 1

        if not next_url:
            break

    return all_items


def _check_response(resp: httpx.Response) -> dict:
    """Check a Graph response and return parsed JSON or raise."""
    if resp.status_code >= 400:
        raise Exception(f"Graph API error {resp.status_code}: {resp.text}")
    if resp.status_code == 204:
        return {"status": "success"}
    return resp.json()


NOT_CONFIGURED_MSG = (
    "Microsoft Teams not configured. Set TEAMS_TENANT_ID / EMAIL_TENANT_ID, "
    "TEAMS_CLIENT_ID / EMAIL_CLIENT_ID, and TEAMS_CLIENT_SECRET / EMAIL_CLIENT_SECRET."
)


# =============================================================================
# Tool Registration
# =============================================================================

def register_teams_tools(mcp, config: 'TeamsConfig') -> None:
    """Register all Microsoft Teams tools with the MCP server."""

    # =========================================================================
    # TEAMS
    # =========================================================================

    @mcp.tool(
        name="teams_list_teams",
        annotations={
            "title": "List Teams",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_list_teams(
        top: int = 50,
        skip: int = 0,
    ) -> str:
        """List all Microsoft Teams in the organisation.

        Args:
            top: Number of teams to return (max 100, default 50)
            skip: Number of teams to skip for pagination
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            top = min(top, 100)
            params = {
                "$filter": "resourceProvisioningOptions/Any(x:x eq 'Team')",
                "$select": "id,displayName,description,mail,visibility",
                "$top": top,
                "$skip": skip,
                "$orderby": "displayName",
            }
            items = await _collect_pages(config, "GET", "/groups", params=params, max_pages=3)
            return json.dumps({"count": len(items), "teams": items}, indent=2, default=str)
        except Exception as e:
            return f"Error listing teams: {e}"

    @mcp.tool(
        name="teams_get_team",
        annotations={
            "title": "Get Team Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_get_team(
        team_id: str = Field(description="The team (group) ID"),
    ) -> str:
        """Get details of a specific Microsoft Team.

        Args:
            team_id: The team (group) ID
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await config.graph_request("GET", f"/teams/{team_id}")
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error getting team: {e}"

    # =========================================================================
    # CHANNELS
    # =========================================================================

    @mcp.tool(
        name="teams_list_channels",
        annotations={
            "title": "List Channels",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_list_channels(
        team_id: str = Field(description="The team ID"),
    ) -> str:
        """List all channels in a Microsoft Team.

        Args:
            team_id: The team ID
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            items = await _collect_pages(
                config, "GET", f"/teams/{team_id}/channels"
            )
            return json.dumps({"count": len(items), "channels": items}, indent=2, default=str)
        except Exception as e:
            return f"Error listing channels: {e}"

    @mcp.tool(
        name="teams_get_channel",
        annotations={
            "title": "Get Channel Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_get_channel(
        team_id: str = Field(description="The team ID"),
        channel_id: str = Field(description="The channel ID"),
    ) -> str:
        """Get details of a specific channel.

        Args:
            team_id: The team ID
            channel_id: The channel ID
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await config.graph_request(
                "GET", f"/teams/{team_id}/channels/{channel_id}"
            )
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error getting channel: {e}"

    @mcp.tool(
        name="teams_create_channel",
        annotations={
            "title": "Create Channel",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def teams_create_channel(
        team_id: str = Field(description="The team ID"),
        display_name: str = Field(description="Channel display name"),
        description: str = Field(default="", description="Channel description"),
        membership_type: str = Field(
            default="standard",
            description="Channel type: 'standard' or 'private'",
        ),
    ) -> str:
        """Create a new channel in a Microsoft Team.

        Args:
            team_id: The team ID
            display_name: Channel display name
            description: Channel description
            membership_type: Channel type - 'standard' (default) or 'private'
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            body: Dict[str, Any] = {
                "displayName": display_name,
                "membershipType": membership_type,
            }
            if description:
                body["description"] = description

            resp = await config.graph_request(
                "POST", f"/teams/{team_id}/channels", json_body=body
            )
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error creating channel: {e}"

    # =========================================================================
    # CHANNEL MESSAGES
    # =========================================================================

    @mcp.tool(
        name="teams_list_channel_messages",
        annotations={
            "title": "List Channel Messages",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_list_channel_messages(
        team_id: str = Field(description="The team ID"),
        channel_id: str = Field(description="The channel ID"),
        top: int = 20,
    ) -> str:
        """List recent messages in a Teams channel.

        Args:
            team_id: The team ID
            channel_id: The channel ID
            top: Number of messages to return (max 50, default 20)
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            top = min(top, 50)
            params = {"$top": top}
            items = await _collect_pages(
                config,
                "GET",
                f"/teams/{team_id}/channels/{channel_id}/messages",
                params=params,
                max_pages=1,
            )
            # Simplify message output for readability
            messages = []
            for msg in items:
                messages.append({
                    "id": msg.get("id"),
                    "createdDateTime": msg.get("createdDateTime"),
                    "from": (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown"),
                    "body_contentType": (msg.get("body") or {}).get("contentType", ""),
                    "body_content": (msg.get("body") or {}).get("content", ""),
                    "importance": msg.get("importance"),
                    "messageType": msg.get("messageType"),
                })
            return json.dumps(
                {"count": len(messages), "messages": messages}, indent=2, default=str
            )
        except Exception as e:
            return f"Error listing channel messages: {e}"

    @mcp.tool(
        name="teams_send_channel_message",
        annotations={
            "title": "Send Channel Message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def teams_send_channel_message(
        team_id: str = Field(description="The team ID"),
        channel_id: str = Field(description="The channel ID"),
        content: str = Field(description="Message content (HTML supported)"),
        content_type: str = Field(
            default="html", description="Content type: 'text' or 'html' (default)"
        ),
    ) -> str:
        """Send a message to a Teams channel.

        Args:
            team_id: The team ID
            channel_id: The channel ID
            content: Message content (HTML supported)
            content_type: Content type - 'text' or 'html' (default 'html')
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            body = {
                "body": {
                    "contentType": content_type,
                    "content": content,
                }
            }
            resp = await config.graph_request(
                "POST",
                f"/teams/{team_id}/channels/{channel_id}/messages",
                json_body=body,
            )
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error sending channel message: {e}"

    @mcp.tool(
        name="teams_reply_to_message",
        annotations={
            "title": "Reply to Channel Message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def teams_reply_to_message(
        team_id: str = Field(description="The team ID"),
        channel_id: str = Field(description="The channel ID"),
        message_id: str = Field(description="The parent message ID to reply to"),
        content: str = Field(description="Reply content (HTML supported)"),
        content_type: str = Field(
            default="html", description="Content type: 'text' or 'html' (default)"
        ),
    ) -> str:
        """Reply to a message in a Teams channel.

        Args:
            team_id: The team ID
            channel_id: The channel ID
            message_id: The parent message ID to reply to
            content: Reply content (HTML supported)
            content_type: Content type - 'text' or 'html' (default 'html')
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            body = {
                "body": {
                    "contentType": content_type,
                    "content": content,
                }
            }
            resp = await config.graph_request(
                "POST",
                f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
                json_body=body,
            )
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error replying to message: {e}"

    # =========================================================================
    # CHATS
    # =========================================================================

    @mcp.tool(
        name="teams_list_chats",
        annotations={
            "title": "List Chats",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_list_chats(
        user_id: str = Field(description="The user ID or UPN (e.g. user@domain.com)"),
        top: int = 25,
    ) -> str:
        """List chats for a user.

        Args:
            user_id: The user ID or UPN (e.g. user@domain.com)
            top: Number of chats to return (max 50, default 25)
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            top = min(top, 50)
            params = {
                "$top": top,
                "$orderby": "lastMessagePreview/createdDateTime desc",
                "$expand": "lastMessagePreview",
            }
            items = await _collect_pages(
                config,
                "GET",
                f"/users/{user_id}/chats",
                params=params,
                max_pages=2,
            )
            chats = []
            for c in items:
                preview = c.get("lastMessagePreview") or {}
                chats.append({
                    "id": c.get("id"),
                    "topic": c.get("topic"),
                    "chatType": c.get("chatType"),
                    "createdDateTime": c.get("createdDateTime"),
                    "lastUpdatedDateTime": c.get("lastUpdatedDateTime"),
                    "lastMessage": {
                        "from": (preview.get("from") or {}).get("user", {}).get("displayName", ""),
                        "createdDateTime": preview.get("createdDateTime"),
                        "body_preview": (preview.get("body") or {}).get("content", "")[:200],
                    } if preview else None,
                })
            return json.dumps({"count": len(chats), "chats": chats}, indent=2, default=str)
        except Exception as e:
            return f"Error listing chats: {e}"

    @mcp.tool(
        name="teams_get_chat",
        annotations={
            "title": "Get Chat Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_get_chat(
        chat_id: str = Field(description="The chat ID"),
    ) -> str:
        """Get details of a specific chat.

        Args:
            chat_id: The chat ID
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await config.graph_request("GET", f"/chats/{chat_id}")
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error getting chat: {e}"

    @mcp.tool(
        name="teams_list_chat_messages",
        annotations={
            "title": "List Chat Messages",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_list_chat_messages(
        chat_id: str = Field(description="The chat ID"),
        top: int = 20,
    ) -> str:
        """List recent messages in a chat.

        Args:
            chat_id: The chat ID
            top: Number of messages to return (max 50, default 20)
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            top = min(top, 50)
            params = {"$top": top}
            items = await _collect_pages(
                config,
                "GET",
                f"/chats/{chat_id}/messages",
                params=params,
                max_pages=1,
            )
            messages = []
            for msg in items:
                messages.append({
                    "id": msg.get("id"),
                    "createdDateTime": msg.get("createdDateTime"),
                    "from": (msg.get("from") or {}).get("user", {}).get("displayName", "Unknown"),
                    "body_contentType": (msg.get("body") or {}).get("contentType", ""),
                    "body_content": (msg.get("body") or {}).get("content", ""),
                    "importance": msg.get("importance"),
                    "messageType": msg.get("messageType"),
                })
            return json.dumps(
                {"count": len(messages), "messages": messages}, indent=2, default=str
            )
        except Exception as e:
            return f"Error listing chat messages: {e}"

    @mcp.tool(
        name="teams_send_chat_message",
        annotations={
            "title": "Send Chat Message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def teams_send_chat_message(
        chat_id: str = Field(description="The chat ID"),
        content: str = Field(description="Message content (HTML supported)"),
        content_type: str = Field(
            default="html", description="Content type: 'text' or 'html' (default)"
        ),
    ) -> str:
        """Send a message to a chat.

        Args:
            chat_id: The chat ID
            content: Message content (HTML supported)
            content_type: Content type - 'text' or 'html' (default 'html')
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            body = {
                "body": {
                    "contentType": content_type,
                    "content": content,
                }
            }
            resp = await config.graph_request(
                "POST", f"/chats/{chat_id}/messages", json_body=body
            )
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error sending chat message: {e}"

    # =========================================================================
    # MEMBERS
    # =========================================================================

    @mcp.tool(
        name="teams_list_members",
        annotations={
            "title": "List Team Members",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_list_members(
        team_id: str = Field(description="The team ID"),
    ) -> str:
        """List all members of a Microsoft Team.

        Args:
            team_id: The team ID
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            items = await _collect_pages(
                config, "GET", f"/teams/{team_id}/members"
            )
            members = []
            for m in items:
                members.append({
                    "id": m.get("id"),
                    "userId": m.get("userId"),
                    "displayName": m.get("displayName"),
                    "email": m.get("email"),
                    "roles": m.get("roles", []),
                })
            return json.dumps(
                {"count": len(members), "members": members}, indent=2, default=str
            )
        except Exception as e:
            return f"Error listing team members: {e}"

    @mcp.tool(
        name="teams_list_channel_members",
        annotations={
            "title": "List Channel Members",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_list_channel_members(
        team_id: str = Field(description="The team ID"),
        channel_id: str = Field(description="The channel ID"),
    ) -> str:
        """List all members of a specific channel.

        Args:
            team_id: The team ID
            channel_id: The channel ID
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            items = await _collect_pages(
                config, "GET", f"/teams/{team_id}/channels/{channel_id}/members"
            )
            members = []
            for m in items:
                members.append({
                    "id": m.get("id"),
                    "userId": m.get("userId"),
                    "displayName": m.get("displayName"),
                    "email": m.get("email"),
                    "roles": m.get("roles", []),
                })
            return json.dumps(
                {"count": len(members), "members": members}, indent=2, default=str
            )
        except Exception as e:
            return f"Error listing channel members: {e}"

    # =========================================================================
    # PRESENCE
    # =========================================================================

    @mcp.tool(
        name="teams_get_user_presence",
        annotations={
            "title": "Get User Presence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def teams_get_user_presence(
        user_id: str = Field(
            description="The user ID or UPN (e.g. user@domain.com)"
        ),
    ) -> str:
        """Get the presence/availability status of a user in Microsoft Teams.

        Returns availability (Available, Busy, DoNotDisturb, Away, Offline, etc.)
        and activity (InACall, InAMeeting, Presenting, etc.).

        Args:
            user_id: The user ID or UPN (e.g. user@domain.com)
        """
        if not config.is_configured:
            return NOT_CONFIGURED_MSG
        try:
            resp = await config.graph_request(
                "GET", f"/users/{user_id}/presence"
            )
            data = _check_response(resp)
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"Error getting user presence: {e}"
