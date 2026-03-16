"""
Notion Integration Tools for Crowd IT MCP Server

Provides full Notion workspace management via the Notion REST API.

Capabilities:
- Create, read, update pages
- Create databases with typed properties
- Append blocks (paragraphs, headings, callouts, dividers, etc.)
- Search across workspace
- List and query databases

Environment Variables:
    NOTION_API_KEY: Notion integration token (starts with secret_ or ntn_)
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# =============================================================================
# Configuration
# =============================================================================

class NotionConfig:
    """Notion API configuration."""

    def __init__(self):
        self._api_key: Optional[str] = None

    @property
    def api_key(self) -> str:
        if self._api_key:
            return self._api_key

        # Try Secret Manager first
        try:
            from app.core.config import get_secret_sync
            secret = get_secret_sync("NOTION_API_KEY")
            if secret:
                self._api_key = secret
                return secret
        except Exception:
            pass

        self._api_key = os.getenv("NOTION_API_KEY", "")
        return self._api_key

    @property
    def is_configured(self) -> bool:
        key = self.api_key
        return bool(key) and (key.startswith("secret_") or key.startswith("ntn_"))

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def get(self, path: str, params: dict = None) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{NOTION_API_BASE}{path}",
                headers=self._headers(),
                params=params or {},
            )
            response.raise_for_status()
            return response.json()

    async def post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{NOTION_API_BASE}{path}",
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
            return response.json()

    async def patch(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(
                f"{NOTION_API_BASE}{path}",
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
            return response.json()


# =============================================================================
# Helpers
# =============================================================================

def _rich_text(content: str) -> list:
    """Build a rich_text array from a plain string."""
    return [{"type": "text", "text": {"content": content}}]


def _format_page(page: dict) -> dict:
    """Extract useful fields from a Notion page object."""
    result = {
        "id": page.get("id", ""),
        "url": page.get("url", ""),
        "created_time": page.get("created_time", ""),
        "last_edited_time": page.get("last_edited_time", ""),
        "archived": page.get("archived", False),
    }

    # Title - pages can have title in different places
    props = page.get("properties", {})
    for prop_name, prop_val in props.items():
        if prop_val.get("type") == "title":
            title_parts = prop_val.get("title", [])
            result["title"] = "".join(t.get("plain_text", "") for t in title_parts)
            break

    # Icon
    icon = page.get("icon")
    if icon:
        if icon.get("type") == "emoji":
            result["icon"] = icon.get("emoji", "")
        elif icon.get("type") == "external":
            result["icon"] = icon.get("external", {}).get("url", "")

    # Parent
    parent = page.get("parent", {})
    parent_type = parent.get("type", "")
    if parent_type == "page_id":
        result["parent_id"] = parent.get("page_id", "")
        result["parent_type"] = "page"
    elif parent_type == "database_id":
        result["parent_id"] = parent.get("database_id", "")
        result["parent_type"] = "database"
    elif parent_type == "workspace":
        result["parent_type"] = "workspace"

    return result


def _format_database(db: dict) -> dict:
    """Extract useful fields from a Notion database object."""
    title_parts = db.get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_parts)

    result = {
        "id": db.get("id", ""),
        "title": title,
        "url": db.get("url", ""),
        "created_time": db.get("created_time", ""),
        "last_edited_time": db.get("last_edited_time", ""),
        "is_inline": db.get("is_inline", False),
    }

    icon = db.get("icon")
    if icon and icon.get("type") == "emoji":
        result["icon"] = icon.get("emoji", "")

    # Property names and types
    properties = db.get("properties", {})
    result["properties"] = {
        name: prop.get("type", "") for name, prop in properties.items()
    }

    return result


def _parse_property_value(prop: dict) -> Any:
    """Parse a Notion property value to a Python-friendly format."""
    prop_type = prop.get("type", "")

    if prop_type == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    elif prop_type == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    elif prop_type == "number":
        return prop.get("number")
    elif prop_type == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    elif prop_type == "multi_select":
        return [o.get("name") for o in prop.get("multi_select", [])]
    elif prop_type == "date":
        date_val = prop.get("date")
        if date_val:
            return date_val.get("start")
        return None
    elif prop_type == "checkbox":
        return prop.get("checkbox", False)
    elif prop_type == "url":
        return prop.get("url")
    elif prop_type == "email":
        return prop.get("email")
    elif prop_type == "phone_number":
        return prop.get("phone_number")
    elif prop_type == "people":
        return [p.get("name", p.get("id", "")) for p in prop.get("people", [])]
    elif prop_type == "relation":
        return [r.get("id") for r in prop.get("relation", [])]
    elif prop_type == "status":
        status = prop.get("status")
        return status.get("name") if status else None
    else:
        return f"[{prop_type}]"


def _build_block(block_type: str, content: str, level: int = 1) -> dict:
    """Build a Notion block object from type and content string."""
    rt = _rich_text(content)

    if block_type == "paragraph":
        return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt}}
    elif block_type in ("heading_1", "heading_2", "heading_3"):
        return {"object": "block", "type": block_type, block_type: {"rich_text": rt}}
    elif block_type == "heading":
        t = f"heading_{min(max(level, 1), 3)}"
        return {"object": "block", "type": t, t: {"rich_text": rt}}
    elif block_type == "bulleted_list_item":
        return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt}}
    elif block_type == "numbered_list_item":
        return {"object": "block", "type": "numbered_list_item", "numbered_list_item": {"rich_text": rt}}
    elif block_type == "to_do":
        return {"object": "block", "type": "to_do", "to_do": {"rich_text": rt, "checked": False}}
    elif block_type == "quote":
        return {"object": "block", "type": "quote", "quote": {"rich_text": rt}}
    elif block_type == "callout":
        return {"object": "block", "type": "callout", "callout": {"rich_text": rt, "icon": {"type": "emoji", "emoji": "💡"}}}
    elif block_type == "divider":
        return {"object": "block", "type": "divider", "divider": {}}
    elif block_type == "code":
        return {"object": "block", "type": "code", "code": {"rich_text": rt, "language": "plain text"}}
    else:
        # Default to paragraph
        return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt}}


def _parse_properties_json(properties_json: str) -> dict:
    """Parse a JSON string of database properties into Notion format."""
    try:
        props = json.loads(properties_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid properties JSON: {e}")

    notion_props = {}
    for name, config in props.items():
        prop_type = config.get("type", "rich_text")

        if prop_type == "title":
            notion_props[name] = {"title": {}}
        elif prop_type == "rich_text":
            notion_props[name] = {"rich_text": {}}
        elif prop_type == "number":
            fmt = config.get("format", "number")
            notion_props[name] = {"number": {"format": fmt}}
        elif prop_type == "select":
            options = [
                {"name": o} if isinstance(o, str) else o
                for o in config.get("options", [])
            ]
            notion_props[name] = {"select": {"options": options}}
        elif prop_type == "multi_select":
            options = [
                {"name": o} if isinstance(o, str) else o
                for o in config.get("options", [])
            ]
            notion_props[name] = {"multi_select": {"options": options}}
        elif prop_type == "date":
            notion_props[name] = {"date": {}}
        elif prop_type == "checkbox":
            notion_props[name] = {"checkbox": {}}
        elif prop_type == "url":
            notion_props[name] = {"url": {}}
        elif prop_type == "email":
            notion_props[name] = {"email": {}}
        elif prop_type == "phone_number":
            notion_props[name] = {"phone_number": {}}
        elif prop_type == "people":
            notion_props[name] = {"people": {}}
        elif prop_type == "files":
            notion_props[name] = {"files": {}}
        elif prop_type == "relation":
            db_id = config.get("database_id", "")
            if db_id:
                notion_props[name] = {"relation": {"database_id": db_id}}
        elif prop_type == "status":
            notion_props[name] = {"status": {}}
        else:
            notion_props[name] = {"rich_text": {}}

    return notion_props


# =============================================================================
# Tool Registration
# =============================================================================

def register_notion_tools(mcp, notion_config: 'NotionConfig'):
    """Register all Notion tools with the MCP server."""

    # =========================================================================
    # SEARCH
    # =========================================================================

    @mcp.tool(
        name="notion_search",
        annotations={
            "title": "Search Notion Workspace",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def notion_search(
        query: str = "",
        filter_type: Optional[str] = None,
        page_size: int = 20,
    ) -> str:
        """Search across the Notion workspace for pages and databases.

        Args:
            query: Search query string. Leave empty to list all accessible content.
            filter_type: Optional filter - 'page' or 'database'.
            page_size: Number of results to return (default 20, max 100).

        Returns matching pages and databases with IDs and URLs.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            body: Dict[str, Any] = {"page_size": min(page_size, 100)}
            if query:
                body["query"] = query
            if filter_type in ("page", "database"):
                body["filter"] = {"value": filter_type, "property": "object"}

            data = await notion_config.post("/search", body)

            results = []
            for item in data.get("results", []):
                obj_type = item.get("object", "")
                if obj_type == "page":
                    formatted = _format_page(item)
                    formatted["object"] = "page"
                else:
                    formatted = _format_database(item)
                    formatted["object"] = "database"
                results.append(formatted)

            return json.dumps({
                "total": len(results),
                "has_more": data.get("has_more", False),
                "results": results,
            }, indent=2)

        except httpx.HTTPStatusError as e:
            return f"Error searching Notion: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error searching Notion: {str(e)}"

    # =========================================================================
    # GET PAGE
    # =========================================================================

    @mcp.tool(
        name="notion_get_page",
        annotations={
            "title": "Get Notion Page",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def notion_get_page(
        page_id: str,
        include_content: bool = False,
    ) -> str:
        """Get a Notion page's properties and optionally its block content.

        Args:
            page_id: The Notion page ID (UUID with or without dashes) or page URL.
            include_content: Whether to fetch the page's block content (default False).

        Returns page metadata, properties, and optionally block content.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            # Clean page ID - strip URL if needed
            page_id = page_id.strip()
            if "notion.so" in page_id:
                # Extract ID from URL
                parts = page_id.rstrip("/").split("/")
                last = parts[-1]
                # Handle ?v= params
                last = last.split("?")[0]
                # ID is the last 32 chars (with or without dashes)
                page_id = last.replace("-", "")[-32:]
                # Reformat as UUID
                page_id = f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}"

            data = await notion_config.get(f"/pages/{page_id}")
            result = _format_page(data)

            # Parse all properties
            parsed_props = {}
            for prop_name, prop_val in data.get("properties", {}).items():
                parsed_props[prop_name] = _parse_property_value(prop_val)
            result["properties"] = parsed_props

            if include_content:
                blocks_data = await notion_config.get(f"/blocks/{page_id}/children", {"page_size": 100})
                blocks = []
                for block in blocks_data.get("results", []):
                    b_type = block.get("type", "")
                    b_content = block.get(b_type, {})
                    rt = b_content.get("rich_text", [])
                    text = "".join(t.get("plain_text", "") for t in rt)
                    blocks.append({
                        "id": block.get("id", ""),
                        "type": b_type,
                        "text": text,
                        "has_children": block.get("has_children", False),
                    })
                result["blocks"] = blocks
                result["block_count"] = len(blocks)

            return json.dumps(result, indent=2)

        except httpx.HTTPStatusError as e:
            return f"Error getting Notion page: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error getting Notion page: {str(e)}"

    # =========================================================================
    # CREATE PAGE
    # =========================================================================

    @mcp.tool(
        name="notion_create_page",
        annotations={
            "title": "Create Notion Page",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def notion_create_page(
        parent_id: str,
        title: str,
        emoji: Optional[str] = None,
        content_blocks: Optional[str] = None,
        parent_type: str = "page",
    ) -> str:
        """Create a new Notion page.

        Args:
            parent_id: ID of the parent page or database.
            title: Page title.
            emoji: Optional emoji icon (e.g. "📋", "🔬").
            content_blocks: Optional JSON array of block objects to add as page content.
                Each block: {"type": "paragraph|heading_1|heading_2|heading_3|bulleted_list_item|
                numbered_list_item|to_do|quote|callout|divider|code", "content": "text"}
                For divider blocks, content is ignored.
                Example: [{"type": "heading_2", "content": "Overview"}, {"type": "paragraph", "content": "Details here"}]
            parent_type: "page" (default) or "database" - type of parent.

        Returns the created page ID and URL.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            if parent_type == "database":
                parent = {"database_id": parent_id}
                properties = {
                    "Name": {"title": _rich_text(title)}
                }
            else:
                parent = {"page_id": parent_id}
                properties = {
                    "title": {"title": _rich_text(title)}
                }

            body: Dict[str, Any] = {
                "parent": parent,
                "properties": properties,
            }

            if emoji:
                body["icon"] = {"type": "emoji", "emoji": emoji}

            if content_blocks:
                try:
                    blocks_input = json.loads(content_blocks)
                    children = []
                    for b in blocks_input:
                        b_type = b.get("type", "paragraph")
                        b_content = b.get("content", "")
                        b_level = b.get("level", 1)
                        children.append(_build_block(b_type, b_content, b_level))
                    if children:
                        body["children"] = children
                except json.JSONDecodeError as e:
                    return f"Error parsing content_blocks JSON: {e}"

            data = await notion_config.post("/pages", body)
            result = _format_page(data)
            result["_status"] = "created"

            return json.dumps(result, indent=2)

        except httpx.HTTPStatusError as e:
            return f"Error creating Notion page: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error creating Notion page: {str(e)}"

    # =========================================================================
    # UPDATE PAGE
    # =========================================================================

    @mcp.tool(
        name="notion_update_page",
        annotations={
            "title": "Update Notion Page",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def notion_update_page(
        page_id: str,
        title: Optional[str] = None,
        emoji: Optional[str] = None,
        archived: Optional[bool] = None,
    ) -> str:
        """Update a Notion page's title, icon, or archive status.

        Args:
            page_id: The Notion page ID to update.
            title: New page title.
            emoji: New emoji icon.
            archived: Set to True to archive (soft delete), False to unarchive.

        Returns the updated page details.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            body: Dict[str, Any] = {}

            if title is not None:
                # Try both title formats (page vs database row)
                body["properties"] = {
                    "title": {"title": _rich_text(title)},
                    "Name": {"title": _rich_text(title)},
                }

            if emoji is not None:
                body["icon"] = {"type": "emoji", "emoji": emoji}

            if archived is not None:
                body["archived"] = archived

            if not body:
                return "Error: No update fields provided."

            data = await notion_config.patch(f"/pages/{page_id}", body)
            result = _format_page(data)
            result["_status"] = "updated"

            return json.dumps(result, indent=2)

        except httpx.HTTPStatusError as e:
            return f"Error updating Notion page: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error updating Notion page: {str(e)}"

    # =========================================================================
    # APPEND BLOCKS
    # =========================================================================

    @mcp.tool(
        name="notion_append_blocks",
        annotations={
            "title": "Append Blocks to Notion Page",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def notion_append_blocks(
        page_id: str,
        blocks: str,
    ) -> str:
        """Append blocks to a Notion page or block.

        Args:
            page_id: The Notion page or block ID to append to.
            blocks: JSON array of block objects to append.
                Each block: {"type": "paragraph|heading_1|heading_2|heading_3|bulleted_list_item|
                numbered_list_item|to_do|quote|callout|divider|code", "content": "text"}
                For divider blocks, content can be empty string.
                Example: [
                    {"type": "heading_2", "content": "Section Title"},
                    {"type": "paragraph", "content": "Body text here"},
                    {"type": "bulleted_list_item", "content": "List item"},
                    {"type": "divider", "content": ""}
                ]

        Returns the IDs of newly created blocks.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            blocks_input = json.loads(blocks)
            children = []
            for b in blocks_input:
                b_type = b.get("type", "paragraph")
                b_content = b.get("content", "")
                b_level = b.get("level", 1)
                children.append(_build_block(b_type, b_content, b_level))

            if not children:
                return "Error: No valid blocks provided."

            data = await notion_config.patch(f"/blocks/{page_id}/children", {"children": children})

            created = []
            for block in data.get("results", []):
                b_type = block.get("type", "")
                b_content = block.get(b_type, {})
                rt = b_content.get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in rt)
                created.append({
                    "id": block.get("id", ""),
                    "type": b_type,
                    "text": text[:100],
                })

            return json.dumps({
                "_status": "blocks_appended",
                "count": len(created),
                "blocks": created,
            }, indent=2)

        except json.JSONDecodeError as e:
            return f"Error parsing blocks JSON: {e}"
        except httpx.HTTPStatusError as e:
            return f"Error appending blocks: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error appending blocks: {str(e)}"

    # =========================================================================
    # CREATE DATABASE
    # =========================================================================

    @mcp.tool(
        name="notion_create_database",
        annotations={
            "title": "Create Notion Database",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def notion_create_database(
        parent_id: str,
        title: str,
        properties: str,
        emoji: Optional[str] = None,
        is_inline: bool = False,
    ) -> str:
        """Create a new Notion database inside a page.

        Args:
            parent_id: ID of the parent page.
            title: Database title.
            properties: JSON object defining database properties/columns.
                Always include a "title" type property (the main title column).
                Supported types: title, rich_text, number, select, multi_select, date,
                                  checkbox, url, email, phone_number, people, files,
                                  relation, status
                For select/multi_select, include options: [{"name": "Option1", "color": "blue"}, ...]
                Available colors: default, gray, brown, orange, yellow, green, blue, purple, pink, red
                Example:
                {
                    "Name": {"type": "title"},
                    "Status": {"type": "select", "options": [{"name": "Active", "color": "green"}, {"name": "Archived", "color": "gray"}]},
                    "Date": {"type": "date"},
                    "Owner": {"type": "people"},
                    "Tags": {"type": "multi_select", "options": [{"name": "Research"}, {"name": "Clinical"}]}
                }
            emoji: Optional emoji icon for the database.
            is_inline: Whether the database appears inline in the parent page (default False).

        Returns the created database ID and URL.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            notion_props = _parse_properties_json(properties)

            # Ensure at least one title property exists
            has_title = any(
                "title" in prop for prop in notion_props.values()
            )
            if not has_title:
                notion_props["Name"] = {"title": {}}

            body: Dict[str, Any] = {
                "parent": {"type": "page_id", "page_id": parent_id},
                "title": _rich_text(title),
                "properties": notion_props,
                "is_inline": is_inline,
            }

            if emoji:
                body["icon"] = {"type": "emoji", "emoji": emoji}

            data = await notion_config.post("/databases", body)
            result = _format_database(data)
            result["_status"] = "created"

            return json.dumps(result, indent=2)

        except ValueError as e:
            return f"Error: {e}"
        except httpx.HTTPStatusError as e:
            return f"Error creating Notion database: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error creating Notion database: {str(e)}"

    # =========================================================================
    # GET DATABASE
    # =========================================================================

    @mcp.tool(
        name="notion_get_database",
        annotations={
            "title": "Get Notion Database",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def notion_get_database(
        database_id: str,
    ) -> str:
        """Get a Notion database's schema and metadata.

        Args:
            database_id: The Notion database ID.

        Returns the database title, properties schema, and metadata.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            data = await notion_config.get(f"/databases/{database_id}")
            result = _format_database(data)

            # Include full property details
            full_props = {}
            for name, prop in data.get("properties", {}).items():
                prop_type = prop.get("type", "")
                prop_detail: Dict[str, Any] = {"type": prop_type, "id": prop.get("id", "")}

                if prop_type == "select":
                    prop_detail["options"] = [
                        {"name": o.get("name"), "color": o.get("color")}
                        for o in prop.get("select", {}).get("options", [])
                    ]
                elif prop_type == "multi_select":
                    prop_detail["options"] = [
                        {"name": o.get("name"), "color": o.get("color")}
                        for o in prop.get("multi_select", {}).get("options", [])
                    ]
                elif prop_type == "number":
                    prop_detail["format"] = prop.get("number", {}).get("format", "number")
                elif prop_type == "relation":
                    prop_detail["related_database_id"] = prop.get("relation", {}).get("database_id", "")

                full_props[name] = prop_detail

            result["properties"] = full_props
            return json.dumps(result, indent=2)

        except httpx.HTTPStatusError as e:
            return f"Error getting Notion database: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error getting Notion database: {str(e)}"

    # =========================================================================
    # QUERY DATABASE
    # =========================================================================

    @mcp.tool(
        name="notion_query_database",
        annotations={
            "title": "Query Notion Database",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def notion_query_database(
        database_id: str,
        filter_json: Optional[str] = None,
        sorts_json: Optional[str] = None,
        page_size: int = 25,
    ) -> str:
        """Query rows from a Notion database with optional filtering and sorting.

        Args:
            database_id: The Notion database ID to query.
            filter_json: Optional Notion filter object as JSON string.
                Example (single condition):
                {"property": "Status", "select": {"equals": "Active"}}
                Example (compound):
                {"and": [{"property": "Status", "select": {"equals": "Active"}}, {"property": "Date", "date": {"is_not_empty": true}}]}
            sorts_json: Optional array of sort objects as JSON string.
                Example: [{"property": "Date", "direction": "descending"}]
            page_size: Number of results to return (default 25, max 100).

        Returns database rows with their property values.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            body: Dict[str, Any] = {"page_size": min(page_size, 100)}

            if filter_json:
                body["filter"] = json.loads(filter_json)

            if sorts_json:
                body["sorts"] = json.loads(sorts_json)

            data = await notion_config.post(f"/databases/{database_id}/query", body)

            rows = []
            for page in data.get("results", []):
                row: Dict[str, Any] = {
                    "id": page.get("id", ""),
                    "url": page.get("url", ""),
                    "created_time": page.get("created_time", ""),
                    "last_edited_time": page.get("last_edited_time", ""),
                }
                props = {}
                for prop_name, prop_val in page.get("properties", {}).items():
                    props[prop_name] = _parse_property_value(prop_val)
                row["properties"] = props
                rows.append(row)

            return json.dumps({
                "total": len(rows),
                "has_more": data.get("has_more", False),
                "results": rows,
            }, indent=2)

        except json.JSONDecodeError as e:
            return f"Error parsing filter/sorts JSON: {e}"
        except httpx.HTTPStatusError as e:
            return f"Error querying Notion database: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error querying Notion database: {str(e)}"

    # =========================================================================
    # CREATE DATABASE ROW (PAGE IN DATABASE)
    # =========================================================================

    @mcp.tool(
        name="notion_create_database_row",
        annotations={
            "title": "Create Notion Database Row",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    async def notion_create_database_row(
        database_id: str,
        properties: str,
        emoji: Optional[str] = None,
        content_blocks: Optional[str] = None,
    ) -> str:
        """Create a new row (page) in a Notion database.

        Args:
            database_id: The Notion database ID to add the row to.
            properties: JSON object of property name -> value pairs.
                The format depends on property type:
                - title/rich_text: {"Name": "My Title", "Notes": "Some text"}
                - select: {"Status": "Active"}
                - multi_select: {"Tags": ["Research", "Clinical"]}
                - date: {"Date": "2025-01-15"}
                - checkbox: {"Active": true}
                - number: {"Count": 42}
                - url: {"Link": "https://example.com"}
                - email: {"Email": "user@example.com"}
                Example:
                {
                    "Name": "Board Meeting Jan 2025",
                    "Date": "2025-01-15",
                    "Type": "Board",
                    "Status": "Draft"
                }
            emoji: Optional emoji icon for the row.
            content_blocks: Optional JSON array of content blocks (same format as notion_create_page).

        Returns the created row's ID and URL.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            # First get the database schema to know property types
            db_data = await notion_config.get(f"/databases/{database_id}")
            db_props = db_data.get("properties", {})

            input_props = json.loads(properties)
            notion_properties: Dict[str, Any] = {}

            for prop_name, value in input_props.items():
                if prop_name not in db_props:
                    # Try case-insensitive match
                    matched = next(
                        (k for k in db_props if k.lower() == prop_name.lower()),
                        None
                    )
                    if not matched:
                        continue
                    prop_name = matched

                prop_schema = db_props[prop_name]
                prop_type = prop_schema.get("type", "rich_text")

                if prop_type == "title":
                    notion_properties[prop_name] = {"title": _rich_text(str(value))}
                elif prop_type == "rich_text":
                    notion_properties[prop_name] = {"rich_text": _rich_text(str(value))}
                elif prop_type == "number":
                    notion_properties[prop_name] = {"number": float(value) if value is not None else None}
                elif prop_type == "select":
                    notion_properties[prop_name] = {"select": {"name": str(value)}}
                elif prop_type == "multi_select":
                    if isinstance(value, list):
                        notion_properties[prop_name] = {"multi_select": [{"name": v} for v in value]}
                    else:
                        notion_properties[prop_name] = {"multi_select": [{"name": str(value)}]}
                elif prop_type == "date":
                    notion_properties[prop_name] = {"date": {"start": str(value)}}
                elif prop_type == "checkbox":
                    notion_properties[prop_name] = {"checkbox": bool(value)}
                elif prop_type == "url":
                    notion_properties[prop_name] = {"url": str(value)}
                elif prop_type == "email":
                    notion_properties[prop_name] = {"email": str(value)}
                elif prop_type == "phone_number":
                    notion_properties[prop_name] = {"phone_number": str(value)}
                elif prop_type == "status":
                    notion_properties[prop_name] = {"status": {"name": str(value)}}
                else:
                    # Fallback to rich_text
                    notion_properties[prop_name] = {"rich_text": _rich_text(str(value))}

            body: Dict[str, Any] = {
                "parent": {"database_id": database_id},
                "properties": notion_properties,
            }

            if emoji:
                body["icon"] = {"type": "emoji", "emoji": emoji}

            if content_blocks:
                try:
                    blocks_input = json.loads(content_blocks)
                    children = []
                    for b in blocks_input:
                        b_type = b.get("type", "paragraph")
                        b_content = b.get("content", "")
                        children.append(_build_block(b_type, b_content))
                    if children:
                        body["children"] = children
                except json.JSONDecodeError as e:
                    return f"Error parsing content_blocks JSON: {e}"

            data = await notion_config.post("/pages", body)
            result = _format_page(data)

            # Also return the parsed properties
            parsed = {}
            for prop_name, prop_val in data.get("properties", {}).items():
                parsed[prop_name] = _parse_property_value(prop_val)
            result["properties"] = parsed
            result["_status"] = "created"

            return json.dumps(result, indent=2)

        except json.JSONDecodeError as e:
            return f"Error parsing properties JSON: {e}"
        except httpx.HTTPStatusError as e:
            return f"Error creating database row: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error creating database row: {str(e)}"

    # =========================================================================
    # UPDATE DATABASE ROW
    # =========================================================================

    @mcp.tool(
        name="notion_update_database_row",
        annotations={
            "title": "Update Notion Database Row",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def notion_update_database_row(
        page_id: str,
        database_id: str,
        properties: str,
    ) -> str:
        """Update properties on an existing Notion database row.

        Args:
            page_id: The page ID of the database row to update.
            database_id: The database ID (needed to look up property schemas).
            properties: JSON object of property name -> new value pairs.
                Uses the same format as notion_create_database_row.

        Returns the updated row's details.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            # Get database schema
            db_data = await notion_config.get(f"/databases/{database_id}")
            db_props = db_data.get("properties", {})

            input_props = json.loads(properties)
            notion_properties: Dict[str, Any] = {}

            for prop_name, value in input_props.items():
                if prop_name not in db_props:
                    matched = next(
                        (k for k in db_props if k.lower() == prop_name.lower()),
                        None
                    )
                    if not matched:
                        continue
                    prop_name = matched

                prop_schema = db_props[prop_name]
                prop_type = prop_schema.get("type", "rich_text")

                if prop_type == "title":
                    notion_properties[prop_name] = {"title": _rich_text(str(value))}
                elif prop_type == "rich_text":
                    notion_properties[prop_name] = {"rich_text": _rich_text(str(value))}
                elif prop_type == "number":
                    notion_properties[prop_name] = {"number": float(value) if value is not None else None}
                elif prop_type == "select":
                    notion_properties[prop_name] = {"select": {"name": str(value)}}
                elif prop_type == "multi_select":
                    if isinstance(value, list):
                        notion_properties[prop_name] = {"multi_select": [{"name": v} for v in value]}
                    else:
                        notion_properties[prop_name] = {"multi_select": [{"name": str(value)}]}
                elif prop_type == "date":
                    notion_properties[prop_name] = {"date": {"start": str(value)}}
                elif prop_type == "checkbox":
                    notion_properties[prop_name] = {"checkbox": bool(value)}
                elif prop_type == "url":
                    notion_properties[prop_name] = {"url": str(value)}
                elif prop_type == "email":
                    notion_properties[prop_name] = {"email": str(value)}
                elif prop_type == "status":
                    notion_properties[prop_name] = {"status": {"name": str(value)}}
                else:
                    notion_properties[prop_name] = {"rich_text": _rich_text(str(value))}

            data = await notion_config.patch(f"/pages/{page_id}", {"properties": notion_properties})
            result = _format_page(data)

            parsed = {}
            for prop_name, prop_val in data.get("properties", {}).items():
                parsed[prop_name] = _parse_property_value(prop_val)
            result["properties"] = parsed
            result["_status"] = "updated"

            return json.dumps(result, indent=2)

        except json.JSONDecodeError as e:
            return f"Error parsing properties JSON: {e}"
        except httpx.HTTPStatusError as e:
            return f"Error updating database row: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error updating database row: {str(e)}"

    # =========================================================================
    # GET BLOCK CHILDREN
    # =========================================================================

    @mcp.tool(
        name="notion_get_block_children",
        annotations={
            "title": "Get Notion Block Children",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    async def notion_get_block_children(
        block_id: str,
        page_size: int = 100,
    ) -> str:
        """Get the child blocks of a Notion page or block.

        Args:
            block_id: The block or page ID to get children of.
            page_size: Number of blocks to return (default 100, max 100).

        Returns a list of child blocks with their type and text content.
        """
        if not notion_config.is_configured:
            return "Error: Notion not configured. Set NOTION_API_KEY in Secret Manager."

        try:
            data = await notion_config.get(
                f"/blocks/{block_id}/children",
                {"page_size": min(page_size, 100)}
            )

            blocks = []
            for block in data.get("results", []):
                b_type = block.get("type", "")
                b_content = block.get(b_type, {})
                rt = b_content.get("rich_text", [])
                text = "".join(t.get("plain_text", "") for t in rt)
                blocks.append({
                    "id": block.get("id", ""),
                    "type": b_type,
                    "text": text,
                    "has_children": block.get("has_children", False),
                    "created_time": block.get("created_time", ""),
                })

            return json.dumps({
                "count": len(blocks),
                "has_more": data.get("has_more", False),
                "blocks": blocks,
            }, indent=2)

        except httpx.HTTPStatusError as e:
            return f"Error getting block children: HTTP {e.response.status_code} - {e.response.text}"
        except Exception as e:
            return f"Error getting block children: {str(e)}"
