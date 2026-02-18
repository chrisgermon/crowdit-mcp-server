"""
Linear Integration Tools for Crowd IT MCP Server

This module provides comprehensive Linear project management capabilities
using the Linear GraphQL API.

Capabilities:
- Search and filter issues with powerful query options
- Get, create, update, and archive issues
- Add comments to issues
- List teams, projects, cycles, labels, and users
- Get workflow states for teams
- View authenticated user info

Authentication: Uses a Personal API key passed via Bearer token.

Environment Variables:
    LINEAR_API_KEY: Personal API key from Linear (Settings > Account > Security & Access)
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

LINEAR_GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"


# =============================================================================
# Configuration and Authentication
# =============================================================================

class LinearConfig:
    """Linear API configuration using Personal API key."""

    def __init__(self):
        self._api_key: Optional[str] = None

    @property
    def api_key(self) -> str:
        if self._api_key:
            return self._api_key

        # Try Secret Manager first
        try:
            from app.core.config import get_secret_sync
            secret = get_secret_sync("LINEAR_API_KEY")
            if secret:
                self._api_key = secret
                return secret
        except Exception:
            pass

        self._api_key = os.getenv("LINEAR_API_KEY", "")
        return self._api_key

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def graphql_request(self, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL request against the Linear API."""
        import httpx

        payload: Dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                LINEAR_GRAPHQL_ENDPOINT,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"{self.api_key}",
                }
            )
            response.raise_for_status()
            result = response.json()

            # Check for GraphQL-level errors
            if "errors" in result:
                error_messages = [e.get("message", str(e)) for e in result["errors"]]
                raise Exception(f"GraphQL errors: {'; '.join(error_messages)}")

            return result.get("data", {})


# =============================================================================
# Helper Functions
# =============================================================================

def _format_issue(issue: dict) -> dict:
    """Format a Linear issue into a clean summary."""
    result = {
        "id": issue.get("id", ""),
        "identifier": issue.get("identifier", ""),
        "title": issue.get("title", ""),
        "url": issue.get("url", ""),
        "priority": issue.get("priority", 0),
        "priorityLabel": issue.get("priorityLabel", ""),
        "createdAt": issue.get("createdAt", ""),
        "updatedAt": issue.get("updatedAt", ""),
    }

    # State
    state = issue.get("state")
    if state:
        result["state"] = state.get("name", "")
        result["stateType"] = state.get("type", "")

    # Assignee
    assignee = issue.get("assignee")
    result["assignee"] = assignee.get("name", "") if assignee else "Unassigned"

    # Team
    team = issue.get("team")
    if team:
        result["team"] = team.get("name", "")

    # Project
    project = issue.get("project")
    if project:
        result["project"] = project.get("name", "")

    # Cycle
    cycle = issue.get("cycle")
    if cycle:
        result["cycle"] = cycle.get("name", "") or cycle.get("number", "")

    # Labels
    labels = issue.get("labels", {})
    if labels and labels.get("nodes"):
        result["labels"] = [l.get("name", "") for l in labels["nodes"]]

    # Description (only if present and non-empty)
    description = issue.get("description")
    if description:
        result["description"] = description

    # Due date
    due_date = issue.get("dueDate")
    if due_date:
        result["dueDate"] = due_date

    # Estimate
    estimate = issue.get("estimate")
    if estimate is not None:
        result["estimate"] = estimate

    return result


def _format_project(project: dict) -> dict:
    """Format a Linear project into a clean summary."""
    result = {
        "id": project.get("id", ""),
        "name": project.get("name", ""),
        "url": project.get("url", ""),
        "state": project.get("state", ""),
        "progress": project.get("progress", 0),
        "createdAt": project.get("createdAt", ""),
        "updatedAt": project.get("updatedAt", ""),
    }

    description = project.get("description")
    if description:
        result["description"] = description

    lead = project.get("lead")
    if lead:
        result["lead"] = lead.get("name", "")

    start_date = project.get("startDate")
    if start_date:
        result["startDate"] = start_date

    target_date = project.get("targetDate")
    if target_date:
        result["targetDate"] = target_date

    return result


# =============================================================================
# Issue fragment for consistent field selection
# =============================================================================

ISSUE_FIELDS = """
    id
    identifier
    title
    url
    priority
    priorityLabel
    createdAt
    updatedAt
    dueDate
    estimate
    description
    state { id name type }
    assignee { id name email }
    team { id name key }
    project { id name }
    cycle { id name number }
    labels { nodes { id name } }
"""

ISSUE_FIELDS_BRIEF = """
    id
    identifier
    title
    url
    priority
    priorityLabel
    createdAt
    updatedAt
    state { name type }
    assignee { name }
    team { name key }
    labels { nodes { name } }
"""


# =============================================================================
# Tool Registration
# =============================================================================

def register_linear_tools(mcp, linear_config: 'LinearConfig'):
    """Register all Linear tools with the MCP server."""

    # =========================================================================
    # VIEWER (AUTHENTICATED USER)
    # =========================================================================

    @mcp.tool(
        name="linear_get_viewer",
        annotations={
            "title": "Get Linear Authenticated User",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_get_viewer() -> str:
        """Get the currently authenticated Linear user's profile.

        Returns the user's name, email, display name, and active status.
        Useful for verifying the connection and getting the current user's ID.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            data = await linear_config.graphql_request("""
                query Viewer {
                    viewer {
                        id
                        name
                        email
                        displayName
                        active
                        admin
                        createdAt
                        organization { id name urlKey }
                    }
                }
            """)

            viewer = data.get("viewer", {})
            org = viewer.get("organization", {})

            return json.dumps({
                "user": {
                    "id": viewer.get("id", ""),
                    "name": viewer.get("name", ""),
                    "email": viewer.get("email", ""),
                    "displayName": viewer.get("displayName", ""),
                    "active": viewer.get("active", False),
                    "admin": viewer.get("admin", False),
                    "createdAt": viewer.get("createdAt", ""),
                },
                "organization": {
                    "id": org.get("id", ""),
                    "name": org.get("name", ""),
                    "urlKey": org.get("urlKey", ""),
                }
            }, indent=2)

        except Exception as e:
            return f"Error getting Linear viewer: {str(e)}"

    # =========================================================================
    # TEAMS
    # =========================================================================

    @mcp.tool(
        name="linear_list_teams",
        annotations={
            "title": "List Linear Teams",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_list_teams() -> str:
        """List all teams in the Linear workspace.

        Returns team names, keys, descriptions, and member counts.
        Team IDs are needed when creating issues.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            data = await linear_config.graphql_request("""
                query Teams {
                    teams {
                        nodes {
                            id
                            name
                            key
                            description
                            private
                            createdAt
                            members { nodes { id name } }
                            states { nodes { id name type position } }
                        }
                    }
                }
            """)

            teams = []
            for t in data.get("teams", {}).get("nodes", []):
                team = {
                    "id": t.get("id", ""),
                    "name": t.get("name", ""),
                    "key": t.get("key", ""),
                    "description": t.get("description", ""),
                    "private": t.get("private", False),
                    "memberCount": len(t.get("members", {}).get("nodes", [])),
                    "members": [m.get("name", "") for m in t.get("members", {}).get("nodes", [])],
                    "states": [
                        {"id": s.get("id"), "name": s.get("name"), "type": s.get("type")}
                        for s in sorted(
                            t.get("states", {}).get("nodes", []),
                            key=lambda s: s.get("position", 0)
                        )
                    ],
                }
                teams.append(team)

            return json.dumps({"total": len(teams), "teams": teams}, indent=2)

        except Exception as e:
            return f"Error listing Linear teams: {str(e)}"

    # =========================================================================
    # WORKFLOW STATES
    # =========================================================================

    @mcp.tool(
        name="linear_list_workflow_states",
        annotations={
            "title": "List Linear Workflow States",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_list_workflow_states(
        team_id: Optional[str] = None
    ) -> str:
        """List workflow states, optionally filtered by team.

        Args:
            team_id: Optional team ID to filter states for a specific team.

        Returns workflow states with their names, types (triage, backlog, unstarted,
        started, completed, cancelled), and positions.
        State IDs are needed when creating or updating issues.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            if team_id:
                query = """
                    query WorkflowStates($teamId: String!) {
                        workflowStates(filter: { team: { id: { eq: $teamId } } }) {
                            nodes { id name type position team { id name key } }
                        }
                    }
                """
                variables = {"teamId": team_id}
            else:
                query = """
                    query WorkflowStates {
                        workflowStates {
                            nodes { id name type position team { id name key } }
                        }
                    }
                """
                variables = None

            data = await linear_config.graphql_request(query, variables)

            states = []
            for s in data.get("workflowStates", {}).get("nodes", []):
                team = s.get("team", {})
                states.append({
                    "id": s.get("id", ""),
                    "name": s.get("name", ""),
                    "type": s.get("type", ""),
                    "position": s.get("position", 0),
                    "team": team.get("name", "") if team else "",
                    "teamKey": team.get("key", "") if team else "",
                })

            # Sort by team then position
            states.sort(key=lambda s: (s.get("team", ""), s.get("position", 0)))

            return json.dumps({"total": len(states), "states": states}, indent=2)

        except Exception as e:
            return f"Error listing workflow states: {str(e)}"

    # =========================================================================
    # ISSUE SEARCH
    # =========================================================================

    @mcp.tool(
        name="linear_search_issues",
        annotations={
            "title": "Search Linear Issues",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_search_issues(
        query: Optional[str] = None,
        team_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        state_name: Optional[str] = None,
        state_type: Optional[str] = None,
        priority: Optional[int] = None,
        label_name: Optional[str] = None,
        project_id: Optional[str] = None,
        first: int = 25
    ) -> str:
        """Search and filter Linear issues.

        Args:
            query: Full-text search query to match against issue title and description.
            team_id: Filter by team ID.
            assignee_id: Filter by assignee user ID. Use 'me' for the authenticated user.
            state_name: Filter by workflow state name (e.g., "In Progress", "Done").
            state_type: Filter by state type: 'triage', 'backlog', 'unstarted', 'started', 'completed', 'cancelled'.
            priority: Filter by priority level (0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low).
            label_name: Filter by label name.
            project_id: Filter by project ID.
            first: Number of results to return (default 25, max 50).

        Returns a list of matching issues with key details.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            # Build the filter object
            filters = {}

            if team_id:
                filters["team"] = {"id": {"eq": team_id}}
            if assignee_id:
                if assignee_id.lower() == "me":
                    filters["assignee"] = {"isMe": {"eq": True}}
                else:
                    filters["assignee"] = {"id": {"eq": assignee_id}}
            if state_name:
                filters["state"] = {"name": {"eqIgnoreCase": state_name}}
            elif state_type:
                filters["state"] = {"type": {"eq": state_type}}
            if priority is not None:
                filters["priority"] = {"eq": priority}
            if label_name:
                filters["labels"] = {"name": {"eqIgnoreCase": label_name}}
            if project_id:
                filters["project"] = {"id": {"eq": project_id}}

            first = min(first, 50)

            if query:
                # Use the searchIssues query for full-text search
                gql_query = f"""
                    query SearchIssues($query: String!, $first: Int) {{
                        searchIssues(term: $query, first: $first) {{
                            nodes {{
                                {ISSUE_FIELDS_BRIEF}
                            }}
                        }}
                    }}
                """
                data = await linear_config.graphql_request(gql_query, {
                    "query": query,
                    "first": first,
                })
                issues_data = data.get("searchIssues", {}).get("nodes", [])
            else:
                # Use filtered issues query
                gql_query = f"""
                    query Issues($filter: IssueFilter, $first: Int) {{
                        issues(filter: $filter, first: $first, orderBy: updatedAt) {{
                            nodes {{
                                {ISSUE_FIELDS_BRIEF}
                            }}
                            pageInfo {{ hasNextPage endCursor }}
                        }}
                    }}
                """
                variables: Dict[str, Any] = {"first": first}
                if filters:
                    variables["filter"] = filters

                data = await linear_config.graphql_request(gql_query, variables)
                issues_data = data.get("issues", {}).get("nodes", [])

            issues = []
            for issue in issues_data:
                formatted = {
                    "identifier": issue.get("identifier", ""),
                    "title": issue.get("title", ""),
                    "url": issue.get("url", ""),
                    "priority": issue.get("priorityLabel", ""),
                    "state": issue.get("state", {}).get("name", "") if issue.get("state") else "",
                    "assignee": issue.get("assignee", {}).get("name", "Unassigned") if issue.get("assignee") else "Unassigned",
                    "team": issue.get("team", {}).get("key", "") if issue.get("team") else "",
                    "updatedAt": issue.get("updatedAt", ""),
                }
                labels = issue.get("labels", {})
                if labels and labels.get("nodes"):
                    formatted["labels"] = [l.get("name", "") for l in labels["nodes"]]
                issues.append(formatted)

            return json.dumps({
                "total": len(issues),
                "issues": issues
            }, indent=2)

        except Exception as e:
            return f"Error searching Linear issues: {str(e)}"

    # =========================================================================
    # GET ISSUE
    # =========================================================================

    @mcp.tool(
        name="linear_get_issue",
        annotations={
            "title": "Get Linear Issue Details",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_get_issue(
        issue_id: str,
        include_comments: bool = False
    ) -> str:
        """Get detailed information about a specific Linear issue.

        Args:
            issue_id: The issue ID (UUID) or identifier (e.g., "ENG-123").
            include_comments: Whether to include the issue's comments (default False).

        Returns full issue details including description, state, assignee, labels, etc.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            comments_fragment = ""
            if include_comments:
                comments_fragment = """
                    comments {
                        nodes {
                            id
                            body
                            createdAt
                            updatedAt
                            user { id name }
                        }
                    }
                """

            gql_query = f"""
                query Issue($id: String!) {{
                    issue(id: $id) {{
                        {ISSUE_FIELDS}
                        {comments_fragment}
                    }}
                }}
            """

            data = await linear_config.graphql_request(gql_query, {"id": issue_id})

            issue = data.get("issue")
            if not issue:
                return f"Error: Issue '{issue_id}' not found."

            result = _format_issue(issue)

            if include_comments:
                comments = []
                for c in issue.get("comments", {}).get("nodes", []):
                    user = c.get("user", {})
                    comments.append({
                        "id": c.get("id", ""),
                        "body": c.get("body", ""),
                        "author": user.get("name", "") if user else "",
                        "createdAt": c.get("createdAt", ""),
                        "updatedAt": c.get("updatedAt", ""),
                    })
                result["comments"] = comments

            return json.dumps(result, indent=2)

        except Exception as e:
            return f"Error getting Linear issue: {str(e)}"

    # =========================================================================
    # CREATE ISSUE
    # =========================================================================

    @mcp.tool(
        name="linear_create_issue",
        annotations={
            "title": "Create Linear Issue",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def linear_create_issue(
        title: str,
        team_id: str,
        description: Optional[str] = None,
        assignee_id: Optional[str] = None,
        state_id: Optional[str] = None,
        priority: Optional[int] = None,
        label_ids: Optional[str] = None,
        project_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        due_date: Optional[str] = None,
        estimate: Optional[int] = None,
        parent_id: Optional[str] = None
    ) -> str:
        """Create a new Linear issue.

        Args:
            title: Issue title (required).
            team_id: Team ID to create the issue in (required). Use linear_list_teams to find team IDs.
            description: Issue description in markdown format.
            assignee_id: User ID to assign the issue to.
            state_id: Workflow state ID. Use linear_list_workflow_states to find valid IDs.
            priority: Priority level (0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low).
            label_ids: Comma-separated label IDs to apply.
            project_id: Project ID to associate with.
            cycle_id: Cycle ID to add the issue to.
            due_date: Due date in YYYY-MM-DD format.
            estimate: Story point estimate.
            parent_id: Parent issue ID to create as a sub-issue.

        Returns the created issue details including its identifier and URL.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            input_data: Dict[str, Any] = {
                "title": title,
                "teamId": team_id,
            }

            if description:
                input_data["description"] = description
            if assignee_id:
                input_data["assigneeId"] = assignee_id
            if state_id:
                input_data["stateId"] = state_id
            if priority is not None:
                input_data["priority"] = priority
            if label_ids:
                input_data["labelIds"] = [lid.strip() for lid in label_ids.split(",")]
            if project_id:
                input_data["projectId"] = project_id
            if cycle_id:
                input_data["cycleId"] = cycle_id
            if due_date:
                input_data["dueDate"] = due_date
            if estimate is not None:
                input_data["estimate"] = estimate
            if parent_id:
                input_data["parentId"] = parent_id

            gql_query = f"""
                mutation IssueCreate($input: IssueCreateInput!) {{
                    issueCreate(input: $input) {{
                        success
                        issue {{
                            {ISSUE_FIELDS}
                        }}
                    }}
                }}
            """

            data = await linear_config.graphql_request(gql_query, {"input": input_data})

            result = data.get("issueCreate", {})
            if not result.get("success"):
                return "Error: Failed to create issue."

            issue = result.get("issue", {})
            formatted = _format_issue(issue)
            formatted["_status"] = "created"

            return json.dumps(formatted, indent=2)

        except Exception as e:
            return f"Error creating Linear issue: {str(e)}"

    # =========================================================================
    # UPDATE ISSUE
    # =========================================================================

    @mcp.tool(
        name="linear_update_issue",
        annotations={
            "title": "Update Linear Issue",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_update_issue(
        issue_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        assignee_id: Optional[str] = None,
        state_id: Optional[str] = None,
        priority: Optional[int] = None,
        label_ids: Optional[str] = None,
        project_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        due_date: Optional[str] = None,
        estimate: Optional[int] = None
    ) -> str:
        """Update an existing Linear issue.

        Args:
            issue_id: The issue ID (UUID) or identifier (e.g., "ENG-123") to update.
            title: New issue title.
            description: New description in markdown format.
            assignee_id: New assignee user ID.
            state_id: New workflow state ID. Use linear_list_workflow_states to find valid IDs.
            priority: New priority (0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low).
            label_ids: Comma-separated label IDs (replaces existing labels).
            project_id: Project ID to associate with.
            cycle_id: Cycle ID to move to.
            due_date: Due date in YYYY-MM-DD format.
            estimate: Story point estimate.

        Returns the updated issue details.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            input_data: Dict[str, Any] = {}

            if title is not None:
                input_data["title"] = title
            if description is not None:
                input_data["description"] = description
            if assignee_id is not None:
                input_data["assigneeId"] = assignee_id
            if state_id is not None:
                input_data["stateId"] = state_id
            if priority is not None:
                input_data["priority"] = priority
            if label_ids is not None:
                input_data["labelIds"] = [lid.strip() for lid in label_ids.split(",")]
            if project_id is not None:
                input_data["projectId"] = project_id
            if cycle_id is not None:
                input_data["cycleId"] = cycle_id
            if due_date is not None:
                input_data["dueDate"] = due_date
            if estimate is not None:
                input_data["estimate"] = estimate

            if not input_data:
                return "Error: No fields provided to update."

            gql_query = f"""
                mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {{
                    issueUpdate(id: $id, input: $input) {{
                        success
                        issue {{
                            {ISSUE_FIELDS}
                        }}
                    }}
                }}
            """

            data = await linear_config.graphql_request(gql_query, {
                "id": issue_id,
                "input": input_data,
            })

            result = data.get("issueUpdate", {})
            if not result.get("success"):
                return f"Error: Failed to update issue '{issue_id}'."

            issue = result.get("issue", {})
            formatted = _format_issue(issue)
            formatted["_status"] = "updated"

            return json.dumps(formatted, indent=2)

        except Exception as e:
            return f"Error updating Linear issue: {str(e)}"

    # =========================================================================
    # ADD COMMENT
    # =========================================================================

    @mcp.tool(
        name="linear_add_comment",
        annotations={
            "title": "Add Comment to Linear Issue",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True
        }
    )
    async def linear_add_comment(
        issue_id: str,
        body: str
    ) -> str:
        """Add a comment to a Linear issue.

        Args:
            issue_id: The issue ID (UUID) or identifier (e.g., "ENG-123") to comment on.
            body: Comment body in markdown format.

        Returns the created comment details.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            gql_query = """
                mutation CommentCreate($input: CommentCreateInput!) {
                    commentCreate(input: $input) {
                        success
                        comment {
                            id
                            body
                            createdAt
                            user { id name }
                            issue { id identifier title }
                        }
                    }
                }
            """

            data = await linear_config.graphql_request(gql_query, {
                "input": {
                    "issueId": issue_id,
                    "body": body,
                }
            })

            result = data.get("commentCreate", {})
            if not result.get("success"):
                return f"Error: Failed to add comment to issue '{issue_id}'."

            comment = result.get("comment", {})
            user = comment.get("user", {})
            issue = comment.get("issue", {})

            return json.dumps({
                "_status": "comment_added",
                "commentId": comment.get("id", ""),
                "body": comment.get("body", ""),
                "author": user.get("name", "") if user else "",
                "createdAt": comment.get("createdAt", ""),
                "issue": {
                    "identifier": issue.get("identifier", ""),
                    "title": issue.get("title", ""),
                }
            }, indent=2)

        except Exception as e:
            return f"Error adding comment to Linear issue: {str(e)}"

    # =========================================================================
    # PROJECTS
    # =========================================================================

    @mcp.tool(
        name="linear_list_projects",
        annotations={
            "title": "List Linear Projects",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_list_projects(
        first: int = 25,
        include_completed: bool = False
    ) -> str:
        """List projects in the Linear workspace.

        Args:
            first: Number of projects to return (default 25, max 50).
            include_completed: Whether to include completed/cancelled projects (default False).

        Returns a list of projects with their names, status, progress, and leads.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            first = min(first, 50)

            if include_completed:
                filter_clause = ""
            else:
                filter_clause = ', filter: { state: { nin: ["completed", "cancelled"] } }'

            gql_query = f"""
                query Projects($first: Int) {{
                    projects(first: $first{filter_clause}, orderBy: updatedAt) {{
                        nodes {{
                            id
                            name
                            description
                            url
                            state
                            progress
                            startDate
                            targetDate
                            createdAt
                            updatedAt
                            lead {{ id name }}
                            teams {{ nodes {{ id name key }} }}
                        }}
                        pageInfo {{ hasNextPage endCursor }}
                    }}
                }}
            """

            data = await linear_config.graphql_request(gql_query, {"first": first})

            projects = []
            for p in data.get("projects", {}).get("nodes", []):
                formatted = _format_project(p)
                teams = p.get("teams", {}).get("nodes", [])
                if teams:
                    formatted["teams"] = [t.get("name", "") for t in teams]
                projects.append(formatted)

            return json.dumps({
                "total": len(projects),
                "projects": projects
            }, indent=2)

        except Exception as e:
            return f"Error listing Linear projects: {str(e)}"

    # =========================================================================
    # CYCLES
    # =========================================================================

    @mcp.tool(
        name="linear_list_cycles",
        annotations={
            "title": "List Linear Cycles",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_list_cycles(
        team_id: Optional[str] = None,
        include_completed: bool = False,
        first: int = 10
    ) -> str:
        """List cycles (sprints) in the Linear workspace.

        Args:
            team_id: Optional team ID to filter cycles for a specific team.
            include_completed: Whether to include completed cycles (default False).
            first: Number of cycles to return (default 10, max 50).

        Returns a list of cycles with their names, dates, and progress.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            first = min(first, 50)
            filters = {}

            if team_id:
                filters["team"] = {"id": {"eq": team_id}}
            if not include_completed:
                filters["isActive"] = {"eq": True}

            gql_query = """
                query Cycles($first: Int, $filter: CycleFilter) {
                    cycles(first: $first, filter: $filter, orderBy: createdAt) {
                        nodes {
                            id
                            name
                            number
                            startsAt
                            endsAt
                            completedAt
                            progress
                            scopeCount: issueCountHistory
                            team { id name key }
                        }
                    }
                }
            """

            variables: Dict[str, Any] = {"first": first}
            if filters:
                variables["filter"] = filters

            data = await linear_config.graphql_request(gql_query, variables)

            cycles = []
            for c in data.get("cycles", {}).get("nodes", []):
                team = c.get("team", {})
                cycle = {
                    "id": c.get("id", ""),
                    "name": c.get("name", "") or f"Cycle {c.get('number', '')}",
                    "number": c.get("number", ""),
                    "startsAt": c.get("startsAt", ""),
                    "endsAt": c.get("endsAt", ""),
                    "progress": c.get("progress", 0),
                    "team": team.get("name", "") if team else "",
                }
                if c.get("completedAt"):
                    cycle["completedAt"] = c["completedAt"]
                cycles.append(cycle)

            return json.dumps({"total": len(cycles), "cycles": cycles}, indent=2)

        except Exception as e:
            return f"Error listing Linear cycles: {str(e)}"

    # =========================================================================
    # LABELS
    # =========================================================================

    @mcp.tool(
        name="linear_list_labels",
        annotations={
            "title": "List Linear Labels",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_list_labels(
        team_id: Optional[str] = None
    ) -> str:
        """List issue labels in the Linear workspace.

        Args:
            team_id: Optional team ID to filter labels for a specific team.

        Returns all available labels with IDs, names, colors, and parent groups.
        Label IDs are needed when creating or updating issues with labels.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            if team_id:
                query = """
                    query Labels($teamId: String!) {
                        issueLabels(filter: { team: { id: { eq: $teamId } } }) {
                            nodes {
                                id name color description
                                parent { id name }
                                team { id name key }
                            }
                        }
                    }
                """
                variables = {"teamId": team_id}
            else:
                query = """
                    query Labels {
                        issueLabels {
                            nodes {
                                id name color description
                                parent { id name }
                                team { id name key }
                            }
                        }
                    }
                """
                variables = None

            data = await linear_config.graphql_request(query, variables)

            labels = []
            for l in data.get("issueLabels", {}).get("nodes", []):
                label = {
                    "id": l.get("id", ""),
                    "name": l.get("name", ""),
                    "color": l.get("color", ""),
                }
                if l.get("description"):
                    label["description"] = l["description"]
                parent = l.get("parent")
                if parent:
                    label["parentGroup"] = parent.get("name", "")
                team = l.get("team")
                if team:
                    label["team"] = team.get("name", "")
                labels.append(label)

            return json.dumps({"total": len(labels), "labels": labels}, indent=2)

        except Exception as e:
            return f"Error listing Linear labels: {str(e)}"

    # =========================================================================
    # LIST USERS
    # =========================================================================

    @mcp.tool(
        name="linear_list_users",
        annotations={
            "title": "List Linear Users",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_list_users(
        include_disabled: bool = False
    ) -> str:
        """List users in the Linear workspace.

        Args:
            include_disabled: Whether to include disabled/deactivated users (default False).

        Returns user IDs, names, emails, and roles.
        User IDs are needed when assigning issues.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            if include_disabled:
                filter_clause = ""
            else:
                filter_clause = "(filter: { active: { eq: true } })"

            gql_query = f"""
                query Users {{
                    users{filter_clause} {{
                        nodes {{
                            id
                            name
                            email
                            displayName
                            active
                            admin
                            guest
                        }}
                    }}
                }}
            """

            data = await linear_config.graphql_request(gql_query)

            users = []
            for u in data.get("users", {}).get("nodes", []):
                user = {
                    "id": u.get("id", ""),
                    "name": u.get("name", ""),
                    "email": u.get("email", ""),
                    "displayName": u.get("displayName", ""),
                    "active": u.get("active", False),
                }
                if u.get("admin"):
                    user["admin"] = True
                if u.get("guest"):
                    user["guest"] = True
                users.append(user)

            return json.dumps({"total": len(users), "users": users}, indent=2)

        except Exception as e:
            return f"Error listing Linear users: {str(e)}"

    # =========================================================================
    # ARCHIVE ISSUE
    # =========================================================================

    @mcp.tool(
        name="linear_archive_issue",
        annotations={
            "title": "Archive Linear Issue",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True
        }
    )
    async def linear_archive_issue(
        issue_id: str
    ) -> str:
        """Archive a Linear issue.

        Args:
            issue_id: The issue ID (UUID) or identifier (e.g., "ENG-123") to archive.

        Returns confirmation of the archive operation.
        """
        if not linear_config.is_configured:
            return "Error: Linear not configured. Set LINEAR_API_KEY."

        try:
            gql_query = """
                mutation IssueArchive($id: String!) {
                    issueArchive(id: $id) {
                        success
                    }
                }
            """

            data = await linear_config.graphql_request(gql_query, {"id": issue_id})

            result = data.get("issueArchive", {})
            if result.get("success"):
                return json.dumps({
                    "_status": "archived",
                    "issueId": issue_id,
                    "message": f"Issue '{issue_id}' has been archived."
                }, indent=2)
            else:
                return f"Error: Failed to archive issue '{issue_id}'."

        except Exception as e:
            return f"Error archiving Linear issue: {str(e)}"
