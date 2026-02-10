"""
Microsoft 365 Integration Tools for Crowd IT MCP Server

Full Microsoft Graph API integration providing:
- Email (Outlook): Search, read, send, reply, move, flag, delete, manage folders
- Calendar: List, create, update, delete events, check availability
- OneDrive: List, search, download, upload, create folders, delete files
- Teams: List teams/channels, send messages, list/send chats
- Contacts & People: List contacts, search people

Authentication: OAuth2 with refresh token via Azure AD app registration.
Uses the same Azure AD app as SharePoint but with expanded Microsoft Graph scopes.

Environment Variables:
    M365_CLIENT_ID: Azure AD app client ID (falls back to SHAREPOINT_CLIENT_ID)
    M365_CLIENT_SECRET: Azure AD app client secret (falls back to SHAREPOINT_CLIENT_SECRET)
    M365_TENANT_ID: Azure AD tenant ID (falls back to SHAREPOINT_TENANT_ID)
    M365_REFRESH_TOKEN: OAuth2 refresh token (stored in Secret Manager)
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from pydantic import Field

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Microsoft Graph scopes needed for full M365 access
M365_SCOPES = (
    "offline_access "
    "User.Read "
    "Mail.ReadWrite Mail.Send "
    "Calendars.ReadWrite "
    "Files.ReadWrite.All "
    "Sites.ReadWrite.All "
    "Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Send "
    "Chat.ReadWrite "
    "Contacts.ReadWrite People.Read "
    "ChannelMessage.Read.All "
    "OnlineMeetings.ReadWrite"
)


class Microsoft365Config:
    """Configuration and token management for Microsoft 365 Graph API."""

    def __init__(self):
        # Allow dedicated M365 credentials, falling back to SharePoint ones
        self.client_id = os.getenv("M365_CLIENT_ID", "") or os.getenv("SHAREPOINT_CLIENT_ID", "")
        self.client_secret = os.getenv("M365_CLIENT_SECRET", "") or os.getenv("SHAREPOINT_CLIENT_SECRET", "")
        self.tenant_id = os.getenv("M365_TENANT_ID", "") or os.getenv("SHAREPOINT_TENANT_ID", "")
        self._refresh_token = os.getenv("M365_REFRESH_TOKEN", "")
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None

    @property
    def is_configured(self) -> bool:
        return all([self.client_id, self.client_secret, self.tenant_id, self._refresh_token])

    async def get_access_token(self) -> str:
        """Get valid access token, refreshing if needed."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        from app.core.config import update_secret_sync

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self._refresh_token,
                    "scope": "https://graph.microsoft.com/.default offline_access",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            if "refresh_token" in data:
                new_refresh = data["refresh_token"]
                if new_refresh != self._refresh_token:
                    self._refresh_token = new_refresh
                    update_secret_sync("M365_REFRESH_TOKEN", new_refresh)
                    logger.info("M365 refresh token rotated and saved to Secret Manager")

            expires_in = data.get("expires_in", 3600)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            return self._access_token


async def _graph_request(
    config: Microsoft365Config,
    method: str,
    endpoint: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    raw_response: bool = False,
) -> dict | list | str:
    """Make an authenticated Microsoft Graph API request."""
    token = await config.get_access_token()
    url = f"{GRAPH_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()

        if response.status_code == 204:
            return {"status": "success"}
        if raw_response:
            return response.text
        return response.json()


def register_microsoft365_tools(mcp, config_getter):
    """
    Register all Microsoft 365 tools with the MCP server.

    Args:
        mcp: FastMCP server instance
        config_getter: Callable that returns the Microsoft365Config instance
    """

    # =========================================================================
    # Authentication Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True})
    async def m365_auth_start() -> str:
        """Get authorization URL to connect Microsoft 365 (Email, Calendar, OneDrive, Teams). Use this if M365 is not connected."""
        cfg = config_getter()
        client_id = cfg.client_id if cfg else os.getenv("M365_CLIENT_ID", "") or os.getenv("SHAREPOINT_CLIENT_ID", "")
        tenant_id = cfg.tenant_id if cfg else os.getenv("M365_TENANT_ID", "") or os.getenv("SHAREPOINT_TENANT_ID", "")
        cloud_run_url = os.getenv("CLOUD_RUN_URL", "")

        if not client_id:
            return "Error: M365_CLIENT_ID (or SHAREPOINT_CLIENT_ID) not configured."
        if not tenant_id:
            return "Error: M365_TENANT_ID (or SHAREPOINT_TENANT_ID) not configured."

        redirect_uri = f"{cloud_run_url}/m365-callback"

        auth_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
            f"?client_id={client_id}"
            f"&response_type=code"
            f"&redirect_uri={redirect_uri}"
            f"&scope={M365_SCOPES}"
            f"&response_mode=query"
            f"&state=m365"
        )

        return f"""## Microsoft 365 Authorization Required

**Click this link to authorize:**
{auth_url}

After authorizing, you'll be redirected back automatically and M365 will be connected.

**Redirect URI for Azure AD App:** `{redirect_uri}`

**Required API permissions (Microsoft Graph - Delegated):**
- Mail.ReadWrite, Mail.Send
- Calendars.ReadWrite
- Files.ReadWrite.All, Sites.ReadWrite.All
- Team.ReadBasic.All, Channel.ReadBasic.All, ChannelMessage.Send
- Chat.ReadWrite, ChannelMessage.Read.All
- Contacts.ReadWrite, People.Read
- OnlineMeetings.ReadWrite
- User.Read"""

    @mcp.tool(annotations={"readOnlyHint": False})
    async def m365_auth_complete(
        auth_code: str = Field(..., description="Authorization code from callback URL"),
    ) -> str:
        """Complete Microsoft 365 authorization with the code from callback URL."""
        cfg = config_getter()
        client_id = cfg.client_id if cfg else os.getenv("M365_CLIENT_ID", "") or os.getenv("SHAREPOINT_CLIENT_ID", "")
        client_secret = cfg.client_secret if cfg else os.getenv("M365_CLIENT_SECRET", "") or os.getenv("SHAREPOINT_CLIENT_SECRET", "")
        tenant_id = cfg.tenant_id if cfg else os.getenv("M365_TENANT_ID", "") or os.getenv("SHAREPOINT_TENANT_ID", "")
        cloud_run_url = os.getenv("CLOUD_RUN_URL", "")

        if not all([client_id, client_secret, tenant_id]):
            return "Error: M365 credentials not configured."

        redirect_uri = f"{cloud_run_url}/m365-callback"

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
                        "scope": "https://graph.microsoft.com/.default offline_access",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                tokens = response.json()

                access_token = tokens["access_token"]
                refresh_token = tokens.get("refresh_token", "")

            cfg._access_token = access_token
            cfg._refresh_token = refresh_token
            cfg._token_expiry = datetime.now() + timedelta(seconds=tokens.get("expires_in", 3600) - 60)

            from app.core.config import update_secret_sync
            saved = update_secret_sync("M365_REFRESH_TOKEN", refresh_token) if refresh_token else False

            # Get user profile
            try:
                profile = await _graph_request(cfg, "GET", "/me")
                user_name = profile.get("displayName", "Unknown")
                user_email = profile.get("mail", profile.get("userPrincipalName", "Unknown"))
                user_info = f"\n**User:** {user_name} ({user_email})"
            except Exception:
                user_info = ""

            if saved:
                return f"""Connected to Microsoft 365!{user_info}

Refresh token saved to Secret Manager. Email, Calendar, OneDrive, and Teams are now accessible."""
            else:
                return f"""Connected to Microsoft 365 for this session!{user_info}

To persist, save M365_REFRESH_TOKEN to Secret Manager:
```bash
echo -n "{refresh_token}" | gcloud secrets versions add M365_REFRESH_TOKEN --data-file=- --project=crowdmcp
```"""

        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.text}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def m365_whoami() -> str:
        """Get the currently authenticated Microsoft 365 user profile."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: Microsoft 365 not configured. Run m365_auth_start to connect."

        try:
            profile = await _graph_request(cfg, "GET", "/me")
            lines = ["## Microsoft 365 Profile"]
            lines.append(f"**Name:** {profile.get('displayName', 'N/A')}")
            lines.append(f"**Email:** {profile.get('mail', profile.get('userPrincipalName', 'N/A'))}")
            lines.append(f"**Job Title:** {profile.get('jobTitle', 'N/A')}")
            lines.append(f"**Office:** {profile.get('officeLocation', 'N/A')}")
            lines.append(f"**Mobile:** {profile.get('mobilePhone', 'N/A')}")
            lines.append(f"**ID:** `{profile.get('id', 'N/A')}`")
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 authentication expired. Run m365_auth_start to reconnect."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # EMAIL (Outlook) Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_list_mail_folders(
        parent_folder_id: Optional[str] = Field(None, description="Parent folder ID to list subfolders. Omit for top-level folders."),
    ) -> str:
        """List Outlook mail folders (Inbox, Sent, Drafts, etc.)."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            if parent_folder_id:
                endpoint = f"/me/mailFolders/{parent_folder_id}/childFolders"
            else:
                endpoint = "/me/mailFolders?$top=50"
            data = await _graph_request(cfg, "GET", endpoint)
            folders = data.get("value", [])

            if not folders:
                return "No mail folders found."

            lines = ["## Mail Folders\n"]
            for f in folders:
                unread = f.get("unreadItemCount", 0)
                total = f.get("totalItemCount", 0)
                unread_badge = f" ({unread} unread)" if unread > 0 else ""
                lines.append(f"- **{f.get('displayName', 'Unknown')}** — {total} items{unread_badge}\n  ID: `{f.get('id', '')}`")
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 auth expired. Run m365_auth_start."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_search_emails(
        query: Optional[str] = Field(None, description="Search query (searches subject, body, sender). E.g. 'invoice from:john'"),
        folder: str = Field("Inbox", description="Mail folder name or ID. Common: Inbox, SentItems, Drafts, DeletedItems, Archive, Junkemail"),
        top: int = Field(20, description="Number of results (1-50)"),
        filter_unread: Optional[bool] = Field(None, description="Filter to unread only (true) or read only (false)"),
        from_address: Optional[str] = Field(None, description="Filter by sender email address"),
        subject_contains: Optional[str] = Field(None, description="Filter by subject containing text"),
        has_attachments: Optional[bool] = Field(None, description="Filter to emails with attachments"),
        received_after: Optional[str] = Field(None, description="Filter emails received after this date (ISO format, e.g. 2024-01-15)"),
        received_before: Optional[str] = Field(None, description="Filter emails received before this date (ISO format)"),
        skip: int = Field(0, description="Number of results to skip (for pagination)"),
    ) -> str:
        """Search and list emails from Outlook with powerful filtering options."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            # Determine folder path - use well-known names or IDs
            well_known = {"inbox", "sentitems", "drafts", "deleteditems", "archive", "junkemail"}
            folder_clean = folder.replace(" ", "").lower()
            if folder_clean in well_known or len(folder) > 50:  # Long strings are likely IDs
                folder_path = folder
            else:
                folder_path = folder

            # Build OData filters
            filters = []
            if filter_unread is not None:
                filters.append(f"isRead eq {str(not filter_unread).lower()}")
            if from_address:
                filters.append(f"from/emailAddress/address eq '{from_address}'")
            if has_attachments is not None:
                filters.append(f"hasAttachments eq {str(has_attachments).lower()}")
            if received_after:
                filters.append(f"receivedDateTime ge {received_after}T00:00:00Z")
            if received_before:
                filters.append(f"receivedDateTime lt {received_before}T00:00:00Z")
            if subject_contains:
                filters.append(f"contains(subject, '{subject_contains}')")

            params = {
                "$top": min(top, 50),
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,toRecipients,receivedDateTime,isRead,hasAttachments,importance,flag,bodyPreview",
            }
            if skip > 0:
                params["$skip"] = skip
            if filters:
                params["$filter"] = " and ".join(filters)
            if query:
                params["$search"] = f'"{query}"'

            endpoint = f"/me/mailFolders/{folder_path}/messages"
            data = await _graph_request(cfg, "GET", endpoint, params=params)
            messages = data.get("value", [])

            if not messages:
                return f"No emails found in {folder} matching your criteria."

            lines = [f"## Emails in {folder}\n"]
            for msg in messages:
                sender = msg.get("from", {}).get("emailAddress", {})
                sender_str = f"{sender.get('name', '')} <{sender.get('address', '')}>"
                recv_dt = msg.get("receivedDateTime", "")[:16].replace("T", " ")
                read_icon = "" if msg.get("isRead") else "[UNREAD] "
                attach_icon = " [+attachments]" if msg.get("hasAttachments") else ""
                flag_status = msg.get("flag", {}).get("flagStatus", "")
                flag_icon = " [FLAGGED]" if flag_status == "flagged" else ""
                importance = msg.get("importance", "normal")
                imp_icon = " [HIGH]" if importance == "high" else ""
                preview = (msg.get("bodyPreview", "") or "")[:120]
                if preview:
                    preview = f"\n  > {preview}..."

                lines.append(
                    f"**{read_icon}{msg.get('subject', '(no subject)')}**{attach_icon}{flag_icon}{imp_icon}\n"
                    f"  From: {sender_str}\n"
                    f"  Date: {recv_dt}\n"
                    f"  ID: `{msg.get('id', '')[:50]}...`{preview}\n"
                )

            total_hint = f"\nShowing {len(messages)} results (skip={skip}). Use `skip` parameter for more." if len(messages) == min(top, 50) else ""
            return "\n".join(lines) + total_hint
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 auth expired. Run m365_auth_start."
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_read_email(
        message_id: str = Field(..., description="Email message ID"),
        include_attachments: bool = Field(False, description="List attachments with download info"),
    ) -> str:
        """Read a specific email message with full body content."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            params = {"$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,sentDateTime,body,hasAttachments,importance,flag,isRead,conversationId,internetMessageHeaders,replyTo"}
            msg = await _graph_request(cfg, "GET", f"/me/messages/{message_id}", params=params)

            sender = msg.get("from", {}).get("emailAddress", {})
            to_list = ", ".join(f"{r['emailAddress'].get('name', '')} <{r['emailAddress']['address']}>" for r in msg.get("toRecipients", []))
            cc_list = ", ".join(f"{r['emailAddress'].get('name', '')} <{r['emailAddress']['address']}>" for r in msg.get("ccRecipients", []))

            body_content = msg.get("body", {}).get("content", "")
            body_type = msg.get("body", {}).get("contentType", "text")

            # Strip HTML tags for readability if HTML
            if body_type == "html":
                import re
                body_content = re.sub(r'<style[^>]*>.*?</style>', '', body_content, flags=re.DOTALL)
                body_content = re.sub(r'<script[^>]*>.*?</script>', '', body_content, flags=re.DOTALL)
                body_content = re.sub(r'<br\s*/?>', '\n', body_content)
                body_content = re.sub(r'</p>', '\n\n', body_content)
                body_content = re.sub(r'</div>', '\n', body_content)
                body_content = re.sub(r'<[^>]+>', '', body_content)
                body_content = re.sub(r'\n{3,}', '\n\n', body_content)
                body_content = body_content.strip()

            lines = [f"## {msg.get('subject', '(no subject)')}\n"]
            lines.append(f"**From:** {sender.get('name', '')} <{sender.get('address', '')}>")
            lines.append(f"**To:** {to_list}")
            if cc_list:
                lines.append(f"**CC:** {cc_list}")
            lines.append(f"**Date:** {msg.get('receivedDateTime', '')[:19].replace('T', ' ')}")
            lines.append(f"**Importance:** {msg.get('importance', 'normal')}")
            flag_status = msg.get("flag", {}).get("flagStatus", "notFlagged")
            if flag_status != "notFlagged":
                lines.append(f"**Flag:** {flag_status}")
            lines.append(f"**Conversation ID:** `{msg.get('conversationId', 'N/A')}`")
            lines.append(f"**Message ID:** `{msg.get('id', '')}`\n")
            lines.append("---\n")
            lines.append(body_content)

            if include_attachments and msg.get("hasAttachments"):
                att_data = await _graph_request(cfg, "GET", f"/me/messages/{message_id}/attachments")
                attachments = att_data.get("value", [])
                if attachments:
                    lines.append("\n---\n## Attachments\n")
                    for att in attachments:
                        size_kb = (att.get("size", 0) or 0) / 1024
                        lines.append(f"- **{att.get('name', 'Unknown')}** ({size_kb:.1f} KB) — Type: {att.get('contentType', 'unknown')}\n  ID: `{att.get('id', '')}`")

            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 auth expired. Run m365_auth_start."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_send_email(
        to: str = Field(..., description="Recipient email addresses (comma-separated for multiple)"),
        subject: str = Field(..., description="Email subject"),
        body: str = Field(..., description="Email body (plain text or HTML)"),
        cc: Optional[str] = Field(None, description="CC recipients (comma-separated)"),
        bcc: Optional[str] = Field(None, description="BCC recipients (comma-separated)"),
        importance: str = Field("normal", description="Importance: low, normal, high"),
        is_html: bool = Field(False, description="Set to true if body contains HTML"),
        save_to_sent: bool = Field(True, description="Save copy to Sent Items"),
    ) -> str:
        """Send an email via Outlook."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        def parse_recipients(addr_str: str) -> list:
            return [{"emailAddress": {"address": a.strip()}} for a in addr_str.split(",") if a.strip()]

        try:
            message = {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if is_html else "Text",
                    "content": body,
                },
                "toRecipients": parse_recipients(to),
                "importance": importance,
            }
            if cc:
                message["ccRecipients"] = parse_recipients(cc)
            if bcc:
                message["bccRecipients"] = parse_recipients(bcc)

            payload = {"message": message, "saveToSentItems": save_to_sent}
            await _graph_request(cfg, "POST", "/me/sendMail", json_body=payload)

            to_preview = to if len(to) <= 80 else to[:77] + "..."
            return f"Email sent to {to_preview}\nSubject: {subject}"
        except httpx.HTTPStatusError as e:
            return f"Error sending email: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_reply_email(
        message_id: str = Field(..., description="ID of the email to reply to"),
        body: str = Field(..., description="Reply body text"),
        reply_all: bool = Field(False, description="Reply to all recipients"),
        is_html: bool = Field(False, description="Set to true if body contains HTML"),
    ) -> str:
        """Reply to an email message."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            action = "replyAll" if reply_all else "reply"
            payload = {
                "message": {
                    "body": {
                        "contentType": "HTML" if is_html else "Text",
                        "content": body,
                    }
                }
            }
            await _graph_request(cfg, "POST", f"/me/messages/{message_id}/{action}", json_body=payload)
            return f"Reply {'all ' if reply_all else ''}sent successfully."
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_forward_email(
        message_id: str = Field(..., description="ID of the email to forward"),
        to: str = Field(..., description="Forward to email addresses (comma-separated)"),
        comment: Optional[str] = Field(None, description="Optional comment to include with the forward"),
    ) -> str:
        """Forward an email to other recipients."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            to_recipients = [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()]
            payload = {"toRecipients": to_recipients}
            if comment:
                payload["comment"] = comment
            await _graph_request(cfg, "POST", f"/me/messages/{message_id}/forward", json_body=payload)
            return f"Email forwarded to {to}."
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_move_email(
        message_id: str = Field(..., description="Email message ID to move"),
        destination_folder: str = Field(..., description="Destination folder name or ID (e.g. 'Archive', 'DeletedItems', 'Inbox', or a folder ID)"),
    ) -> str:
        """Move an email to a different folder."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            # Well-known folder names can be used directly as IDs
            payload = {"destinationId": destination_folder}
            result = await _graph_request(cfg, "POST", f"/me/messages/{message_id}/move", json_body=payload)
            return f"Email moved to {destination_folder}. New message ID: `{result.get('id', 'N/A')}`"
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_flag_email(
        message_id: str = Field(..., description="Email message ID"),
        flag_status: str = Field("flagged", description="Flag status: 'flagged', 'complete', or 'notFlagged'"),
    ) -> str:
        """Flag or unflag an email for follow-up."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            payload = {"flag": {"flagStatus": flag_status}}
            await _graph_request(cfg, "PATCH", f"/me/messages/{message_id}", json_body=payload)
            return f"Email flag set to: {flag_status}"
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    async def m365_delete_email(
        message_id: str = Field(..., description="Email message ID to delete"),
    ) -> str:
        """Delete an email (moves to Deleted Items)."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            await _graph_request(cfg, "DELETE", f"/me/messages/{message_id}")
            return "Email deleted (moved to Deleted Items)."
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def m365_mark_email_read(
        message_id: str = Field(..., description="Email message ID"),
        is_read: bool = Field(True, description="True to mark as read, False to mark as unread"),
    ) -> str:
        """Mark an email as read or unread."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            payload = {"isRead": is_read}
            await _graph_request(cfg, "PATCH", f"/me/messages/{message_id}", json_body=payload)
            return f"Email marked as {'read' if is_read else 'unread'}."
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # CALENDAR Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_list_calendars() -> str:
        """List all calendars for the authenticated user."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            data = await _graph_request(cfg, "GET", "/me/calendars")
            calendars = data.get("value", [])

            if not calendars:
                return "No calendars found."

            lines = ["## Calendars\n"]
            for cal in calendars:
                owner = cal.get("owner", {})
                is_default = " (Default)" if cal.get("isDefaultCalendar") else ""
                color = cal.get("color", "auto")
                lines.append(
                    f"- **{cal.get('name', 'Unknown')}**{is_default}\n"
                    f"  Owner: {owner.get('name', 'N/A')} ({owner.get('address', 'N/A')})\n"
                    f"  Color: {color} | Can Edit: {cal.get('canEdit', False)}\n"
                    f"  ID: `{cal.get('id', '')}`"
                )
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 auth expired. Run m365_auth_start."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_list_events(
        start_date: Optional[str] = Field(None, description="Start date (ISO format, e.g. 2024-01-15). Defaults to today."),
        end_date: Optional[str] = Field(None, description="End date (ISO format). Defaults to 7 days from start."),
        calendar_id: Optional[str] = Field(None, description="Calendar ID. Omit for default calendar."),
        top: int = Field(25, description="Max events to return (1-50)"),
        search: Optional[str] = Field(None, description="Search text in event subject/body"),
    ) -> str:
        """List calendar events within a date range."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            from datetime import date as date_type

            if not start_date:
                start_date = date_type.today().isoformat()
            if not end_date:
                end_dt = datetime.fromisoformat(start_date) + timedelta(days=7)
                end_date = end_dt.strftime("%Y-%m-%d")

            if calendar_id:
                endpoint = f"/me/calendars/{calendar_id}/calendarView"
            else:
                endpoint = "/me/calendarView"

            params = {
                "startDateTime": f"{start_date}T00:00:00Z",
                "endDateTime": f"{end_date}T23:59:59Z",
                "$top": min(top, 50),
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,location,organizer,isAllDay,isCancelled,importance,showAs,recurrence,onlineMeeting,bodyPreview,attendees",
            }
            if search:
                params["$filter"] = f"contains(subject, '{search}')"

            data = await _graph_request(cfg, "GET", endpoint, params=params)
            events = data.get("value", [])

            if not events:
                return f"No events found between {start_date} and {end_date}."

            lines = [f"## Calendar Events ({start_date} to {end_date})\n"]
            for evt in events:
                start = evt.get("start", {})
                end = evt.get("end", {})
                start_str = start.get("dateTime", "")[:16].replace("T", " ")
                end_str = end.get("dateTime", "")[:16].replace("T", " ")
                tz = start.get("timeZone", "")

                location = evt.get("location", {}).get("displayName", "")
                location_str = f"\n  Location: {location}" if location else ""

                organizer = evt.get("organizer", {}).get("emailAddress", {})
                org_str = f"{organizer.get('name', '')}".strip()

                cancelled = " [CANCELLED]" if evt.get("isCancelled") else ""
                all_day = " [All Day]" if evt.get("isAllDay") else ""
                show_as = evt.get("showAs", "busy")

                online = evt.get("onlineMeeting", {})
                meeting_link = ""
                if online and online.get("joinUrl"):
                    meeting_link = f"\n  Teams: {online['joinUrl']}"

                attendee_count = len(evt.get("attendees", []))
                attendees_str = f" | {attendee_count} attendees" if attendee_count > 0 else ""

                lines.append(
                    f"**{evt.get('subject', '(no subject)')}**{cancelled}{all_day}\n"
                    f"  {start_str} — {end_str} ({tz})\n"
                    f"  Status: {show_as} | Organizer: {org_str}{attendees_str}{location_str}{meeting_link}\n"
                    f"  ID: `{evt.get('id', '')[:50]}...`\n"
                )

            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 auth expired. Run m365_auth_start."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_get_event(
        event_id: str = Field(..., description="Calendar event ID"),
    ) -> str:
        """Get full details of a calendar event including attendees."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            evt = await _graph_request(cfg, "GET", f"/me/events/{event_id}")

            start = evt.get("start", {})
            end = evt.get("end", {})

            lines = [f"## {evt.get('subject', '(no subject)')}\n"]
            lines.append(f"**Start:** {start.get('dateTime', '')[:16].replace('T', ' ')} ({start.get('timeZone', '')})")
            lines.append(f"**End:** {end.get('dateTime', '')[:16].replace('T', ' ')} ({end.get('timeZone', '')})")

            location = evt.get("location", {}).get("displayName", "")
            if location:
                lines.append(f"**Location:** {location}")

            organizer = evt.get("organizer", {}).get("emailAddress", {})
            lines.append(f"**Organizer:** {organizer.get('name', '')} <{organizer.get('address', '')}>")
            lines.append(f"**Show As:** {evt.get('showAs', 'busy')}")
            lines.append(f"**Importance:** {evt.get('importance', 'normal')}")
            lines.append(f"**All Day:** {evt.get('isAllDay', False)}")
            lines.append(f"**Cancelled:** {evt.get('isCancelled', False)}")

            if evt.get("recurrence"):
                rec = evt["recurrence"]
                pattern = rec.get("pattern", {})
                lines.append(f"**Recurrence:** {pattern.get('type', 'unknown')} every {pattern.get('interval', 1)} {pattern.get('type', '')}")

            online = evt.get("onlineMeeting", {})
            if online and online.get("joinUrl"):
                lines.append(f"**Teams Meeting:** {online['joinUrl']}")

            attendees = evt.get("attendees", [])
            if attendees:
                lines.append(f"\n### Attendees ({len(attendees)})\n")
                for att in attendees:
                    email = att.get("emailAddress", {})
                    status = att.get("status", {}).get("response", "none")
                    att_type = att.get("type", "required")
                    lines.append(f"- {email.get('name', '')} <{email.get('address', '')}> — {status} ({att_type})")

            body = evt.get("body", {}).get("content", "")
            if body:
                import re
                body = re.sub(r'<[^>]+>', '', body).strip()
                if body:
                    lines.append(f"\n### Description\n\n{body[:2000]}")

            lines.append(f"\n**Event ID:** `{evt.get('id', '')}`")
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_create_event(
        subject: str = Field(..., description="Event subject/title"),
        start_datetime: str = Field(..., description="Start date and time (ISO format, e.g. '2024-01-15T09:00:00')"),
        end_datetime: str = Field(..., description="End date and time (ISO format, e.g. '2024-01-15T10:00:00')"),
        timezone: str = Field("Australia/Sydney", description="Timezone (e.g. 'Australia/Sydney', 'UTC', 'America/New_York')"),
        body: Optional[str] = Field(None, description="Event description/body"),
        location: Optional[str] = Field(None, description="Event location"),
        attendees: Optional[str] = Field(None, description="Attendee email addresses (comma-separated)"),
        is_all_day: bool = Field(False, description="All-day event"),
        is_online_meeting: bool = Field(False, description="Create as Teams online meeting"),
        importance: str = Field("normal", description="Importance: low, normal, high"),
        reminder_minutes: int = Field(15, description="Reminder minutes before event"),
        is_html: bool = Field(False, description="Set to true if body contains HTML"),
        calendar_id: Optional[str] = Field(None, description="Calendar ID. Omit for default calendar."),
    ) -> str:
        """Create a new calendar event."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            event_data = {
                "subject": subject,
                "start": {"dateTime": start_datetime, "timeZone": timezone},
                "end": {"dateTime": end_datetime, "timeZone": timezone},
                "importance": importance,
                "isReminderOn": True,
                "reminderMinutesBeforeStart": reminder_minutes,
            }

            if body:
                event_data["body"] = {
                    "contentType": "HTML" if is_html else "Text",
                    "content": body,
                }
            if location:
                event_data["location"] = {"displayName": location}
            if attendees:
                event_data["attendees"] = [
                    {"emailAddress": {"address": a.strip()}, "type": "required"}
                    for a in attendees.split(",") if a.strip()
                ]
            if is_all_day:
                event_data["isAllDay"] = True
            if is_online_meeting:
                event_data["isOnlineMeeting"] = True
                event_data["onlineMeetingProvider"] = "teamsForBusiness"

            endpoint = f"/me/calendars/{calendar_id}/events" if calendar_id else "/me/events"
            result = await _graph_request(cfg, "POST", endpoint, json_body=event_data)

            meeting_info = ""
            if result.get("onlineMeeting", {}).get("joinUrl"):
                meeting_info = f"\nTeams Link: {result['onlineMeeting']['joinUrl']}"

            return (
                f"Event created: **{subject}**\n"
                f"Start: {start_datetime} ({timezone})\n"
                f"End: {end_datetime}{meeting_info}\n"
                f"Event ID: `{result.get('id', 'N/A')}`"
            )
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_update_event(
        event_id: str = Field(..., description="Event ID to update"),
        subject: Optional[str] = Field(None, description="New subject"),
        start_datetime: Optional[str] = Field(None, description="New start (ISO format)"),
        end_datetime: Optional[str] = Field(None, description="New end (ISO format)"),
        timezone: Optional[str] = Field(None, description="Timezone for start/end"),
        body: Optional[str] = Field(None, description="New body/description"),
        location: Optional[str] = Field(None, description="New location"),
        is_cancelled: Optional[bool] = Field(None, description="Cancel the event"),
    ) -> str:
        """Update an existing calendar event."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            update_data = {}
            if subject is not None:
                update_data["subject"] = subject
            if start_datetime is not None:
                update_data["start"] = {"dateTime": start_datetime, "timeZone": timezone or "Australia/Sydney"}
            if end_datetime is not None:
                update_data["end"] = {"dateTime": end_datetime, "timeZone": timezone or "Australia/Sydney"}
            if body is not None:
                update_data["body"] = {"contentType": "Text", "content": body}
            if location is not None:
                update_data["location"] = {"displayName": location}
            if is_cancelled is not None:
                update_data["isCancelled"] = is_cancelled

            if not update_data:
                return "Error: No fields to update. Provide at least one field."

            result = await _graph_request(cfg, "PATCH", f"/me/events/{event_id}", json_body=update_data)
            return f"Event updated: **{result.get('subject', 'N/A')}**\nEvent ID: `{result.get('id', '')}`"
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    async def m365_delete_event(
        event_id: str = Field(..., description="Calendar event ID to delete"),
    ) -> str:
        """Delete a calendar event."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            await _graph_request(cfg, "DELETE", f"/me/events/{event_id}")
            return "Calendar event deleted."
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_check_availability(
        email_addresses: str = Field(..., description="Email addresses to check (comma-separated)"),
        start_datetime: str = Field(..., description="Start of time range (ISO format, e.g. '2024-01-15T09:00:00')"),
        end_datetime: str = Field(..., description="End of time range (ISO format)"),
        timezone: str = Field("Australia/Sydney", description="Timezone"),
        interval_minutes: int = Field(30, description="Availability interval in minutes"),
    ) -> str:
        """Check availability (free/busy) for one or more people."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            schedules = [a.strip() for a in email_addresses.split(",") if a.strip()]
            payload = {
                "schedules": schedules,
                "startTime": {"dateTime": start_datetime, "timeZone": timezone},
                "endTime": {"dateTime": end_datetime, "timeZone": timezone},
                "availabilityViewInterval": interval_minutes,
            }

            data = await _graph_request(cfg, "POST", "/me/calendar/getSchedule", json_body=payload)
            results = data.get("value", [])

            if not results:
                return "No availability data returned."

            lines = [f"## Availability ({start_datetime} to {end_datetime})\n"]
            status_map = {"0": "Free", "1": "Tentative", "2": "Busy", "3": "Out of Office", "4": "Working Elsewhere"}

            for schedule in results:
                email = schedule.get("scheduleId", "Unknown")
                avail_view = schedule.get("availabilityView", "")
                items = schedule.get("scheduleItems", [])

                lines.append(f"### {email}\n")
                if avail_view:
                    decoded = " ".join(status_map.get(c, c) for c in avail_view)
                    lines.append(f"Availability slots ({interval_minutes}min each): {decoded}\n")

                if items:
                    for item in items:
                        start_t = item.get("start", {}).get("dateTime", "")[:16].replace("T", " ")
                        end_t = item.get("end", {}).get("dateTime", "")[:16].replace("T", " ")
                        status = item.get("status", "unknown")
                        subject = item.get("subject", "")
                        subj_str = f" — {subject}" if subject else ""
                        lines.append(f"- {start_t} to {end_t}: **{status}**{subj_str}")
                else:
                    lines.append("- No scheduled items in this range (free)")

                lines.append("")

            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # ONEDRIVE Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_onedrive_list(
        folder_path: str = Field("/", description="Folder path (e.g. '/', '/Documents', '/Projects/2024'). Use '/' for root."),
        top: int = Field(25, description="Max items to return (1-200)"),
        order_by: str = Field("name", description="Sort by: 'name', 'lastModifiedDateTime', 'size'"),
    ) -> str:
        """List files and folders in OneDrive."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            if folder_path == "/" or folder_path == "":
                endpoint = "/me/drive/root/children"
            else:
                # Remove leading/trailing slashes and encode
                clean_path = folder_path.strip("/")
                endpoint = f"/me/drive/root:/{clean_path}:/children"

            params = {
                "$top": min(top, 200),
                "$orderby": order_by,
                "$select": "id,name,size,lastModifiedDateTime,folder,file,webUrl,parentReference",
            }
            data = await _graph_request(cfg, "GET", endpoint, params=params)
            items = data.get("value", [])

            if not items:
                return f"No items found in {folder_path}."

            lines = [f"## OneDrive: {folder_path}\n"]
            for item in items:
                is_folder = "folder" in item
                icon = "[Folder]" if is_folder else "[File]"
                name = item.get("name", "Unknown")
                size = item.get("size", 0)
                modified = item.get("lastModifiedDateTime", "")[:16].replace("T", " ")

                if is_folder:
                    child_count = item.get("folder", {}).get("childCount", 0)
                    size_str = f"{child_count} items"
                else:
                    if size > 1048576:
                        size_str = f"{size / 1048576:.1f} MB"
                    elif size > 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size} B"

                lines.append(
                    f"- {icon} **{name}** — {size_str} | Modified: {modified}\n"
                    f"  ID: `{item.get('id', '')}`"
                )

            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Error: Folder '{folder_path}' not found."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_onedrive_search(
        query: str = Field(..., description="Search query (searches file names and content)"),
        top: int = Field(25, description="Max results (1-50)"),
    ) -> str:
        """Search for files in OneDrive."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            endpoint = f"/me/drive/root/search(q='{query}')"
            params = {
                "$top": min(top, 50),
                "$select": "id,name,size,lastModifiedDateTime,folder,file,webUrl,parentReference",
            }
            data = await _graph_request(cfg, "GET", endpoint, params=params)
            items = data.get("value", [])

            if not items:
                return f"No files found matching '{query}'."

            lines = [f"## OneDrive Search: '{query}'\n"]
            for item in items:
                is_folder = "folder" in item
                icon = "[Folder]" if is_folder else "[File]"
                name = item.get("name", "Unknown")
                size = item.get("size", 0)
                modified = item.get("lastModifiedDateTime", "")[:16].replace("T", " ")
                parent = item.get("parentReference", {}).get("path", "").replace("/drive/root:", "") or "/"
                web_url = item.get("webUrl", "")

                if size > 1048576:
                    size_str = f"{size / 1048576:.1f} MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} B"

                lines.append(
                    f"- {icon} **{name}** — {size_str} | Modified: {modified}\n"
                    f"  Path: {parent}\n"
                    f"  URL: {web_url}\n"
                    f"  ID: `{item.get('id', '')}`"
                )

            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_onedrive_get_file(
        item_id: Optional[str] = Field(None, description="File item ID"),
        file_path: Optional[str] = Field(None, description="File path (e.g. '/Documents/report.pdf'). Provide either item_id or file_path."),
        get_download_url: bool = Field(True, description="Include a temporary download URL"),
        get_content: bool = Field(False, description="Get file content (only for text files < 1MB)"),
    ) -> str:
        """Get file details, download URL, or content from OneDrive."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        if not item_id and not file_path:
            return "Error: Provide either item_id or file_path."

        try:
            if item_id:
                endpoint = f"/me/drive/items/{item_id}"
            else:
                clean_path = file_path.strip("/")
                endpoint = f"/me/drive/root:/{clean_path}"

            item = await _graph_request(cfg, "GET", endpoint)

            name = item.get("name", "Unknown")
            size = item.get("size", 0)
            modified = item.get("lastModifiedDateTime", "")[:19].replace("T", " ")
            web_url = item.get("webUrl", "")
            mime = item.get("file", {}).get("mimeType", "N/A") if "file" in item else "folder"

            if size > 1048576:
                size_str = f"{size / 1048576:.1f} MB"
            elif size > 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size} B"

            lines = [f"## {name}\n"]
            lines.append(f"**Size:** {size_str}")
            lines.append(f"**Type:** {mime}")
            lines.append(f"**Modified:** {modified}")
            lines.append(f"**Web URL:** {web_url}")
            lines.append(f"**ID:** `{item.get('id', '')}`")

            created_by = item.get("createdBy", {}).get("user", {}).get("displayName", "")
            modified_by = item.get("lastModifiedBy", {}).get("user", {}).get("displayName", "")
            if created_by:
                lines.append(f"**Created By:** {created_by}")
            if modified_by:
                lines.append(f"**Modified By:** {modified_by}")

            if get_download_url and "@microsoft.graph.downloadUrl" in item:
                lines.append(f"\n**Download URL (temporary):** {item['@microsoft.graph.downloadUrl']}")

            if get_content and "file" in item and size < 1048576:
                try:
                    content = await _graph_request(cfg, "GET", f"{endpoint}:/content" if file_path else f"/me/drive/items/{item.get('id')}/content", raw_response=True)
                    lines.append(f"\n### Content\n\n```\n{content[:5000]}\n```")
                    if len(content) > 5000:
                        lines.append(f"\n(Truncated — showing first 5000 of {len(content)} characters)")
                except Exception:
                    lines.append("\n(Could not retrieve file content — binary file or access error)")

            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Error: File not found."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_onedrive_create_folder(
        folder_name: str = Field(..., description="Name of the folder to create"),
        parent_path: str = Field("/", description="Parent folder path (e.g. '/', '/Documents')"),
    ) -> str:
        """Create a new folder in OneDrive."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            if parent_path == "/" or parent_path == "":
                endpoint = "/me/drive/root/children"
            else:
                clean_path = parent_path.strip("/")
                endpoint = f"/me/drive/root:/{clean_path}:/children"

            payload = {
                "name": folder_name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            }
            result = await _graph_request(cfg, "POST", endpoint, json_body=payload)
            return f"Folder created: **{folder_name}** in {parent_path}\nID: `{result.get('id', '')}`\nURL: {result.get('webUrl', '')}"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                return f"Error: Folder '{folder_name}' already exists in {parent_path}."
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_onedrive_upload_text(
        file_name: str = Field(..., description="File name (e.g. 'notes.txt', 'report.md')"),
        content: str = Field(..., description="Text content to upload"),
        folder_path: str = Field("/", description="Destination folder path"),
    ) -> str:
        """Upload a text file to OneDrive (for files up to 4MB)."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            if folder_path == "/" or folder_path == "":
                endpoint = f"/me/drive/root:/{file_name}:/content"
            else:
                clean_path = folder_path.strip("/")
                endpoint = f"/me/drive/root:/{clean_path}/{file_name}:/content"

            token = await cfg.get_access_token()
            url = f"{GRAPH_BASE}{endpoint}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    url,
                    content=content.encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "text/plain",
                    },
                )
                response.raise_for_status()
                result = response.json()

            return f"File uploaded: **{file_name}** to {folder_path}\nSize: {result.get('size', 0)} bytes\nID: `{result.get('id', '')}`\nURL: {result.get('webUrl', '')}"
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True})
    async def m365_onedrive_delete(
        item_id: Optional[str] = Field(None, description="Item ID to delete"),
        file_path: Optional[str] = Field(None, description="File/folder path to delete"),
    ) -> str:
        """Delete a file or folder from OneDrive."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        if not item_id and not file_path:
            return "Error: Provide either item_id or file_path."

        try:
            if item_id:
                endpoint = f"/me/drive/items/{item_id}"
            else:
                clean_path = file_path.strip("/")
                endpoint = f"/me/drive/root:/{clean_path}"

            await _graph_request(cfg, "DELETE", endpoint)
            target = file_path or item_id
            return f"Deleted: {target}"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return "Error: Item not found."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # TEAMS Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_teams_list() -> str:
        """List all Microsoft Teams the user is a member of."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            data = await _graph_request(cfg, "GET", "/me/joinedTeams")
            teams = data.get("value", [])

            if not teams:
                return "No Teams found."

            lines = ["## My Teams\n"]
            for team in teams:
                desc = team.get("description", "") or ""
                desc_str = f"\n  {desc[:100]}" if desc else ""
                lines.append(
                    f"- **{team.get('displayName', 'Unknown')}**{desc_str}\n"
                    f"  ID: `{team.get('id', '')}`"
                )
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 auth expired. Run m365_auth_start."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_teams_list_channels(
        team_id: str = Field(..., description="Team ID"),
    ) -> str:
        """List channels in a Microsoft Team."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            data = await _graph_request(cfg, "GET", f"/teams/{team_id}/channels")
            channels = data.get("value", [])

            if not channels:
                return "No channels found."

            lines = ["## Team Channels\n"]
            for ch in channels:
                desc = ch.get("description", "") or ""
                desc_str = f" — {desc[:80]}" if desc else ""
                membership = ch.get("membershipType", "standard")
                lines.append(
                    f"- **{ch.get('displayName', 'Unknown')}** ({membership}){desc_str}\n"
                    f"  ID: `{ch.get('id', '')}`"
                )
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_teams_list_channel_messages(
        team_id: str = Field(..., description="Team ID"),
        channel_id: str = Field(..., description="Channel ID"),
        top: int = Field(20, description="Number of messages to retrieve (1-50)"),
    ) -> str:
        """Read recent messages from a Teams channel."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            params = {"$top": min(top, 50), "$orderby": "createdDateTime desc"}
            data = await _graph_request(cfg, "GET", f"/teams/{team_id}/channels/{channel_id}/messages", params=params)
            messages = data.get("value", [])

            if not messages:
                return "No messages in this channel."

            lines = ["## Channel Messages\n"]
            for msg in messages:
                sender = msg.get("from", {})
                user = sender.get("user", {}) if sender else {}
                sender_name = user.get("displayName", "Unknown") if user else "System"
                created = msg.get("createdDateTime", "")[:16].replace("T", " ")

                body = msg.get("body", {}).get("content", "")
                body_type = msg.get("body", {}).get("contentType", "text")
                if body_type == "html":
                    import re
                    body = re.sub(r'<[^>]+>', '', body).strip()
                body_preview = body[:200] if body else "(empty)"

                msg_type = msg.get("messageType", "message")
                type_badge = f" [{msg_type}]" if msg_type != "message" else ""

                attachments = msg.get("attachments", [])
                attach_str = f" [+{len(attachments)} attachments]" if attachments else ""

                lines.append(
                    f"**{sender_name}**{type_badge} — {created}{attach_str}\n"
                    f"  {body_preview}\n"
                    f"  ID: `{msg.get('id', '')}`\n"
                )
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                return "Error: Insufficient permissions to read channel messages. The ChannelMessage.Read.All permission may be needed."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_teams_send_channel_message(
        team_id: str = Field(..., description="Team ID"),
        channel_id: str = Field(..., description="Channel ID"),
        message: str = Field(..., description="Message content (plain text or HTML)"),
        is_html: bool = Field(False, description="Set to true if message is HTML"),
        importance: str = Field("normal", description="Message importance: normal, urgent"),
    ) -> str:
        """Send a message to a Teams channel."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            payload = {
                "body": {
                    "contentType": "html" if is_html else "text",
                    "content": message,
                },
                "importance": importance,
            }
            result = await _graph_request(cfg, "POST", f"/teams/{team_id}/channels/{channel_id}/messages", json_body=payload)
            return f"Message sent to channel.\nMessage ID: `{result.get('id', '')}`"
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_teams_list_chats(
        top: int = Field(20, description="Number of chats to list (1-50)"),
    ) -> str:
        """List recent Teams chats (1:1 and group chats)."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            params = {
                "$top": min(top, 50),
                "$orderby": "lastUpdatedDateTime desc",
                "$expand": "members",
            }
            data = await _graph_request(cfg, "GET", "/me/chats", params=params)
            chats = data.get("value", [])

            if not chats:
                return "No chats found."

            lines = ["## Teams Chats\n"]
            for chat in chats:
                chat_type = chat.get("chatType", "unknown")
                topic = chat.get("topic", "")
                updated = chat.get("lastUpdatedDateTime", "")[:16].replace("T", " ")

                members = chat.get("members", [])
                member_names = [m.get("displayName", "Unknown") for m in members[:5]]
                members_str = ", ".join(member_names)
                if len(members) > 5:
                    members_str += f" +{len(members) - 5} more"

                title = topic or members_str or "Chat"

                lines.append(
                    f"- **{title}** ({chat_type})\n"
                    f"  Last updated: {updated}\n"
                    f"  Members: {members_str}\n"
                    f"  ID: `{chat.get('id', '')}`"
                )
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: M365 auth expired. Run m365_auth_start."
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_teams_list_chat_messages(
        chat_id: str = Field(..., description="Chat ID"),
        top: int = Field(20, description="Number of messages to retrieve (1-50)"),
    ) -> str:
        """Read recent messages from a Teams chat."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            params = {"$top": min(top, 50), "$orderby": "createdDateTime desc"}
            data = await _graph_request(cfg, "GET", f"/me/chats/{chat_id}/messages", params=params)
            messages = data.get("value", [])

            if not messages:
                return "No messages in this chat."

            lines = ["## Chat Messages\n"]
            for msg in messages:
                sender = msg.get("from", {})
                user = sender.get("user", {}) if sender else {}
                sender_name = user.get("displayName", "Unknown") if user else "System"
                created = msg.get("createdDateTime", "")[:16].replace("T", " ")

                body = msg.get("body", {}).get("content", "")
                body_type = msg.get("body", {}).get("contentType", "text")
                if body_type == "html":
                    import re
                    body = re.sub(r'<[^>]+>', '', body).strip()
                body_preview = body[:200] if body else "(empty)"

                lines.append(
                    f"**{sender_name}** — {created}\n"
                    f"  {body_preview}\n"
                    f"  ID: `{msg.get('id', '')}`\n"
                )
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_teams_send_chat_message(
        chat_id: str = Field(..., description="Chat ID"),
        message: str = Field(..., description="Message content"),
        is_html: bool = Field(False, description="Set to true if message is HTML"),
    ) -> str:
        """Send a message in a Teams chat."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            payload = {
                "body": {
                    "contentType": "html" if is_html else "text",
                    "content": message,
                },
            }
            result = await _graph_request(cfg, "POST", f"/me/chats/{chat_id}/messages", json_body=payload)
            return f"Chat message sent.\nMessage ID: `{result.get('id', '')}`"
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"

    # =========================================================================
    # CONTACTS & PEOPLE Tools
    # =========================================================================

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_list_contacts(
        top: int = Field(25, description="Max contacts to return (1-100)"),
        search: Optional[str] = Field(None, description="Search contacts by name or email"),
        folder_id: Optional[str] = Field(None, description="Contact folder ID (omit for default)"),
    ) -> str:
        """List Outlook contacts."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            if folder_id:
                endpoint = f"/me/contactFolders/{folder_id}/contacts"
            else:
                endpoint = "/me/contacts"

            params = {
                "$top": min(top, 100),
                "$orderby": "displayName",
                "$select": "id,displayName,emailAddresses,businessPhones,mobilePhone,companyName,jobTitle,department",
            }
            if search:
                params["$filter"] = f"startswith(displayName, '{search}') or startswith(givenName, '{search}') or startswith(surname, '{search}')"

            data = await _graph_request(cfg, "GET", endpoint, params=params)
            contacts = data.get("value", [])

            if not contacts:
                return "No contacts found."

            lines = ["## Contacts\n"]
            for c in contacts:
                name = c.get("displayName", "Unknown")
                emails = c.get("emailAddresses", [])
                email_str = ", ".join(e.get("address", "") for e in emails[:3]) if emails else "N/A"
                company = c.get("companyName", "")
                title = c.get("jobTitle", "")
                phone = c.get("mobilePhone", "") or (c.get("businessPhones", [None])[0] if c.get("businessPhones") else "")

                details = []
                if company:
                    details.append(company)
                if title:
                    details.append(title)
                detail_str = f" — {', '.join(details)}" if details else ""
                phone_str = f"\n  Phone: {phone}" if phone else ""

                lines.append(
                    f"- **{name}**{detail_str}\n"
                    f"  Email: {email_str}{phone_str}\n"
                    f"  ID: `{c.get('id', '')}`"
                )
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def m365_search_people(
        query: str = Field(..., description="Search query (name, email, etc.)"),
        top: int = Field(10, description="Max results (1-25)"),
    ) -> str:
        """Search for people in the organization directory (including recent contacts)."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            params = {"$search": f'"{query}"', "$top": min(top, 25)}
            data = await _graph_request(cfg, "GET", "/me/people", params=params)
            people = data.get("value", [])

            if not people:
                return f"No people found matching '{query}'."

            lines = [f"## People Search: '{query}'\n"]
            for p in people:
                name = p.get("displayName", "Unknown")
                emails = p.get("scoredEmailAddresses", [])
                email = emails[0].get("address", "N/A") if emails else "N/A"
                company = p.get("companyName", "")
                title = p.get("jobTitle", "")
                dept = p.get("department", "")

                details = []
                if title:
                    details.append(title)
                if dept:
                    details.append(dept)
                if company:
                    details.append(company)
                detail_str = f" — {', '.join(details)}" if details else ""

                lines.append(f"- **{name}**{detail_str}\n  Email: {email}")
            return "\n".join(lines)
        except httpx.HTTPStatusError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})
    async def m365_create_contact(
        given_name: str = Field(..., description="First name"),
        surname: Optional[str] = Field(None, description="Last name"),
        email: Optional[str] = Field(None, description="Email address"),
        mobile_phone: Optional[str] = Field(None, description="Mobile phone number"),
        business_phone: Optional[str] = Field(None, description="Business phone number"),
        company_name: Optional[str] = Field(None, description="Company name"),
        job_title: Optional[str] = Field(None, description="Job title"),
        department: Optional[str] = Field(None, description="Department"),
    ) -> str:
        """Create a new Outlook contact."""
        cfg = config_getter()
        if not cfg or not cfg.is_configured:
            return "Error: M365 not configured. Run m365_auth_start to connect."

        try:
            contact_data = {"givenName": given_name}
            if surname:
                contact_data["surname"] = surname
            if email:
                contact_data["emailAddresses"] = [{"address": email, "name": f"{given_name} {surname or ''}".strip()}]
            if mobile_phone:
                contact_data["mobilePhone"] = mobile_phone
            if business_phone:
                contact_data["businessPhones"] = [business_phone]
            if company_name:
                contact_data["companyName"] = company_name
            if job_title:
                contact_data["jobTitle"] = job_title
            if department:
                contact_data["department"] = department

            result = await _graph_request(cfg, "POST", "/me/contacts", json_body=contact_data)
            return f"Contact created: **{result.get('displayName', given_name)}**\nID: `{result.get('id', '')}`"
        except httpx.HTTPStatusError as e:
            return f"Error: {e.response.status_code} - {e.response.text[:500]}"
        except Exception as e:
            return f"Error: {str(e)}"
