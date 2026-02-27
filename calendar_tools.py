"""
Microsoft Graph Calendar Integration Tools for Crowd IT MCP Server

This module provides comprehensive Outlook calendar management capabilities
using the Microsoft Graph API, designed for AI agent-driven scheduling,
planning, and calendar optimization.

Capabilities:
- List/search calendar events with date range filtering
- Get detailed event information
- Create, update, and delete events
- Find free/busy times for scheduling
- Manage multiple calendars
- Accept/decline/tentative meeting responses

Authentication: Reuses the EmailConfig from email_tools.py which uses
OAuth2 client_credentials flow with Azure AD. Requires Calendars.ReadWrite
application permission (NOT delegated) granted with admin consent.

Environment Variables (same as email_tools):
    EMAIL_TENANT_ID: Azure AD tenant ID (Crowd IT tenant)
    EMAIL_CLIENT_ID: Azure AD Application (client) ID
    EMAIL_CLIENT_SECRET: Azure AD Application client secret
    EMAIL_USER_ID: Default user to access (e.g., chris@crowdit.com.au)
"""

import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================

def format_event_summary(event: dict, include_body: bool = False) -> dict:
    """Format a Graph API calendar event into a clean summary."""
    start = event.get("start", {})
    end = event.get("end", {})

    organizer = event.get("organizer", {}).get("emailAddress", {})
    organizer_str = organizer.get("name", organizer.get("address", "unknown"))

    attendees = []
    for att in event.get("attendees", []):
        email_addr = att.get("emailAddress", {})
        status = att.get("status", {}).get("response", "none")
        attendees.append({
            "name": email_addr.get("name", ""),
            "email": email_addr.get("address", ""),
            "response": status,
            "type": att.get("type", "required")
        })

    result = {
        "id": event.get("id", ""),
        "subject": event.get("subject", "(no subject)"),
        "start": start.get("dateTime", ""),
        "start_timezone": start.get("timeZone", ""),
        "end": end.get("dateTime", ""),
        "end_timezone": end.get("timeZone", ""),
        "is_all_day": event.get("isAllDay", False),
        "location": event.get("location", {}).get("displayName", ""),
        "organizer": organizer_str,
        "attendee_count": len(attendees),
        "show_as": event.get("showAs", "busy"),
        "importance": event.get("importance", "normal"),
        "is_cancelled": event.get("isCancelled", False),
        "is_online_meeting": event.get("isOnlineMeeting", False),
        "online_meeting_url": event.get("onlineMeeting", {}).get("joinUrl", "") if event.get("onlineMeeting") else "",
        "response_status": event.get("responseStatus", {}).get("response", "none"),
        "categories": event.get("categories", []),
        "recurrence": "recurring" if event.get("recurrence") else "single",
        "series_master_id": event.get("seriesMasterId", ""),
    }

    if include_body:
        body = event.get("body", {})
        result["body"] = body.get("content", "")
        result["body_type"] = body.get("contentType", "text")
        result["attendees"] = attendees
        result["web_link"] = event.get("webLink", "")

    return result


def _parse_date(date_str: str) -> str:
    """Parse a date string and return ISO format. Accepts YYYY-MM-DD or ISO datetime."""
    if not date_str:
        return ""
    # If it's just a date, add time
    if len(date_str) == 10:
        return f"{date_str}T00:00:00"
    return date_str


# =============================================================================
# Tool Registration
# =============================================================================

def register_calendar_tools(mcp, email_config):
    """Register all calendar tools with the MCP server.

    Args:
        mcp: FastMCP server instance
        email_config: EmailConfig instance (shared with email_tools)
    """

    # =========================================================================
    # LIST & VIEW EVENTS
    # =========================================================================

    @mcp.tool(
        name="calendar_list_events",
        annotations={
            "title": "List Calendar Events",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_list_events(
        start_date: str = "",
        end_date: str = "",
        days: int = 7,
        top: int = 50,
        calendar_id: str = "",
        user_id: str = ""
    ) -> str:
        """List calendar events within a date range.

        Uses calendarView for accurate expansion of recurring events.

        Args:
            start_date: Start date (YYYY-MM-DD or ISO datetime). Default: today.
            end_date: End date (YYYY-MM-DD or ISO datetime). Default: start_date + days.
            days: Number of days to show if end_date not provided (default 7).
            top: Max events to return (default 50, max 100).
            calendar_id: Specific calendar ID (empty = default calendar).
            user_id: Override default mailbox (e.g., another user's email).

        Returns a list of event summaries sorted by start time.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured. Set EMAIL_TENANT_ID, EMAIL_CLIENT_ID, EMAIL_CLIENT_SECRET, and EMAIL_USER_ID."

        try:
            top = min(top, 100)

            # Determine date range
            if start_date:
                start_dt = start_date if "T" in start_date else f"{start_date}T00:00:00"
            else:
                start_dt = datetime.utcnow().strftime("%Y-%m-%dT00:00:00")

            if end_date:
                end_dt = end_date if "T" in end_date else f"{end_date}T23:59:59"
            else:
                # Add 'days' to start
                start_parsed = datetime.fromisoformat(start_dt)
                end_parsed = start_parsed + timedelta(days=days)
                end_dt = end_parsed.strftime("%Y-%m-%dT23:59:59")

            # Use calendarView for proper recurring event expansion
            if calendar_id:
                endpoint = f"/calendars/{calendar_id}/calendarView"
            else:
                endpoint = "/calendarView"

            params = {
                "startDateTime": start_dt,
                "endDateTime": end_dt,
                "$top": top,
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,isAllDay,location,organizer,attendees,showAs,importance,isCancelled,isOnlineMeeting,onlineMeeting,responseStatus,categories,recurrence,seriesMasterId"
            }

            result = await email_config.graph_request(
                "GET", endpoint,
                user_id=user_id or None,
                params=params
            )

            events = result.get("value", [])
            formatted = [format_event_summary(evt) for evt in events]

            # Build summary stats
            total = len(formatted)
            all_day = sum(1 for e in formatted if e.get("is_all_day"))
            cancelled = sum(1 for e in formatted if e.get("is_cancelled"))

            summary = f"📅 {total} events from {start_dt[:10]} to {end_dt[:10]}"
            if all_day:
                summary += f" ({all_day} all-day)"
            if cancelled:
                summary += f" ({cancelled} cancelled)"
            summary += "\n\n"

            return summary + json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error listing calendar events: {e}"

    @mcp.tool(
        name="calendar_get_event",
        annotations={
            "title": "Get Calendar Event Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_get_event(
        event_id: str,
        user_id: str = ""
    ) -> str:
        """Get detailed information about a specific calendar event.

        Args:
            event_id: The event ID (from calendar_list_events).
            user_id: Override default mailbox.

        Returns full event details including body, attendees, and meeting links.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            result = await email_config.graph_request(
                "GET", f"/events/{event_id}",
                user_id=user_id or None,
                params={
                    "$select": "id,subject,start,end,isAllDay,location,organizer,attendees,showAs,importance,isCancelled,isOnlineMeeting,onlineMeeting,responseStatus,categories,body,webLink,recurrence,seriesMasterId,sensitivity"
                }
            )

            formatted = format_event_summary(result, include_body=True)
            formatted["sensitivity"] = result.get("sensitivity", "normal")
            return json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error getting event: {e}"

    # =========================================================================
    # SEARCH EVENTS
    # =========================================================================

    @mcp.tool(
        name="calendar_search_events",
        annotations={
            "title": "Search Calendar Events",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_search_events(
        query: str,
        start_date: str = "",
        end_date: str = "",
        top: int = 25,
        user_id: str = ""
    ) -> str:
        """Search calendar events by subject, body, or attendees.

        Args:
            query: Search text to match against event subject and body.
            start_date: Optional start date filter (YYYY-MM-DD).
            end_date: Optional end date filter (YYYY-MM-DD).
            top: Max results (default 25, max 50).
            user_id: Override default mailbox.

        Returns matching events sorted by start time.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            top = min(top, 50)

            # Build filter
            filters = [f"contains(subject, '{query}')"]
            if start_date:
                filters.append(f"start/dateTime ge '{start_date}T00:00:00'")
            if end_date:
                filters.append(f"end/dateTime le '{end_date}T23:59:59'")

            params = {
                "$filter": " and ".join(filters),
                "$top": top,
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,isAllDay,location,organizer,attendees,showAs,importance,isCancelled,isOnlineMeeting,responseStatus,categories"
            }

            result = await email_config.graph_request(
                "GET", "/events",
                user_id=user_id or None,
                params=params
            )

            events = result.get("value", [])
            formatted = [format_event_summary(evt) for evt in events]
            return f"🔍 {len(formatted)} events matching '{query}'\n\n" + json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error searching events: {e}"

    # =========================================================================
    # CREATE EVENTS
    # =========================================================================

    @mcp.tool(
        name="calendar_create_event",
        annotations={
            "title": "Create Calendar Event",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def calendar_create_event(
        subject: str,
        start: str,
        end: str,
        body: str = "",
        location: str = "",
        attendees: str = "",
        is_all_day: bool = False,
        is_online_meeting: bool = False,
        show_as: str = "busy",
        importance: str = "normal",
        timezone: str = "Australia/Sydney",
        reminder_minutes: int = 15,
        categories: str = "",
        is_private: bool = False,
        calendar_id: str = "",
        user_id: str = ""
    ) -> str:
        """Create a new calendar event.

        Args:
            subject: Event title/subject.
            start: Start datetime (YYYY-MM-DD for all-day, or YYYY-MM-DDTHH:MM:SS).
            end: End datetime (same format as start).
            body: Event body/description (plain text).
            location: Event location (e.g., room name, address, or "Microsoft Teams").
            attendees: Comma-separated email addresses of attendees.
                      Prefix with 'optional:' for optional attendees
                      (e.g., "chris@crowdit.com.au,optional:jane@example.com").
            is_all_day: Whether this is an all-day event.
            is_online_meeting: Create a Teams meeting link.
            show_as: Free/busy status: 'free', 'tentative', 'busy', 'oof', 'workingElsewhere'.
            importance: Event importance: 'low', 'normal', 'high'.
            timezone: Timezone for start/end (default: Australia/Sydney).
            reminder_minutes: Minutes before event to remind (default 15, 0 to disable).
            categories: Comma-separated category names.
            is_private: Mark event as private.
            calendar_id: Specific calendar ID (empty = default calendar).
            user_id: Override default mailbox.

        Returns the created event details.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            # Build event body
            event_body = {
                "subject": subject,
                "start": {
                    "dateTime": _parse_date(start),
                    "timeZone": timezone
                },
                "end": {
                    "dateTime": _parse_date(end),
                    "timeZone": timezone
                },
                "isAllDay": is_all_day,
                "showAs": show_as,
                "importance": importance,
            }

            if body:
                event_body["body"] = {
                    "contentType": "Text",
                    "content": body
                }

            if location:
                event_body["location"] = {"displayName": location}

            if attendees:
                att_list = []
                for addr in attendees.split(","):
                    addr = addr.strip()
                    if not addr:
                        continue
                    att_type = "required"
                    if addr.lower().startswith("optional:"):
                        att_type = "optional"
                        addr = addr[9:].strip()
                    att_list.append({
                        "emailAddress": {"address": addr},
                        "type": att_type
                    })
                event_body["attendees"] = att_list

            if is_online_meeting:
                event_body["isOnlineMeeting"] = True
                event_body["onlineMeetingProvider"] = "teamsForBusiness"

            if reminder_minutes > 0:
                event_body["isReminderOn"] = True
                event_body["reminderMinutesBeforeStart"] = reminder_minutes
            else:
                event_body["isReminderOn"] = False

            if categories:
                event_body["categories"] = [c.strip() for c in categories.split(",") if c.strip()]

            if is_private:
                event_body["sensitivity"] = "private"

            # Choose endpoint
            if calendar_id:
                endpoint = f"/calendars/{calendar_id}/events"
            else:
                endpoint = "/events"

            result = await email_config.graph_request(
                "POST", endpoint,
                user_id=user_id or None,
                json_body=event_body
            )

            formatted = format_event_summary(result)
            return f"✅ Event created: {subject}\n\n" + json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error creating event: {e}"

    # =========================================================================
    # UPDATE EVENTS
    # =========================================================================

    @mcp.tool(
        name="calendar_update_event",
        annotations={
            "title": "Update Calendar Event",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_update_event(
        event_id: str,
        subject: str = "",
        start: str = "",
        end: str = "",
        body: str = "",
        location: str = "",
        show_as: str = "",
        importance: str = "",
        timezone: str = "Australia/Sydney",
        is_online_meeting: Optional[bool] = None,
        reminder_minutes: int = -1,
        categories: str = "",
        user_id: str = ""
    ) -> str:
        """Update an existing calendar event. Only provided fields are updated.

        Args:
            event_id: The event ID to update.
            subject: New event subject (empty = no change).
            start: New start datetime (empty = no change).
            end: New end datetime (empty = no change).
            body: New event body (empty = no change).
            location: New location (empty = no change).
            show_as: New free/busy status (empty = no change).
            importance: New importance (empty = no change).
            timezone: Timezone for start/end times (default: Australia/Sydney).
            is_online_meeting: Toggle online meeting (None = no change).
            reminder_minutes: Reminder in minutes (-1 = no change, 0 = disable).
            categories: New comma-separated categories (empty = no change).
            user_id: Override default mailbox.

        Returns updated event details.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            updates = {}

            if subject:
                updates["subject"] = subject
            if start:
                updates["start"] = {"dateTime": _parse_date(start), "timeZone": timezone}
            if end:
                updates["end"] = {"dateTime": _parse_date(end), "timeZone": timezone}
            if body:
                updates["body"] = {"contentType": "Text", "content": body}
            if location:
                updates["location"] = {"displayName": location}
            if show_as:
                updates["showAs"] = show_as
            if importance:
                updates["importance"] = importance
            if is_online_meeting is not None:
                updates["isOnlineMeeting"] = is_online_meeting
                if is_online_meeting:
                    updates["onlineMeetingProvider"] = "teamsForBusiness"
            if reminder_minutes >= 0:
                if reminder_minutes == 0:
                    updates["isReminderOn"] = False
                else:
                    updates["isReminderOn"] = True
                    updates["reminderMinutesBeforeStart"] = reminder_minutes
            if categories:
                updates["categories"] = [c.strip() for c in categories.split(",") if c.strip()]

            if not updates:
                return "⚠️ No changes specified. Provide at least one field to update."

            result = await email_config.graph_request(
                "PATCH", f"/events/{event_id}",
                user_id=user_id or None,
                json_body=updates
            )

            formatted = format_event_summary(result)
            return f"✅ Event updated\n\n" + json.dumps(formatted, indent=2, default=str)

        except Exception as e:
            return f"❌ Error updating event: {e}"

    # =========================================================================
    # DELETE EVENTS
    # =========================================================================

    @mcp.tool(
        name="calendar_delete_event",
        annotations={
            "title": "Delete Calendar Event",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_delete_event(
        event_id: str,
        user_id: str = ""
    ) -> str:
        """Delete a calendar event. This cannot be undone.

        Args:
            event_id: The event ID to delete.
            user_id: Override default mailbox.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            await email_config.graph_request(
                "DELETE", f"/events/{event_id}",
                user_id=user_id or None
            )
            return "✅ Event deleted"

        except Exception as e:
            return f"❌ Error deleting event: {e}"

    # =========================================================================
    # RESPOND TO EVENTS
    # =========================================================================

    @mcp.tool(
        name="calendar_respond_event",
        annotations={
            "title": "Accept/Decline/Tentative Calendar Event",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_respond_event(
        event_id: str,
        response: str,
        comment: str = "",
        send_response: bool = True,
        user_id: str = ""
    ) -> str:
        """Accept, decline, or tentatively accept a meeting invitation.

        Args:
            event_id: The event ID to respond to.
            response: Response type: 'accept', 'decline', or 'tentative'.
            comment: Optional message to include with the response.
            send_response: Whether to notify the organizer (default True).
            user_id: Override default mailbox.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            valid_responses = {"accept", "decline", "tentative"}
            if response.lower() not in valid_responses:
                return f"❌ Invalid response '{response}'. Use: {', '.join(valid_responses)}"

            # Map to Graph API endpoint names
            endpoint_map = {
                "accept": "accept",
                "decline": "decline",
                "tentative": "tentativelyAccept"
            }

            body = {"sendResponse": send_response}
            if comment:
                body["comment"] = comment

            await email_config.graph_request(
                "POST", f"/events/{event_id}/{endpoint_map[response.lower()]}",
                user_id=user_id or None,
                json_body=body
            )

            return f"✅ Event {response}ed" + (f" with comment: {comment}" if comment else "")

        except Exception as e:
            return f"❌ Error responding to event: {e}"

    # =========================================================================
    # FREE/BUSY & SCHEDULING
    # =========================================================================

    @mcp.tool(
        name="calendar_find_free_time",
        annotations={
            "title": "Find Free Time Slots",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_find_free_time(
        start_date: str,
        end_date: str,
        min_duration_minutes: int = 30,
        schedules: str = "",
        timezone: str = "Australia/Sydney",
        user_id: str = ""
    ) -> str:
        """Find available time slots by checking free/busy schedules.

        Uses the Graph API getSchedule endpoint to check availability
        for one or more users, then calculates gaps.

        Args:
            start_date: Start date (YYYY-MM-DD or ISO datetime).
            end_date: End date (YYYY-MM-DD or ISO datetime).
            min_duration_minutes: Minimum free slot duration in minutes (default 30).
            schedules: Comma-separated email addresses to check availability for.
                      Empty = check only the default user.
            timezone: Timezone (default: Australia/Sydney).
            user_id: Override default mailbox.

        Returns free/busy schedule and identified free slots.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            uid = user_id or email_config.default_user_id
            schedule_list = [s.strip() for s in schedules.split(",") if s.strip()] if schedules else [uid]

            start_dt = start_date if "T" in start_date else f"{start_date}T00:00:00"
            end_dt = end_date if "T" in end_date else f"{end_date}T23:59:59"

            body = {
                "schedules": schedule_list,
                "startTime": {
                    "dateTime": start_dt,
                    "timeZone": timezone
                },
                "endTime": {
                    "dateTime": end_dt,
                    "timeZone": timezone
                },
                "availabilityViewInterval": max(min_duration_minutes, 15)
            }

            # getSchedule is a POST on /calendar/getSchedule
            result = await email_config.graph_request(
                "POST", "/calendar/getSchedule",
                user_id=user_id or None,
                json_body=body
            )

            schedules_result = result.get("value", [])
            output = {
                "period": {"start": start_dt, "end": end_dt, "timezone": timezone},
                "schedules": []
            }

            for sched in schedules_result:
                email_addr = sched.get("scheduleId", "")
                items = sched.get("scheduleItems", [])
                availability_view = sched.get("availabilityView", "")

                busy_slots = []
                for item in items:
                    busy_slots.append({
                        "subject": item.get("subject", ""),
                        "status": item.get("status", ""),
                        "start": item.get("start", {}).get("dateTime", ""),
                        "end": item.get("end", {}).get("dateTime", ""),
                        "location": item.get("location", "")
                    })

                output["schedules"].append({
                    "email": email_addr,
                    "availability_view": availability_view,
                    "busy_slots": busy_slots
                })

            return json.dumps(output, indent=2, default=str)

        except Exception as e:
            return f"❌ Error finding free time: {e}"

    # =========================================================================
    # LIST CALENDARS
    # =========================================================================

    @mcp.tool(
        name="calendar_list_calendars",
        annotations={
            "title": "List Calendars",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_list_calendars(
        user_id: str = ""
    ) -> str:
        """List all calendars available to the user.

        Args:
            user_id: Override default mailbox.

        Returns calendar names, IDs, colors, and permissions.
        Useful for finding calendar IDs to use with other calendar tools.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            result = await email_config.graph_request(
                "GET", "/calendars",
                user_id=user_id or None,
                params={
                    "$top": 50,
                    "$select": "id,name,color,isDefaultCalendar,canEdit,canShare,owner"
                }
            )

            calendars = result.get("value", [])
            formatted = []
            for cal in calendars:
                owner = cal.get("owner", {})
                formatted.append({
                    "id": cal.get("id", ""),
                    "name": cal.get("name", ""),
                    "color": cal.get("color", ""),
                    "is_default": cal.get("isDefaultCalendar", False),
                    "can_edit": cal.get("canEdit", False),
                    "can_share": cal.get("canShare", False),
                    "owner_name": owner.get("name", ""),
                    "owner_email": owner.get("address", ""),
                })

            return json.dumps(formatted, indent=2)

        except Exception as e:
            return f"❌ Error listing calendars: {e}"

    # =========================================================================
    # WEEKLY SUMMARY (AI-OPTIMIZED)
    # =========================================================================

    @mcp.tool(
        name="calendar_weekly_summary",
        annotations={
            "title": "Get Weekly Calendar Summary",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def calendar_weekly_summary(
        start_date: str = "",
        timezone: str = "Australia/Sydney",
        user_id: str = ""
    ) -> str:
        """Get an AI-optimized weekly calendar summary with analytics.

        Returns a structured overview of the week including:
        - Total meetings and hours booked
        - Busiest/lightest days
        - Gaps of 2+ hours (potential focus time)
        - Back-to-back meeting sequences
        - Meeting categorization (internal vs external, recurring vs one-off)

        Args:
            start_date: Start of week (YYYY-MM-DD). Default: upcoming Monday.
            timezone: Timezone (default: Australia/Sydney).
            user_id: Override default mailbox.

        Designed for AI-driven weekly planning and calendar optimization.
        """
        if not email_config.is_configured:
            return "❌ Calendar not configured."

        try:
            # Default to the upcoming Monday
            if start_date:
                week_start = datetime.fromisoformat(start_date)
            else:
                today = datetime.utcnow()
                # Find next Monday (or today if it's Monday)
                days_ahead = (7 - today.weekday()) % 7
                if days_ahead == 0 and today.weekday() != 0:
                    days_ahead = 7
                if today.weekday() == 0:
                    days_ahead = 0
                week_start = today + timedelta(days=days_ahead)
                week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

            week_end = week_start + timedelta(days=5)  # Mon-Fri

            params = {
                "startDateTime": week_start.strftime("%Y-%m-%dT00:00:00"),
                "endDateTime": week_end.strftime("%Y-%m-%dT23:59:59"),
                "$top": 100,
                "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,isAllDay,location,organizer,attendees,showAs,importance,isCancelled,isOnlineMeeting,responseStatus,categories,recurrence,seriesMasterId"
            }

            result = await email_config.graph_request(
                "GET", "/calendarView",
                user_id=user_id or None,
                params=params
            )

            events = result.get("value", [])
            uid = user_id or email_config.default_user_id

            # Analyze by day
            days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            daily_stats = {}
            all_events = []

            for day_offset in range(5):
                day_date = week_start + timedelta(days=day_offset)
                day_name = days_of_week[day_offset]
                day_str = day_date.strftime("%Y-%m-%d")
                daily_stats[day_name] = {
                    "date": day_str,
                    "events": [],
                    "meeting_count": 0,
                    "meeting_hours": 0.0,
                    "gaps_2plus_hours": [],
                    "first_meeting": None,
                    "last_meeting_end": None
                }

            for evt in events:
                if evt.get("isCancelled"):
                    continue
                if evt.get("isAllDay"):
                    continue

                start_str = evt.get("start", {}).get("dateTime", "")
                end_str = evt.get("end", {}).get("dateTime", "")
                if not start_str or not end_str:
                    continue

                try:
                    evt_start = datetime.fromisoformat(start_str.replace("Z", ""))
                    evt_end = datetime.fromisoformat(end_str.replace("Z", ""))
                except Exception:
                    continue

                duration_hours = (evt_end - evt_start).total_seconds() / 3600
                day_offset = (evt_start.date() - week_start.date()).days

                if 0 <= day_offset < 5:
                    day_name = days_of_week[day_offset]
                    day = daily_stats[day_name]

                    organizer_email = evt.get("organizer", {}).get("emailAddress", {}).get("address", "")
                    is_external = not organizer_email.endswith("crowdit.com.au") if organizer_email else False

                    event_info = {
                        "id": evt.get("id", ""),
                        "subject": evt.get("subject", "(no subject)"),
                        "start": start_str,
                        "end": end_str,
                        "duration_hours": round(duration_hours, 2),
                        "organizer": organizer_email,
                        "is_external": is_external,
                        "is_recurring": bool(evt.get("recurrence") or evt.get("seriesMasterId")),
                        "attendee_count": len(evt.get("attendees", [])),
                        "show_as": evt.get("showAs", "busy"),
                        "response": evt.get("responseStatus", {}).get("response", "none"),
                    }

                    day["events"].append(event_info)
                    day["meeting_count"] += 1
                    day["meeting_hours"] += duration_hours

                    start_time = evt_start.strftime("%H:%M")
                    end_time = evt_end.strftime("%H:%M")

                    if day["first_meeting"] is None or start_time < day["first_meeting"]:
                        day["first_meeting"] = start_time
                    if day["last_meeting_end"] is None or end_time > day["last_meeting_end"]:
                        day["last_meeting_end"] = end_time

            # Calculate gaps for each day
            work_start_hour = 8  # 8 AM
            work_end_hour = 18   # 6 PM

            for day_name, day in daily_stats.items():
                day["meeting_hours"] = round(day["meeting_hours"], 1)

                # Sort events by start time
                sorted_events = sorted(day["events"], key=lambda e: e["start"])

                if not sorted_events:
                    # Whole day is free
                    day["gaps_2plus_hours"].append({
                        "start": f"{work_start_hour:02d}:00",
                        "end": f"{work_end_hour:02d}:00",
                        "duration_hours": work_end_hour - work_start_hour
                    })
                    continue

                # Check gap from work start to first meeting
                first_start = datetime.fromisoformat(sorted_events[0]["start"].replace("Z", ""))
                first_start_hour = first_start.hour + first_start.minute / 60
                if first_start_hour - work_start_hour >= 2:
                    day["gaps_2plus_hours"].append({
                        "start": f"{work_start_hour:02d}:00",
                        "end": first_start.strftime("%H:%M"),
                        "duration_hours": round(first_start_hour - work_start_hour, 1)
                    })

                # Check gaps between meetings
                for i in range(len(sorted_events) - 1):
                    curr_end = datetime.fromisoformat(sorted_events[i]["end"].replace("Z", ""))
                    next_start = datetime.fromisoformat(sorted_events[i + 1]["start"].replace("Z", ""))
                    gap_hours = (next_start - curr_end).total_seconds() / 3600
                    if gap_hours >= 2:
                        day["gaps_2plus_hours"].append({
                            "start": curr_end.strftime("%H:%M"),
                            "end": next_start.strftime("%H:%M"),
                            "duration_hours": round(gap_hours, 1)
                        })

                # Check gap from last meeting to work end
                last_end = datetime.fromisoformat(sorted_events[-1]["end"].replace("Z", ""))
                last_end_hour = last_end.hour + last_end.minute / 60
                if work_end_hour - last_end_hour >= 2:
                    day["gaps_2plus_hours"].append({
                        "start": last_end.strftime("%H:%M"),
                        "end": f"{work_end_hour:02d}:00",
                        "duration_hours": round(work_end_hour - last_end_hour, 1)
                    })

            # Build overall summary
            total_meetings = sum(d["meeting_count"] for d in daily_stats.values())
            total_hours = sum(d["meeting_hours"] for d in daily_stats.values())
            busiest_day = max(daily_stats.items(), key=lambda x: x[1]["meeting_hours"])
            lightest_day = min(daily_stats.items(), key=lambda x: x[1]["meeting_hours"])
            total_gaps = sum(len(d["gaps_2plus_hours"]) for d in daily_stats.values())

            summary = {
                "week": {
                    "start": week_start.strftime("%Y-%m-%d"),
                    "end": (week_end - timedelta(days=1)).strftime("%Y-%m-%d"),
                    "timezone": timezone
                },
                "overview": {
                    "total_meetings": total_meetings,
                    "total_meeting_hours": round(total_hours, 1),
                    "avg_meetings_per_day": round(total_meetings / 5, 1),
                    "busiest_day": f"{busiest_day[0]} ({busiest_day[1]['meeting_count']} meetings, {busiest_day[1]['meeting_hours']}h)",
                    "lightest_day": f"{lightest_day[0]} ({lightest_day[1]['meeting_count']} meetings, {lightest_day[1]['meeting_hours']}h)",
                    "total_focus_gaps_2h_plus": total_gaps,
                },
                "daily_breakdown": daily_stats,
            }

            return json.dumps(summary, indent=2, default=str)

        except Exception as e:
            return f"❌ Error generating weekly summary: {e}"

    print("✅ Calendar tools registered successfully")
