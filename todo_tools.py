"""
Microsoft To Do Integration Tools for Crowd IT MCP Server

This module provides Microsoft To Do task management capabilities using the
Microsoft Graph API, designed for AI agent-driven task triage and planning.

Capabilities:
- List/create/delete Microsoft To Do task lists
- List, get, create, update, complete, delete tasks
- Cross-list search by title/body text
- Well-known list aliases ("tasks" -> defaultList, "flagged" -> flaggedEmails)

Authentication: Reuses the EmailConfig from email_tools.py which uses
OAuth2 client_credentials flow with Azure AD. Requires Tasks.ReadWrite
application permission (NOT delegated) granted with admin consent.

If you see 403s after deploy, grant Tasks.ReadWrite in Entra admin consent
alongside the existing Mail.ReadWrite / Calendars.ReadWrite permissions.

Environment Variables (same as email_tools):
    EMAIL_TENANT_ID: Azure AD tenant ID (Crowd IT tenant)
    EMAIL_CLIENT_ID: Azure AD Application (client) ID
    EMAIL_CLIENT_SECRET: Azure AD Application client secret
    EMAIL_USER_ID: Default user to access (e.g., chris@crowdit.com.au)
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Well-known Microsoft To Do list aliases. The "Tasks" / default list is the
# one Outlook and Teams tasks flow into, so "tasks" / "default" / "inbox" all
# resolve to the same wellknownListName = 'defaultList'.
_WELLKNOWN_LIST_ALIASES: dict[str, str] = {
    "tasks": "defaultList",
    "default": "defaultList",
    "inbox": "defaultList",
    "flagged": "flaggedEmails",
    "flaggedemails": "flaggedEmails",
    "flagged_emails": "flaggedEmails",
}

_VALID_IMPORTANCES = {"low", "normal", "high"}
_VALID_STATUSES = {
    "notStarted",
    "inProgress",
    "completed",
    "waitingOnOthers",
    "deferred",
}


# =============================================================================
# Helper Functions
# =============================================================================

async def _resolve_list_id(
    email_config,
    list_name_or_id: str,
    user_id: Optional[str] = None,
) -> str:
    """Resolve a user-friendly list name/alias to a todoTaskList id.

    Resolution order:
      1. Empty / "default" / "tasks" / "inbox" -> wellknownListName='defaultList'
      2. Known wellknownListName alias (see _WELLKNOWN_LIST_ALIASES)
      3. Exact displayName match (case-insensitive)
      4. Assume it's already a todoTaskList id and return it unchanged

    Raises ValueError with actionable list of available lists on miss.
    """
    normalized = (list_name_or_id or "").strip()

    if not normalized or normalized.lower() in _WELLKNOWN_LIST_ALIASES:
        wkn = _WELLKNOWN_LIST_ALIASES.get(normalized.lower(), "defaultList")
        data = await email_config.graph_request(
            "GET",
            "/todo/lists",
            user_id=user_id,
            params={"$filter": f"wellknownListName eq '{wkn}'"},
        )
        items = data.get("value", [])
        if items:
            return items[0]["id"]
        # Fall through - user may have renamed/deleted the default list.

    # Try displayName match.
    data = await email_config.graph_request("GET", "/todo/lists", user_id=user_id)
    lists = data.get("value", [])
    for lst in lists:
        if lst.get("displayName", "").lower() == normalized.lower():
            return lst["id"]

    # Last resort: looks like an id already (long base64-ish string)?
    if len(normalized) > 20 and "=" in normalized:
        return normalized

    available = ", ".join(
        f"{lst.get('displayName')!r}" for lst in lists[:10]
    ) or "(none)"
    raise ValueError(
        f"Could not resolve To Do list '{list_name_or_id}'. "
        f"Available lists: {available}. "
        f"Use todo_list_task_lists to see all lists, or pass a list id."
    )


def _parse_datetime(value: Optional[str], timezone: str) -> Optional[dict[str, str]]:
    """Convert a user-provided date/datetime string to Graph's DateTimeTimeZone.

    Accepts:
        - None / "" -> None
        - "YYYY-MM-DD" -> all-day semantic (midnight local)
        - "YYYY-MM-DDTHH:MM[:SS]" -> exact time
        - datetime-parseable ISO 8601

    The Graph API expects: {"dateTime": "2026-04-25T09:00:00", "timeZone": "..."}
    """
    if not value:
        return None
    s = value.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        s = f"{s}T00:00:00"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"Invalid datetime '{value}'. Use 'YYYY-MM-DD' or "
            f"'YYYY-MM-DDTHH:MM:SS'. ({exc})"
        ) from exc
    # If the input carried a UTC offset (e.g. "...Z" or "+10:00"), preserve the
    # actual instant by converting into the caller's timezone before stripping
    # tzinfo. Naive inputs are treated as already being wall time in `timezone`.
    if dt.tzinfo is not None:
        try:
            target_tz = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Invalid timezone '{timezone}': {exc}"
            ) from exc
        dt = dt.astimezone(target_tz).replace(tzinfo=None)
    return {
        "dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "timeZone": timezone,
    }


def _format_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    """Return a compact dict suitable for triage / listing views."""
    body = task.get("body") or {}
    due = task.get("dueDateTime") or {}
    reminder = task.get("reminderDateTime") or {}
    completed = task.get("completedDateTime") or {}
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "importance": task.get("importance"),
        "is_reminder_on": task.get("isReminderOn"),
        "created": task.get("createdDateTime"),
        "last_modified": task.get("lastModifiedDateTime"),
        "due": due.get("dateTime"),
        "due_timezone": due.get("timeZone"),
        "reminder": reminder.get("dateTime"),
        "reminder_timezone": reminder.get("timeZone"),
        "categories": task.get("categories") or [],
        "body_preview": (body.get("content") or "")[:200],
        "completed": completed.get("dateTime"),
        "has_attachments": task.get("hasAttachments", False),
    }


# =============================================================================
# Tool Registration
# =============================================================================

def register_todo_tools(mcp, email_config):
    """Register all Microsoft To Do tools with the MCP server.

    Args:
        mcp: FastMCP server instance
        email_config: EmailConfig instance (shared with email_tools / calendar_tools)
    """

    # =========================================================================
    # TASK LISTS
    # =========================================================================

    @mcp.tool(
        name="todo_list_task_lists",
        annotations={
            "title": "List Microsoft To Do Task Lists",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def todo_list_task_lists(user_id: str = "") -> str:
        """List all Microsoft To Do task lists in the mailbox.

        Args:
            user_id: Override default mailbox (e.g., another user's email).

        Returns summaries of each task list including id, displayName,
        wellknownListName (e.g. 'defaultList', 'flaggedEmails'), isOwner,
        and isShared. Use the returned id (or displayName) with any other
        todo_* tool that takes a list_name parameter.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured. Set EMAIL_TENANT_ID, EMAIL_CLIENT_ID, EMAIL_CLIENT_SECRET, and EMAIL_USER_ID."

        try:
            data = await email_config.graph_request(
                "GET", "/todo/lists", user_id=user_id or None
            )
            lists = data.get("value", [])
            summaries = [
                {
                    "id": lst.get("id"),
                    "displayName": lst.get("displayName"),
                    "wellknownListName": lst.get("wellknownListName"),
                    "isOwner": lst.get("isOwner"),
                    "isShared": lst.get("isShared"),
                }
                for lst in lists
            ]
            return (
                f"📋 {len(summaries)} task list(s)\n\n"
                + json.dumps(summaries, indent=2, default=str)
            )
        except Exception as e:
            return f"❌ Error listing task lists: {e}"

    @mcp.tool(
        name="todo_create_task_list",
        annotations={
            "title": "Create Microsoft To Do Task List",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def todo_create_task_list(display_name: str, user_id: str = "") -> str:
        """Create a new Microsoft To Do task list.

        Args:
            display_name: Name of the new list (e.g., "Inbox Triage 23 Apr").
            user_id: Override default mailbox.

        Returns the newly created list's id and displayName.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        try:
            data = await email_config.graph_request(
                "POST",
                "/todo/lists",
                user_id=user_id or None,
                json_body={"displayName": display_name},
            )
            return (
                f"✅ Created task list '{data.get('displayName')}' "
                f"(id: {data.get('id')})"
            )
        except Exception as e:
            return f"❌ Error creating task list: {e}"

    @mcp.tool(
        name="todo_delete_task_list",
        annotations={
            "title": "Delete Microsoft To Do Task List",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def todo_delete_task_list(list_name: str, user_id: str = "") -> str:
        """Delete a Microsoft To Do task list.

        Args:
            list_name: The list's displayName or id. Cannot be the default list.
            user_id: Override default mailbox.

        WARNING: Deletes the list and all tasks inside it. Irreversible.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        try:
            list_id = await _resolve_list_id(
                email_config, list_name, user_id or None
            )
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as e:
            return f"❌ Error resolving list: {e}"

        try:
            await email_config.graph_request(
                "DELETE", f"/todo/lists/{list_id}", user_id=user_id or None
            )
            return f"🗑️ Deleted task list '{list_name}' (id: {list_id})"
        except Exception as e:
            return f"❌ Error deleting task list: {e}"

    # =========================================================================
    # TASKS
    # =========================================================================

    @mcp.tool(
        name="todo_list_tasks",
        annotations={
            "title": "List Microsoft To Do Tasks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def todo_list_tasks(
        list_name: str = "tasks",
        top: int = 25,
        skip: int = 0,
        status: str = "",
        importance: str = "",
        include_completed: bool = False,
        user_id: str = "",
    ) -> str:
        """List tasks from a Microsoft To Do list.

        Args:
            list_name: List alias ('tasks', 'flagged'), displayName, or id. Default: 'tasks'.
            top: Max tasks to return (1-100, default 25).
            skip: Skip N for pagination.
            status: Filter by status - 'notStarted', 'inProgress', 'completed',
                    'waitingOnOthers', or 'deferred'.
            importance: Filter by 'low', 'normal', or 'high'.
            include_completed: If False (default), completed tasks are hidden.
                               Overridden by explicit status filter.
            user_id: Override default mailbox.

        Returns task summaries sorted by lastModifiedDateTime desc,
        optimised for AI triage.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        if status and status not in _VALID_STATUSES:
            return (
                f"❌ Invalid status '{status}'. "
                f"Valid: {', '.join(sorted(_VALID_STATUSES))}"
            )
        if importance and importance not in _VALID_IMPORTANCES:
            return (
                f"❌ Invalid importance '{importance}'. "
                f"Valid: {', '.join(sorted(_VALID_IMPORTANCES))}"
            )
        top = max(1, min(top, 100))

        try:
            list_id = await _resolve_list_id(
                email_config, list_name, user_id or None
            )
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as e:
            return f"❌ Error resolving list: {e}"

        filters: list[str] = []
        if status:
            filters.append(f"status eq '{status}'")
        elif not include_completed:
            filters.append("status ne 'completed'")
        if importance:
            filters.append(f"importance eq '{importance}'")

        params: dict[str, Any] = {
            "$top": top,
            "$skip": skip,
            "$orderby": "lastModifiedDateTime desc",
        }
        if filters:
            params["$filter"] = " and ".join(filters)

        try:
            data = await email_config.graph_request(
                "GET",
                f"/todo/lists/{list_id}/tasks",
                user_id=user_id or None,
                params=params,
            )
            tasks = data.get("value", [])
            summaries = [_format_task_summary(t) for t in tasks]
            return (
                f"📝 {len(summaries)} task(s) in '{list_name}' "
                f"(showing skip={skip}, top={top})\n\n"
                + json.dumps(summaries, indent=2, default=str)
            )
        except Exception as e:
            return f"❌ Error listing tasks: {e}"

    @mcp.tool(
        name="todo_get_task",
        annotations={
            "title": "Get Microsoft To Do Task",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def todo_get_task(
        task_id: str,
        list_name: str = "tasks",
        user_id: str = "",
    ) -> str:
        """Get full details of a single Microsoft To Do task, including body.

        Args:
            task_id: The task's id (from todo_list_tasks or todo_create_task).
            list_name: The list containing the task. Default: 'tasks'.
            user_id: Override default mailbox.

        Returns the full task JSON including body content, checklist items,
        linked resources, and categories.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        try:
            list_id = await _resolve_list_id(
                email_config, list_name, user_id or None
            )
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as e:
            return f"❌ Error resolving list: {e}"

        try:
            data = await email_config.graph_request(
                "GET",
                f"/todo/lists/{list_id}/tasks/{task_id}",
                user_id=user_id or None,
            )
            return json.dumps(data, indent=2, default=str)
        except Exception as e:
            return f"❌ Error fetching task: {e}"

    @mcp.tool(
        name="todo_create_task",
        annotations={
            "title": "Create Microsoft To Do Task",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def todo_create_task(
        title: str,
        list_name: str = "tasks",
        body: str = "",
        body_type: str = "text",
        importance: str = "normal",
        due_date: str = "",
        reminder_date: str = "",
        timezone: str = "Australia/Sydney",
        categories: str = "",
        linked_url: str = "",
        linked_url_title: str = "",
        user_id: str = "",
    ) -> str:
        """Create a new Microsoft To Do task.

        Args:
            title: Task title (required).
            list_name: Target list alias/name/id. Default: 'tasks' (the default list).
            body: Task notes / description.
            body_type: 'text' (default) or 'html'.
            importance: 'low', 'normal', or 'high'. Default: 'normal'.
            due_date: Due date as 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'.
            reminder_date: Reminder as 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'.
                           If set, isReminderOn is turned on automatically.
            timezone: IANA timezone for due/reminder dates.
                      Default: 'Australia/Sydney'.
            categories: Comma-separated category names
                        (e.g., "VRG,Efex,Urgent").
            linked_url: Optional URL to attach to the task
                        (e.g., the Outlook web link to the originating email).
            linked_url_title: Display name for the linked URL. Required if
                              linked_url is set; will default to linked_url if blank.
            user_id: Override default mailbox.

        Returns the created task's id and title on success.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        if importance not in _VALID_IMPORTANCES:
            return (
                f"❌ Invalid importance '{importance}'. "
                f"Valid: {', '.join(sorted(_VALID_IMPORTANCES))}"
            )
        if body_type not in {"text", "html"}:
            return f"❌ Invalid body_type '{body_type}'. Valid: 'text' or 'html'."

        try:
            list_id = await _resolve_list_id(
                email_config, list_name, user_id or None
            )
            due_payload = _parse_datetime(due_date, timezone)
            reminder_payload = _parse_datetime(reminder_date, timezone)
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as e:
            return f"❌ Error preparing task: {e}"

        payload: dict[str, Any] = {
            "title": title,
            "importance": importance,
        }
        if body:
            payload["body"] = {"content": body, "contentType": body_type}
        if due_payload:
            payload["dueDateTime"] = due_payload
        if reminder_payload:
            payload["reminderDateTime"] = reminder_payload
            payload["isReminderOn"] = True
        if categories:
            payload["categories"] = [
                c.strip() for c in categories.split(",") if c.strip()
            ]
        if linked_url:
            payload["linkedResources"] = [
                {
                    "webUrl": linked_url,
                    "applicationName": "Crowd IT MCP",
                    "displayName": linked_url_title or linked_url,
                }
            ]

        try:
            data = await email_config.graph_request(
                "POST",
                f"/todo/lists/{list_id}/tasks",
                user_id=user_id or None,
                json_body=payload,
            )
            return (
                f"✅ Created task '{data.get('title')}' in '{list_name}' "
                f"(id: {data.get('id')})"
            )
        except Exception as e:
            return f"❌ Error creating task: {e}"

    @mcp.tool(
        name="todo_update_task",
        annotations={
            "title": "Update Microsoft To Do Task",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def todo_update_task(
        task_id: str,
        list_name: str = "tasks",
        title: str = "",
        body: str = "",
        body_type: str = "text",
        importance: str = "",
        status: str = "",
        due_date: str = "",
        reminder_date: str = "",
        clear_reminder: bool = False,
        timezone: str = "Australia/Sydney",
        categories: str = "",
        user_id: str = "",
    ) -> str:
        """Update an existing Microsoft To Do task. Only supplied fields change.

        Args:
            task_id: The task id to update (required).
            list_name: The list containing the task. Default: 'tasks'.
            title: New title (omit to leave unchanged).
            body: New body/notes (omit to leave unchanged).
            body_type: 'text' or 'html'. Only used if body is supplied.
            importance: 'low', 'normal', 'high'.
            status: 'notStarted', 'inProgress', 'completed', 'waitingOnOthers',
                    or 'deferred'. Use todo_complete_task for convenience.
            due_date: New due date (YYYY-MM-DD or ISO datetime).
                      Pass "" to leave unchanged.
            reminder_date: New reminder (YYYY-MM-DD or ISO datetime).
            clear_reminder: If True, clears the existing reminder
                            (isReminderOn -> False).
            timezone: IANA timezone for date parsing. Default: 'Australia/Sydney'.
            categories: Comma-separated categories - REPLACES existing list.
            user_id: Override default mailbox.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        if importance and importance not in _VALID_IMPORTANCES:
            return (
                f"❌ Invalid importance '{importance}'. "
                f"Valid: {', '.join(sorted(_VALID_IMPORTANCES))}"
            )
        if status and status not in _VALID_STATUSES:
            return (
                f"❌ Invalid status '{status}'. "
                f"Valid: {', '.join(sorted(_VALID_STATUSES))}"
            )
        if body and body_type not in {"text", "html"}:
            return f"❌ Invalid body_type '{body_type}'. Valid: 'text' or 'html'."

        try:
            list_id = await _resolve_list_id(
                email_config, list_name, user_id or None
            )
            due_payload = _parse_datetime(due_date, timezone) if due_date else None
            reminder_payload = (
                _parse_datetime(reminder_date, timezone) if reminder_date else None
            )
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as e:
            return f"❌ Error preparing update: {e}"

        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        if body:
            payload["body"] = {"content": body, "contentType": body_type}
        if importance:
            payload["importance"] = importance
        if status:
            payload["status"] = status
        if due_payload is not None:
            payload["dueDateTime"] = due_payload
        if reminder_payload is not None:
            payload["reminderDateTime"] = reminder_payload
            payload["isReminderOn"] = True
        elif clear_reminder:
            payload["isReminderOn"] = False
            payload["reminderDateTime"] = None
        if categories:
            payload["categories"] = [
                c.strip() for c in categories.split(",") if c.strip()
            ]

        if not payload:
            return "⚠️ No fields supplied — nothing to update."

        try:
            data = await email_config.graph_request(
                "PATCH",
                f"/todo/lists/{list_id}/tasks/{task_id}",
                user_id=user_id or None,
                json_body=payload,
            )
            return (
                f"✅ Updated task '{data.get('title')}' (id: {data.get('id')})"
            )
        except Exception as e:
            return f"❌ Error updating task: {e}"

    @mcp.tool(
        name="todo_complete_task",
        annotations={
            "title": "Complete Microsoft To Do Task",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def todo_complete_task(
        task_id: str,
        list_name: str = "tasks",
        user_id: str = "",
    ) -> str:
        """Mark a Microsoft To Do task as completed.

        Convenience wrapper around todo_update_task with status='completed'.

        Args:
            task_id: The task id to complete.
            list_name: The list containing the task. Default: 'tasks'.
            user_id: Override default mailbox.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        try:
            list_id = await _resolve_list_id(
                email_config, list_name, user_id or None
            )
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as e:
            return f"❌ Error resolving list: {e}"

        try:
            await email_config.graph_request(
                "PATCH",
                f"/todo/lists/{list_id}/tasks/{task_id}",
                user_id=user_id or None,
                json_body={"status": "completed"},
            )
            return f"✅ Completed task {task_id}"
        except Exception as e:
            return f"❌ Error completing task: {e}"

    @mcp.tool(
        name="todo_delete_task",
        annotations={
            "title": "Delete Microsoft To Do Task",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def todo_delete_task(
        task_id: str,
        list_name: str = "tasks",
        user_id: str = "",
    ) -> str:
        """Permanently delete a Microsoft To Do task.

        Args:
            task_id: The task id to delete.
            list_name: The list containing the task. Default: 'tasks'.
            user_id: Override default mailbox.

        WARNING: Irreversible. Prefer todo_complete_task if the task is done
        but you may want to audit it later.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        try:
            list_id = await _resolve_list_id(
                email_config, list_name, user_id or None
            )
        except ValueError as exc:
            return f"❌ {exc}"
        except Exception as e:
            return f"❌ Error resolving list: {e}"

        try:
            await email_config.graph_request(
                "DELETE",
                f"/todo/lists/{list_id}/tasks/{task_id}",
                user_id=user_id or None,
            )
            return f"🗑️ Deleted task {task_id}"
        except Exception as e:
            return f"❌ Error deleting task: {e}"

    @mcp.tool(
        name="todo_search_tasks",
        annotations={
            "title": "Search Microsoft To Do Tasks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def todo_search_tasks(
        query: str,
        top: int = 25,
        include_completed: bool = False,
        user_id: str = "",
    ) -> str:
        """Search across ALL Microsoft To Do lists by title/body text.

        Scans every list in the mailbox — useful when you don't remember
        which list a task is in, or you want a cross-list triage view.

        Args:
            query: Text to match against task title/body (case-insensitive).
            top: Max results (1-100, default 25).
            include_completed: If False (default), completed tasks are hidden.
            user_id: Override default mailbox.

        Returns matching tasks with the name of their containing list.
        """
        if not email_config.is_configured:
            return "❌ To Do not configured."

        top = max(1, min(top, 100))
        q = query.strip().lower()
        if not q:
            return "❌ query cannot be empty"

        try:
            lists_data = await email_config.graph_request(
                "GET", "/todo/lists", user_id=user_id or None
            )
        except Exception as e:
            return f"❌ Error listing task lists: {e}"

        matches: list[dict[str, Any]] = []
        page_size = 200
        # Safety cap so a runaway list can't exhaust memory or rate limits.
        max_per_list = 2000
        for lst in lists_data.get("value", []):
            if len(matches) >= top:
                break
            list_id = lst["id"]
            list_name = lst.get("displayName") or lst.get("wellknownListName")

            skip = 0
            while skip < max_per_list and len(matches) < top:
                params: dict[str, Any] = {"$top": page_size, "$skip": skip}
                if not include_completed:
                    params["$filter"] = "status ne 'completed'"

                try:
                    tdata = await email_config.graph_request(
                        "GET",
                        f"/todo/lists/{list_id}/tasks",
                        user_id=user_id or None,
                        params=params,
                    )
                except Exception as exc:
                    logger.warning(
                        "Skipping list %s during search at skip=%d: %s",
                        list_name,
                        skip,
                        exc,
                    )
                    break

                batch = tdata.get("value", [])
                for task in batch:
                    haystacks = [
                        (task.get("title") or "").lower(),
                        ((task.get("body") or {}).get("content") or "").lower(),
                    ]
                    if any(q in h for h in haystacks):
                        summary = _format_task_summary(task)
                        summary["list_name"] = list_name
                        summary["list_id"] = list_id
                        matches.append(summary)
                        if len(matches) >= top:
                            break

                if len(batch) < page_size:
                    break
                skip += page_size

        return (
            f"🔍 {len(matches)} match(es) for '{query}'\n\n"
            + json.dumps(matches, indent=2, default=str)
        )
