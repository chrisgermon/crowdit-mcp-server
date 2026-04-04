"""
GCP Cloud Build tools for Crowd IT MCP Server.
Uses google-cloud-build library with ADC (automatic on Cloud Run).
Default project: crowdmcp.
"""

import os
import logging
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "crowdmcp")

STATUS_EMOJI = {
    "SUCCESS": "✅",
    "FAILURE": "❌",
    "WORKING": "🔄",
    "QUEUED": "⏳",
    "CANCELLED": "🚫",
    "TIMEOUT": "⏰",
    "INTERNAL_ERROR": "💥",
    "STATUS_UNKNOWN": "❓",
}


def _format_duration(start, finish):
    """Calculate and format duration from protobuf timestamps."""
    if not start or not finish:
        return "N/A"
    try:
        s = start if isinstance(start, datetime) else start.ToDatetime()
        f = finish if isinstance(finish, datetime) else finish.ToDatetime()
        delta = f - s
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return "N/A"
        minutes, seconds = divmod(total_seconds, 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
    except Exception:
        return "N/A"


def _short_sha(sha):
    """Return first 7 chars of a commit SHA."""
    if sha:
        return sha[:7]
    return "N/A"


def _format_timestamp(ts):
    """Format a protobuf timestamp or datetime."""
    if not ts:
        return "N/A"
    try:
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        return ts.ToDatetime().strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


def register_cloudbuild_tools(mcp):

    @mcp.tool()
    async def cloudbuild_list_builds(
        project: Optional[str] = None,
        limit: int = 10,
        status_filter: Optional[str] = None,
    ) -> str:
        """List recent Cloud Build builds with status, trigger name, duration, commit info.
        Args:
            project: GCP project ID (default: crowdmcp)
            limit: Number of builds to return (default 10, max 50)
            status_filter: Filter by status: SUCCESS, FAILURE, WORKING, QUEUED, CANCELLED
        """
        from google.cloud.devtools import cloudbuild_v1

        project = project or GCP_PROJECT_ID
        limit = min(max(limit, 1), 50)

        try:
            client = cloudbuild_v1.CloudBuildClient()

            filter_str = ""
            if status_filter:
                sf = status_filter.upper()
                valid = {"SUCCESS", "FAILURE", "WORKING", "QUEUED", "CANCELLED", "TIMEOUT"}
                if sf not in valid:
                    return f"❌ Invalid status filter `{status_filter}`. Valid: {', '.join(sorted(valid))}"
                filter_str = f'status="{sf}"'

            request = cloudbuild_v1.ListBuildsRequest(
                project_id=project,
                page_size=limit,
                filter=filter_str,
            )

            builds = list(client.list_builds(request=request))[:limit]

            if not builds:
                msg = f"No builds found in project `{project}`"
                if status_filter:
                    msg += f" with status `{status_filter.upper()}`"
                return msg

            lines = [f"## Cloud Build — `{project}` ({len(builds)} builds)\n"]

            for b in builds:
                status_name = cloudbuild_v1.Build.Status(b.status).name
                emoji = STATUS_EMOJI.get(status_name, "❓")
                trigger_name = b.build_trigger_id or "manual"
                if b.substitutions and "_TRIGGER_NAME" in b.substitutions:
                    trigger_name = b.substitutions["_TRIGGER_NAME"]

                branch = "N/A"
                commit = "N/A"
                repo = ""
                if b.source and b.source.repo_source:
                    branch = b.source.repo_source.branch_name or b.source.repo_source.tag_name or "N/A"
                    commit = _short_sha(b.source.repo_source.commit_sha)
                    repo = b.source.repo_source.repo_name or ""
                if b.substitutions:
                    if "BRANCH_NAME" in b.substitutions:
                        branch = b.substitutions["BRANCH_NAME"]
                    if "SHORT_SHA" in b.substitutions:
                        commit = b.substitutions["SHORT_SHA"]
                    elif "COMMIT_SHA" in b.substitutions:
                        commit = _short_sha(b.substitutions["COMMIT_SHA"])
                    if "REPO_NAME" in b.substitutions:
                        repo = b.substitutions["REPO_NAME"]

                duration = _format_duration(b.start_time, b.finish_time)
                created = _format_timestamp(b.create_time)

                lines.append(
                    f"### {emoji} {status_name} — `{trigger_name}`\n"
                    f"- **Build ID:** `{b.id[:8]}...`\n"
                    f"- **Branch:** `{branch}` | **Commit:** `{commit}`"
                    + (f" | **Repo:** `{repo}`" if repo else "")
                    + f"\n- **Duration:** {duration} | **Created:** {created}\n"
                )

            return "\n".join(lines)

        except Exception as e:
            return f"❌ Error listing builds: {e}"

    @mcp.tool()
    async def cloudbuild_get_build(
        build_id: str,
        project: Optional[str] = None,
    ) -> str:
        """Get full build details including logs URL, steps, timing.
        Args:
            build_id: The Cloud Build build ID
            project: GCP project ID (default: crowdmcp)
        """
        from google.cloud.devtools import cloudbuild_v1

        project = project or GCP_PROJECT_ID

        try:
            client = cloudbuild_v1.CloudBuildClient()
            request = cloudbuild_v1.GetBuildRequest(
                project_id=project,
                id=build_id,
            )
            b = client.get_build(request=request)

            status_name = cloudbuild_v1.Build.Status(b.status).name
            emoji = STATUS_EMOJI.get(status_name, "❓")

            trigger_name = b.build_trigger_id or "manual"
            if b.substitutions and "_TRIGGER_NAME" in b.substitutions:
                trigger_name = b.substitutions["_TRIGGER_NAME"]

            branch = "N/A"
            commit = "N/A"
            repo = ""
            if b.substitutions:
                branch = b.substitutions.get("BRANCH_NAME", "N/A")
                commit = b.substitutions.get("SHORT_SHA") or _short_sha(b.substitutions.get("COMMIT_SHA"))
                repo = b.substitutions.get("REPO_NAME", "")
            elif b.source and b.source.repo_source:
                branch = b.source.repo_source.branch_name or "N/A"
                commit = _short_sha(b.source.repo_source.commit_sha)
                repo = b.source.repo_source.repo_name or ""

            duration = _format_duration(b.start_time, b.finish_time)

            lines = [
                f"## {emoji} Build `{b.id}`\n",
                f"- **Status:** {status_name}",
                f"- **Trigger:** `{trigger_name}`",
                f"- **Branch:** `{branch}` | **Commit:** `{commit}`"
                + (f" | **Repo:** `{repo}`" if repo else ""),
                f"- **Duration:** {duration}",
                f"- **Created:** {_format_timestamp(b.create_time)}",
                f"- **Started:** {_format_timestamp(b.start_time)}",
                f"- **Finished:** {_format_timestamp(b.finish_time)}",
            ]

            if b.logs_url:
                lines.append(f"- **Logs:** {b.logs_url}")
            if b.log_url:
                lines.append(f"- **Log bucket:** `{b.log_url}`")

            if b.images:
                lines.append(f"- **Images:** {', '.join(f'`{img}`' for img in b.images)}")

            if b.tags:
                lines.append(f"- **Tags:** {', '.join(f'`{t}`' for t in b.tags)}")

            # Build steps
            if b.steps:
                lines.append(f"\n### Steps ({len(b.steps)})\n")
                lines.append("| # | Name | Status | Duration |")
                lines.append("|---|------|--------|----------|")
                for i, step in enumerate(b.steps, 1):
                    step_status = cloudbuild_v1.Build.Status(step.status).name if step.status else "PENDING"
                    step_emoji = STATUS_EMOJI.get(step_status, "❓")
                    step_duration = _format_duration(step.timing.start_time, step.timing.end_time) if step.timing else "N/A"
                    step_name = step.name or "unnamed"
                    # Truncate long image names
                    if len(step_name) > 50:
                        step_name = "..." + step_name[-47:]
                    lines.append(f"| {i} | `{step_name}` | {step_emoji} {step_status} | {step_duration} |")

            # Substitutions
            if b.substitutions:
                lines.append("\n### Substitutions\n")
                for k, v in sorted(b.substitutions.items()):
                    lines.append(f"- `{k}`: `{v}`")

            return "\n".join(lines)

        except Exception as e:
            return f"❌ Error getting build `{build_id}`: {e}"

    @mcp.tool()
    async def cloudbuild_list_triggers(
        project: Optional[str] = None,
    ) -> str:
        """List all Cloud Build triggers with name, repo, branch filter, description.
        Args:
            project: GCP project ID (default: crowdmcp)
        """
        from google.cloud.devtools import cloudbuild_v1

        project = project or GCP_PROJECT_ID

        try:
            client = cloudbuild_v1.CloudBuildClient()
            request = cloudbuild_v1.ListBuildTriggersRequest(
                project_id=project,
            )

            triggers = list(client.list_build_triggers(request=request))

            if not triggers:
                return f"No build triggers found in project `{project}`."

            lines = [f"## Cloud Build Triggers — `{project}` ({len(triggers)} triggers)\n"]

            for t in triggers:
                disabled_tag = " *(disabled)*" if t.disabled else ""
                lines.append(f"### `{t.name or t.id}`{disabled_tag}\n")

                if t.description:
                    lines.append(f"- **Description:** {t.description}")

                lines.append(f"- **ID:** `{t.id}`")

                # Repo info
                if t.github:
                    owner = t.github.owner or ""
                    repo = t.github.name or ""
                    lines.append(f"- **GitHub:** `{owner}/{repo}`")
                    if t.github.push:
                        branch = t.github.push.branch or ""
                        tag = t.github.push.tag or ""
                        if branch:
                            lines.append(f"- **Branch filter:** `{branch}`")
                        if tag:
                            lines.append(f"- **Tag filter:** `{tag}`")
                    if t.github.pull_request:
                        lines.append(f"- **PR branch:** `{t.github.pull_request.branch or '*'}`")

                if t.trigger_template:
                    tt = t.trigger_template
                    if tt.repo_name:
                        lines.append(f"- **Repo:** `{tt.repo_name}`")
                    if tt.branch_name:
                        lines.append(f"- **Branch:** `{tt.branch_name}`")
                    if tt.tag_name:
                        lines.append(f"- **Tag:** `{tt.tag_name}`")

                if t.filename:
                    lines.append(f"- **Config:** `{t.filename}`")
                elif t.build:
                    lines.append(f"- **Config:** inline build config ({len(t.build.steps)} steps)")

                if t.tags:
                    lines.append(f"- **Tags:** {', '.join(f'`{tag}`' for tag in t.tags)}")

                if t.substitutions:
                    subs = ", ".join(f"`{k}={v}`" for k, v in sorted(t.substitutions.items()))
                    lines.append(f"- **Substitutions:** {subs}")

                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            return f"❌ Error listing triggers: {e}"

    @mcp.tool()
    async def cloudbuild_get_build_log(
        build_id: str,
        project: Optional[str] = None,
        tail: int = 50,
    ) -> str:
        """Get build log text (last N lines).
        Args:
            build_id: The Cloud Build build ID
            project: GCP project ID (default: crowdmcp)
            tail: Number of lines from the end to return (default 50)
        """
        from google.cloud.devtools import cloudbuild_v1
        from google.cloud import storage

        project = project or GCP_PROJECT_ID
        tail = min(max(tail, 10), 500)

        try:
            # First get the build to find the log bucket
            cb_client = cloudbuild_v1.CloudBuildClient()
            request = cloudbuild_v1.GetBuildRequest(
                project_id=project,
                id=build_id,
            )
            b = cb_client.get_build(request=request)

            status_name = cloudbuild_v1.Build.Status(b.status).name

            # log_url is like gs://[BUCKET]/log-[BUILD_ID].txt
            # or the logs_bucket field
            log_bucket = None
            log_object = None

            if b.log_url:
                # Parse gs://bucket/path format
                url = b.log_url
                if url.startswith("gs://"):
                    url = url[5:]
                parts = url.split("/", 1)
                log_bucket = parts[0]
                log_object = f"log-{build_id}.txt"
            elif b.logs_bucket:
                bucket_url = b.logs_bucket
                if bucket_url.startswith("gs://"):
                    bucket_url = bucket_url[5:]
                log_bucket = bucket_url.rstrip("/")
                log_object = f"log-{build_id}.txt"

            if not log_bucket:
                # Fallback: default GCS bucket
                log_bucket = f"{project}_cloudbuild"
                log_object = f"log-{build_id}.txt"

            # Read log from GCS
            storage_client = storage.Client()
            bucket = storage_client.bucket(log_bucket)
            blob = bucket.blob(log_object)

            if not blob.exists():
                return (
                    f"## Build Log — `{build_id[:8]}...` ({status_name})\n\n"
                    f"Log file not found at `gs://{log_bucket}/{log_object}`.\n"
                    f"Build may still be in progress or logs may have been cleaned up.\n"
                    + (f"\n**Logs URL:** {b.logs_url}" if b.logs_url else "")
                )

            content = blob.download_as_text()
            all_lines = content.splitlines()
            total = len(all_lines)

            if total > tail:
                shown_lines = all_lines[-tail:]
                header_note = f"(showing last {tail} of {total} lines)"
            else:
                shown_lines = all_lines
                header_note = f"({total} lines)"

            log_text = "\n".join(shown_lines)

            return (
                f"## Build Log — `{build_id[:8]}...` ({status_name}) {header_note}\n\n"
                f"```\n{log_text}\n```"
            )

        except Exception as e:
            return f"❌ Error fetching build log for `{build_id}`: {e}"

    logger.info("Cloud Build tools registered (4 tools)")
