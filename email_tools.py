"""
Microsoft Graph Email Integration Tools for Crowd IT MCP Server

This module provides comprehensive email management capabilities using the
Microsoft Graph API, designed for AI agent-driven email triage and prioritisation.

Capabilities:
- List/search/read emails from any mailbox folder
- AI-friendly email prioritisation with metadata extraction
- Move, flag, categorize, and archive emails
- Send emails and reply to threads
- Manage folders

Authentication: Uses OAuth2 client_credentials flow with a dedicated Azure AD
app registration. Requires Mail.ReadWrite and Mail.Send application permissions
(NOT delegated) granted with admin consent.

Environment Variables:
    EMAIL_TENANT_ID: Azure AD tenant ID (Crowd IT tenant)
    EMAIL_CLIENT_ID: Azure AD Application (client) ID for email access
    EMAIL_CLIENT_SECRET: Azure AD Application client secret
    EMAIL_USER_ID: Default mailbox to access (e.g., chris@crowdit.com.au)
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration and Authentication
# =============================================================================

class EmailConfig:
    """Microsoft Graph Email configuration using OAuth2 client_credentials flow."""

    def __init__(self):
        self.tenant_id = os.getenv("EMAIL_TENANT_ID", "")
        self.client_id = os.getenv("EMAIL_CLIENT_ID", "")
        self._client_secret: Optional[str] = None
        self.default_user_id = os.getenv("EMAIL_USER_ID", "")  # e.g., chris@crowdit.com.au
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self.graph_base_url = "https://graph.microsoft.com/v1.0"

    @property
    def client_secret(self) -> str:
        if self._client_secret:
            return self._client_secret

        # Try Secret Manager first
        try:
            from app.core.config import get_secret_sync
            secret = get_secret_sync("EMAIL_CLIENT_SECRET")
            if secret:
                self._client_secret = secret
                return secret
        except Exception:
            pass

        self._client_secret = os.getenv("EMAIL_CLIENT_SECRET", "")
        return self._client_secret

    @property
    def is_configured(self) -> bool:
        return all([self.tenant_id, self.client_id, self.client_secret, self.default_user_id])

    async def get_access_token(self) -> str:
        """Get access token using client_credentials flow."""
        if self._access_token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._access_token

        import httpx

        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://graph.microsoft.com/.default"
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            data = response.json()

            self._access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            return self._access_token

    async def graph_request(self, method: str, endpoint: str, user_id: str = None,
                            params: dict = None, json_body: dict = None) -> Any:
        """Make a Microsoft Graph API request."""
        import httpx

        token = await self.get_access_token()
        uid = user_id or self.default_user_id
        url = f"{self.graph_base_url}/users/{uid}{endpoint}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method,
                url=url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Prefer": 'outlook.body-content-type="text"'
                },
                params=params,
                json=json_body
            )
            response.raise_for_status()

            if response.status_code == 204:
                return {"status": "success"}
            return response.json()


# =============================================================================
# Helper Functions
# =============================================================================

def format_email_summary(msg: dict, include_body: bool = False) -> dict:
    """Format a Graph API message into a clean summary for AI processing."""
    result = {
        "id": msg.get("id", ""),
        "subject": msg.get("subject", "(no subject)"),
        "from": _format_recipient(msg.get("from", {})),
        "to": [_format_recipient(r) for r in msg.get("toRecipients", [])],
        "received": msg.get("receivedDateTime", ""),
        "is_read": msg.get("isRead", False),
        "importance": msg.get("importance", "normal"),
        "flag": msg.get("flag", {}).get("flagStatus", "notFlagged"),
        "has_attachments": msg.get("hasAttachments", False),
        "categories": msg.get("categories", []),
        "preview": msg.get("bodyPreview", "")[:200],
        "conversation_id": msg.get("conversationId", ""),
        "is_reply": bool(msg.get("subject", "").lower().startswith("re:")),
        "is_forward": bool(msg.get("subject", "").lower().startswith("fw:")),
    }

    if include_body:
        body = msg.get("body", {})
        result["body"] = body.get("content", "")
        result["body_type"] = body.get("contentType", "text")

    # Extract cc
    cc = msg.get("ccRecipients", [])
    if cc:
        result["cc"] = [_format_recipient(r) for r in cc]

    return result


def _format_recipient(recipient: dict) -> str:
    """Format a Graph API recipient into a readable string."""
    email_addr = recipient.get("emailAddress", {})
    name = email_addr.get("name", "")
    address = email_addr.get("address", "")
    if name and address:
        return f"{name} <{address}>"
    return address or name or "unknown"


# =============================================================================
# Tool Registration
# =============================================================================

def register_email_tools(mcp, email_config: 'EmailConfig'):
    """Register all email tools with the MCP server."""

    # =========================================================================
    # INBOX & MESSAGE LISTING
    # =========================================================================

    @mcp.tool(
        name="email_list_inbox",
        annotations={
            "title": "List Inbox Emails",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_list_inbox(
        top: int = 25,
        skip: int = 0,
        unread_only: bool = False,
        importance: str = "",
        folder: str = "inbox",
        user_id: str = ""
    ) -> str:
        """List emails from a mailbox folder with optional filters.

        Args:
            top: Number of emails to return (max 50, default 25)
            skip: Number of emails to skip for pagination
            unread_only: If True, only return unread emails
            importance: Filter by importance: "high", "normal", or "low"
            folder: Mail folder - "inbox", "sentitems", "drafts", "deleteditems", "archive", or a folder ID
            user_id: Override default mailbox (e.g., another user's email)

        Returns a list of email summaries sorted by received date (newest first),
        optimised for AI triage and prioritisation.
        """
        if not email_config.is_configured:
            return "❌ Email not configured. Set EMAIL_TENANT_ID, EMAIL_CLIENT_ID, EMAIL_CLIENT_SECRET, and EMAIL_USER_ID."

        try:
            top = min(top, 50)

            # Build filter
            filters = []
            if unread_only:
                filters.append("isRead eq false")
            if importance:
                filters.append(f"importance eq '{importance}'")

            params = {
                "$top": top,
                "$skip": skip,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,isRead,importance,flag,hasAttachments,categories,bodyPreview,conversationId"
            }
            if filters:
                params["$filter"] = " and ".join(filters)

            result = await email_config.graph_request(
                "GET", f"/mailFolders/{folder}/messages",
                user_id=user_id or None,
                params=params
            )

            messages = result.get("value", [])
            formatted = [format_email_summary(msg) for msg in messages]

            total_hint = f" (showing {skip+1}-{skip+len(formatted)})" if skip > 0 else ""
            summary = f"📧 {len(formatted)} emails from {folder}{total_hint}\n\n"
            return summary + json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error listing emails: {e}"

    @mcp.tool(
        name="email_search",
        annotations={
            "title": "Search Emails",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_search(
        query: str,
        top: int = 20,
        user_id: str = ""
    ) -> str:
        """Search emails across all folders using Microsoft Search.

        Args:
            query: Search query - supports KQL syntax. Examples:
                - "from:john@example.com" - emails from a specific person
                - "subject:invoice" - emails with invoice in subject
                - "hasAttachment:true" - emails with attachments
                - "received:2024-01-01..2024-01-31" - date range
                - "VPN issue" - full text search
                - "from:vision importance:high" - combined filters
            top: Max results (default 20, max 50)
            user_id: Override default mailbox

        Returns matching emails sorted by relevance.
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            params = {
                "$search": f'"{query}"',
                "$top": min(top, 50),
                "$select": "id,subject,from,toRecipients,receivedDateTime,isRead,importance,flag,hasAttachments,categories,bodyPreview,conversationId"
            }

            result = await email_config.graph_request(
                "GET", "/messages",
                user_id=user_id or None,
                params=params
            )

            messages = result.get("value", [])
            formatted = [format_email_summary(msg) for msg in messages]
            return f"🔍 {len(formatted)} results for '{query}'\n\n" + json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error searching emails: {e}"

    # =========================================================================
    # READ EMAIL
    # =========================================================================

    @mcp.tool(
        name="email_get_message",
        annotations={
            "title": "Get Email Message",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_get_message(
        message_id: str,
        user_id: str = ""
    ) -> str:
        """Get the full content of a specific email by its ID.

        Args:
            message_id: The email message ID (from email_list_inbox or email_search)
            user_id: Override default mailbox

        Returns full email content including body text, all recipients, and metadata.
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            result = await email_config.graph_request(
                "GET", f"/messages/{message_id}",
                user_id=user_id or None,
                params={
                    "$select": "id,subject,from,toRecipients,ccRecipients,bccRecipients,replyTo,receivedDateTime,sentDateTime,isRead,importance,flag,hasAttachments,categories,body,bodyPreview,conversationId,internetMessageHeaders,webLink"
                }
            )

            formatted = format_email_summary(result, include_body=True)
            formatted["web_link"] = result.get("webLink", "")
            formatted["sent"] = result.get("sentDateTime", "")
            formatted["reply_to"] = [_format_recipient(r) for r in result.get("replyTo", [])]

            return json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error getting email: {e}"

    @mcp.tool(
        name="email_get_thread",
        annotations={
            "title": "Get Email Thread/Conversation",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_get_thread(
        conversation_id: str,
        top: int = 20,
        user_id: str = ""
    ) -> str:
        """Get all emails in a conversation thread.

        Args:
            conversation_id: The conversation ID (from email_list_inbox results)
            top: Max messages to return (default 20)
            user_id: Override default mailbox

        Returns all messages in the thread sorted chronologically.
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            params = {
                "$filter": f"conversationId eq '{conversation_id}'",
                "$top": min(top, 50),
                "$orderby": "receivedDateTime asc",
                "$select": "id,subject,from,toRecipients,receivedDateTime,isRead,importance,body,bodyPreview"
            }

            result = await email_config.graph_request(
                "GET", "/messages",
                user_id=user_id or None,
                params=params
            )

            messages = result.get("value", [])
            formatted = [format_email_summary(msg, include_body=True) for msg in messages]
            return f"📧 Thread: {len(formatted)} messages\n\n" + json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error getting thread: {e}"

    # =========================================================================
    # TRIAGE & PRIORITISATION
    # =========================================================================

    @mcp.tool(
        name="email_triage_inbox",
        annotations={
            "title": "Triage Inbox for AI Prioritisation",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_triage_inbox(
        hours_back: int = 24,
        user_id: str = ""
    ) -> str:
        """Get unread emails with context optimised for AI prioritisation.

        Fetches unread emails from the last N hours with enhanced metadata
        to help an AI agent categorise and prioritise them. Includes sender
        domain analysis, thread detection, and age indicators.

        Args:
            hours_back: Look back period in hours (default 24, max 168/1 week)
            user_id: Override default mailbox

        Returns structured data designed for AI-driven triage:
        - Email metadata and preview
        - Sender domain (to identify client vs vendor vs internal)
        - Age in hours (urgency indicator)
        - Thread depth hint (ongoing conversation vs new)
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            hours_back = min(hours_back, 168)
            since = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")

            params = {
                "$filter": f"isRead eq false and receivedDateTime ge {since}",
                "$top": 50,
                "$orderby": "receivedDateTime desc",
                "$select": "id,subject,from,toRecipients,ccRecipients,receivedDateTime,importance,flag,hasAttachments,categories,bodyPreview,conversationId"
            }

            result = await email_config.graph_request(
                "GET", "/mailFolders/inbox/messages",
                user_id=user_id or None,
                params=params
            )

            messages = result.get("value", [])
            now = datetime.utcnow()
            triage_items = []

            for msg in messages:
                from_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "")
                from_domain = from_addr.split("@")[-1] if "@" in from_addr else ""

                received_str = msg.get("receivedDateTime", "")
                try:
                    received_dt = datetime.fromisoformat(received_str.replace("Z", "+00:00"))
                    age_hours = round((now - received_dt.replace(tzinfo=None)).total_seconds() / 3600, 1)
                except Exception:
                    age_hours = None

                item = format_email_summary(msg)
                item["sender_domain"] = from_domain
                item["age_hours"] = age_hours
                triage_items.append(item)

            # Group by domain for context
            domains = {}
            for item in triage_items:
                d = item.get("sender_domain", "unknown")
                domains[d] = domains.get(d, 0) + 1

            summary = {
                "total_unread": len(triage_items),
                "period_hours": hours_back,
                "high_importance_count": sum(1 for i in triage_items if i.get("importance") == "high"),
                "flagged_count": sum(1 for i in triage_items if i.get("flag") == "flagged"),
                "with_attachments": sum(1 for i in triage_items if i.get("has_attachments")),
                "sender_domains": domains,
                "emails": triage_items
            }

            return json.dumps(summary, indent=2, default=str)

        except Exception as e:
            return f"❌ Error triaging inbox: {e}"

    # =========================================================================
    # EMAIL ACTIONS
    # =========================================================================

    @mcp.tool(
        name="email_mark_read",
        annotations={
            "title": "Mark Email as Read/Unread",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_mark_read(
        message_id: str,
        is_read: bool = True,
        user_id: str = ""
    ) -> str:
        """Mark an email as read or unread.

        Args:
            message_id: The email message ID
            is_read: True to mark as read, False for unread (default True)
            user_id: Override default mailbox
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            await email_config.graph_request(
                "PATCH", f"/messages/{message_id}",
                user_id=user_id or None,
                json_body={"isRead": is_read}
            )
            status = "read" if is_read else "unread"
            return f"✅ Email marked as {status}"
        except Exception as e:
            return f"❌ Error: {e}"

    @mcp.tool(
        name="email_flag",
        annotations={
            "title": "Flag/Unflag Email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_flag(
        message_id: str,
        flag_status: str = "flagged",
        user_id: str = ""
    ) -> str:
        """Flag or unflag an email for follow-up.

        Args:
            message_id: The email message ID
            flag_status: "flagged", "complete", or "notFlagged"
            user_id: Override default mailbox
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            await email_config.graph_request(
                "PATCH", f"/messages/{message_id}",
                user_id=user_id or None,
                json_body={"flag": {"flagStatus": flag_status}}
            )
            return f"✅ Email flag set to: {flag_status}"
        except Exception as e:
            return f"❌ Error: {e}"

    @mcp.tool(
        name="email_categorize",
        annotations={
            "title": "Categorize Email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_categorize(
        message_id: str,
        categories: str = "",
        user_id: str = ""
    ) -> str:
        """Set categories/labels on an email. Great for AI-driven classification.

        Args:
            message_id: The email message ID
            categories: Comma-separated category names (e.g., "Urgent,Client,VisionRad")
                       Use empty string to clear all categories.
            user_id: Override default mailbox

        Note: Categories must exist in Outlook. Common ones: Red/Orange/Yellow/Green/Blue/Purple category.
        Custom categories can be created in Outlook settings.
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else []
            await email_config.graph_request(
                "PATCH", f"/messages/{message_id}",
                user_id=user_id or None,
                json_body={"categories": cat_list}
            )
            return f"✅ Categories set: {cat_list if cat_list else '(cleared)'}"
        except Exception as e:
            return f"❌ Error: {e}"

    @mcp.tool(
        name="email_move",
        annotations={
            "title": "Move Email to Folder",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_move(
        message_id: str,
        destination_folder: str,
        user_id: str = ""
    ) -> str:
        """Move an email to a different folder.

        Args:
            message_id: The email message ID
            destination_folder: Folder name or ID. Well-known names:
                "inbox", "drafts", "sentitems", "deleteditems",
                "archive", "junkemail"
                Or use a folder ID from email_list_folders.
            user_id: Override default mailbox
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            result = await email_config.graph_request(
                "POST", f"/messages/{message_id}/move",
                user_id=user_id or None,
                json_body={"destinationId": destination_folder}
            )
            return f"✅ Email moved to {destination_folder}"
        except Exception as e:
            return f"❌ Error moving email: {e}"

    @mcp.tool(
        name="email_batch_action",
        annotations={
            "title": "Batch Email Actions",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def email_batch_action(
        message_ids: str,
        action: str,
        value: str = "",
        user_id: str = ""
    ) -> str:
        """Perform an action on multiple emails at once.

        Args:
            message_ids: Comma-separated list of message IDs
            action: One of: "mark_read", "mark_unread", "flag", "unflag",
                   "move", "categorize", "archive"
            value: Additional value depending on action:
                - For "move": destination folder name/ID
                - For "categorize": comma-separated categories
                - For others: not needed
            user_id: Override default mailbox

        Returns summary of successes and failures.
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            ids = [mid.strip() for mid in message_ids.split(",") if mid.strip()]
            uid = user_id or None
            success = 0
            errors = []

            for mid in ids:
                try:
                    if action == "mark_read":
                        await email_config.graph_request("PATCH", f"/messages/{mid}", user_id=uid, json_body={"isRead": True})
                    elif action == "mark_unread":
                        await email_config.graph_request("PATCH", f"/messages/{mid}", user_id=uid, json_body={"isRead": False})
                    elif action == "flag":
                        await email_config.graph_request("PATCH", f"/messages/{mid}", user_id=uid, json_body={"flag": {"flagStatus": "flagged"}})
                    elif action == "unflag":
                        await email_config.graph_request("PATCH", f"/messages/{mid}", user_id=uid, json_body={"flag": {"flagStatus": "notFlagged"}})
                    elif action == "move":
                        await email_config.graph_request("POST", f"/messages/{mid}/move", user_id=uid, json_body={"destinationId": value})
                    elif action == "categorize":
                        cats = [c.strip() for c in value.split(",") if c.strip()]
                        await email_config.graph_request("PATCH", f"/messages/{mid}", user_id=uid, json_body={"categories": cats})
                    elif action == "archive":
                        await email_config.graph_request("POST", f"/messages/{mid}/move", user_id=uid, json_body={"destinationId": "archive"})
                    else:
                        errors.append(f"Unknown action: {action}")
                        break
                    success += 1
                except Exception as e:
                    errors.append(f"{mid[:8]}...: {str(e)[:80]}")

            result = f"✅ {success}/{len(ids)} emails processed ({action})"
            if errors:
                result += f"\n⚠️ Errors:\n" + "\n".join(errors)
            return result

        except Exception as e:
            return f"❌ Error: {e}"

    # =========================================================================
    # SEND & REPLY
    # =========================================================================

    @mcp.tool(
        name="email_send",
        annotations={
            "title": "Send Email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def email_send(
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        importance: str = "normal",
        user_id: str = ""
    ) -> str:
        """Send a new email.

        Args:
            to: Comma-separated recipient email addresses
            subject: Email subject line
            body: Email body (plain text)
            cc: Comma-separated CC addresses (optional)
            importance: "low", "normal", or "high"
            user_id: Send from a different mailbox (default: EMAIL_USER_ID)

        Returns confirmation with message details.
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            to_recipients = [
                {"emailAddress": {"address": addr.strip()}}
                for addr in to.split(",") if addr.strip()
            ]

            cc_recipients = [
                {"emailAddress": {"address": addr.strip()}}
                for addr in cc.split(",") if addr.strip()
            ] if cc else []

            message = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "Text",
                        "content": body
                    },
                    "toRecipients": to_recipients,
                    "importance": importance
                },
                "saveToSentItems": True
            }

            if cc_recipients:
                message["message"]["ccRecipients"] = cc_recipients

            await email_config.graph_request(
                "POST", "/sendMail",
                user_id=user_id or None,
                json_body=message
            )

            return f"✅ Email sent to {to} | Subject: {subject}"

        except Exception as e:
            return f"❌ Error sending email: {e}"

    @mcp.tool(
        name="email_reply",
        annotations={
            "title": "Reply to Email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def email_reply(
        message_id: str,
        body: str,
        reply_all: bool = False,
        user_id: str = ""
    ) -> str:
        """Reply to an existing email.

        Args:
            message_id: The email message ID to reply to
            body: Reply body text
            reply_all: If True, reply to all recipients (default False)
            user_id: Override default mailbox
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            endpoint = f"/messages/{message_id}/replyAll" if reply_all else f"/messages/{message_id}/reply"
            await email_config.graph_request(
                "POST", endpoint,
                user_id=user_id or None,
                json_body={
                    "comment": body
                }
            )
            action = "Reply all" if reply_all else "Reply"
            return f"✅ {action} sent"

        except Exception as e:
            return f"❌ Error replying: {e}"

    @mcp.tool(
        name="email_forward",
        annotations={
            "title": "Forward Email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def email_forward(
        message_id: str,
        to: str,
        comment: str = "",
        user_id: str = ""
    ) -> str:
        """Forward an email to someone.

        Args:
            message_id: The email message ID to forward
            to: Comma-separated recipient email addresses
            comment: Optional message to include above the forwarded email
            user_id: Override default mailbox
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            to_recipients = [
                {"emailAddress": {"address": addr.strip()}}
                for addr in to.split(",") if addr.strip()
            ]

            await email_config.graph_request(
                "POST", f"/messages/{message_id}/forward",
                user_id=user_id or None,
                json_body={
                    "comment": comment,
                    "toRecipients": to_recipients
                }
            )

            return f"✅ Email forwarded to {to}"

        except Exception as e:
            return f"❌ Error forwarding: {e}"

    # =========================================================================
    # FOLDER MANAGEMENT
    # =========================================================================

    @mcp.tool(
        name="email_list_folders",
        annotations={
            "title": "List Mail Folders",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_list_folders(
        user_id: str = ""
    ) -> str:
        """List all mail folders with unread counts.

        Args:
            user_id: Override default mailbox

        Returns folder names, IDs, and unread/total counts.
        Useful for understanding mailbox structure and finding folder IDs.
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            result = await email_config.graph_request(
                "GET", "/mailFolders",
                user_id=user_id or None,
                params={
                    "$top": 100,
                    "$select": "id,displayName,totalItemCount,unreadItemCount,childFolderCount"
                }
            )

            folders = result.get("value", [])
            formatted = []
            for f in folders:
                formatted.append({
                    "id": f.get("id", ""),
                    "name": f.get("displayName", ""),
                    "total": f.get("totalItemCount", 0),
                    "unread": f.get("unreadItemCount", 0),
                    "has_subfolders": f.get("childFolderCount", 0) > 0
                })

            return json.dumps(formatted, indent=2)

        except Exception as e:
            return f"❌ Error listing folders: {e}"

    @mcp.tool(
        name="email_create_folder",
        annotations={
            "title": "Create Mail Folder",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def email_create_folder(
        display_name: str,
        parent_folder_id: str = "",
        user_id: str = ""
    ) -> str:
        """Create a new mail folder.

        Args:
            display_name: Name for the new folder
            parent_folder_id: Optional parent folder ID for nested folders.
                            If empty, creates in the mailbox root.
            user_id: Override default mailbox
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            if parent_folder_id:
                endpoint = f"/mailFolders/{parent_folder_id}/childFolders"
            else:
                endpoint = "/mailFolders"

            result = await email_config.graph_request(
                "POST", endpoint,
                user_id=user_id or None,
                json_body={"displayName": display_name}
            )
            return f"✅ Folder created: {display_name} (ID: {result.get('id', 'unknown')})"

        except Exception as e:
            return f"❌ Error creating folder: {e}"

    # =========================================================================
    # ATTACHMENTS
    # =========================================================================

    @mcp.tool(
        name="email_list_attachments",
        annotations={
            "title": "List Email Attachments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def email_list_attachments(
        message_id: str,
        user_id: str = ""
    ) -> str:
        """List attachments on an email (names, sizes, types - not content).

        Args:
            message_id: The email message ID
            user_id: Override default mailbox

        Returns attachment metadata (not the actual file content).
        """
        if not email_config.is_configured:
            return "❌ Email not configured."

        try:
            result = await email_config.graph_request(
                "GET", f"/messages/{message_id}/attachments",
                user_id=user_id or None,
                params={"$select": "id,name,contentType,size,isInline"}
            )

            attachments = result.get("value", [])
            formatted = []
            for att in attachments:
                size_kb = round(att.get("size", 0) / 1024, 1)
                formatted.append({
                    "id": att.get("id", ""),
                    "name": att.get("name", ""),
                    "type": att.get("contentType", ""),
                    "size_kb": size_kb,
                    "is_inline": att.get("isInline", False)
                })

            return json.dumps(formatted, indent=2)

        except Exception as e:
            return f"❌ Error listing attachments: {e}"

    print("✅ Email tools registered successfully")
