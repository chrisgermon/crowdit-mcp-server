"""
BigQuery tools for Crowd IT MCP Server.
Uses google-cloud-bigquery library with ADC (automatic on Cloud Run).
Default data project: vision-radiology, default dataset: karisma_live.
Job project defaults to crowdmcp (configurable via env vars).
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Job project — where queries execute and billing is charged
BIGQUERY_JOB_PROJECT = os.environ.get(
    "BIGQUERY_JOB_PROJECT_ID",
    os.environ.get("BIGQUERY_PROJECT_ID", "crowdmcp"),
)

# Data project — where tables live
BIGQUERY_DATA_PROJECT = os.environ.get("BIGQUERY_DATA_PROJECT_ID", "vision-radiology")

# Default dataset
BIGQUERY_DEFAULT_DATASET = os.environ.get("BIGQUERY_DEFAULT_DATASET", "karisma_live")

# Safety limits
BIGQUERY_MAX_BYTES_BILLED = int(
    os.environ.get("BIGQUERY_MAX_BYTES_BILLED", str(1 * 1024 * 1024 * 1024))  # 1 GB
)
BIGQUERY_TIMEOUT_SECONDS = int(os.environ.get("BIGQUERY_TIMEOUT_SECONDS", "120"))


def _get_client():
    """Create a BigQuery client scoped to the job project."""
    from google.cloud import bigquery

    return bigquery.Client(project=BIGQUERY_JOB_PROJECT)


def _format_bytes(num_bytes: int) -> str:
    """Human-readable byte size."""
    if num_bytes is None:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def register_bigquery_tools(mcp):

    @mcp.tool()
    async def bigquery_list_datasets(project: Optional[str] = None) -> str:
        """List datasets in a BigQuery project.

        Args:
            project: GCP project ID containing the datasets (default: vision-radiology)
        """
        project = project or BIGQUERY_DATA_PROJECT
        try:
            client = _get_client()
            datasets = list(client.list_datasets(project=project))

            if not datasets:
                return f"No datasets found in project `{project}`."

            lines = [f"## Datasets in `{project}`\n"]
            for ds in datasets:
                lines.append(f"- **{ds.dataset_id}** — `{ds.full_dataset_id}`")

            lines.append(f"\n_{len(datasets)} dataset(s) found._")
            return "\n".join(lines)
        except Exception as e:
            return f"**Error listing datasets:** {e}"

    @mcp.tool()
    async def bigquery_list_tables(
        dataset: Optional[str] = None, project: Optional[str] = None
    ) -> str:
        """List tables in a BigQuery dataset.

        Args:
            dataset: Dataset name (default: karisma_live)
            project: GCP project containing the dataset (default: vision-radiology)
        """
        dataset = dataset or BIGQUERY_DEFAULT_DATASET
        project = project or BIGQUERY_DATA_PROJECT
        try:
            client = _get_client()
            dataset_ref = f"{project}.{dataset}"
            tables = list(client.list_tables(dataset_ref))

            if not tables:
                return f"No tables found in `{dataset_ref}`."

            lines = [f"## Tables in `{dataset_ref}`\n"]
            lines.append("| Table | Type |")
            lines.append("|-------|------|")
            for t in sorted(tables, key=lambda x: x.table_id):
                ttype = t.table_type or "TABLE"
                lines.append(f"| `{t.table_id}` | {ttype} |")

            lines.append(f"\n_{len(tables)} table(s) found._")
            return "\n".join(lines)
        except Exception as e:
            return f"**Error listing tables:** {e}"

    @mcp.tool()
    async def bigquery_describe_table(
        table: str,
        dataset: Optional[str] = None,
        project: Optional[str] = None,
    ) -> str:
        """Get table schema, row count, size, and metadata for a BigQuery table.

        Args:
            table: Table name
            dataset: Dataset name (default: karisma_live)
            project: GCP project containing the table (default: vision-radiology)
        """
        dataset = dataset or BIGQUERY_DEFAULT_DATASET
        project = project or BIGQUERY_DATA_PROJECT
        try:
            client = _get_client()
            table_ref = f"{project}.{dataset}.{table}"
            tbl = client.get_table(table_ref)

            lines = [f"## `{table_ref}`\n"]
            lines.append(f"- **Type:** {tbl.table_type}")
            lines.append(f"- **Rows:** {tbl.num_rows:,}" if tbl.num_rows is not None else "- **Rows:** unknown")
            lines.append(f"- **Size:** {_format_bytes(tbl.num_bytes)}")
            lines.append(f"- **Created:** {tbl.created}")
            lines.append(f"- **Modified:** {tbl.modified}")
            if tbl.description:
                lines.append(f"- **Description:** {tbl.description}")
            if tbl.time_partitioning:
                tp = tbl.time_partitioning
                lines.append(f"- **Partitioned by:** {tp.field or 'ingestion time'} ({tp.type_})")
            if tbl.clustering_fields:
                lines.append(f"- **Clustered by:** {', '.join(tbl.clustering_fields)}")

            lines.append(f"\n### Schema ({len(tbl.schema)} columns)\n")
            lines.append("| Column | Type | Mode | Description |")
            lines.append("|--------|------|------|-------------|")
            for field in tbl.schema:
                desc = (field.description or "")[:60]
                lines.append(f"| `{field.name}` | {field.field_type} | {field.mode} | {desc} |")

            return "\n".join(lines)
        except Exception as e:
            return f"**Error describing table:** {e}"

    @mcp.tool()
    async def bigquery_query(
        sql: str,
        max_rows: int = 100,
        max_bytes_billed: Optional[int] = None,
        project: Optional[str] = None,
    ) -> str:
        """Run a SQL query against BigQuery and return results as markdown.

        Queries execute in the job project and are billed there.
        A maximum bytes billed limit is enforced for safety (default 1 GB).

        Args:
            sql: SQL query to execute. Reference tables as `project.dataset.table`.
            max_rows: Maximum rows to return (default 100, max 1000).
            max_bytes_billed: Maximum bytes billed safety limit (default 1 GB). Set to 0 to disable.
            project: Override job project for billing (default: crowdmcp).
        """
        from google.cloud import bigquery

        max_rows = min(max_rows, 1000)
        project = project or BIGQUERY_JOB_PROJECT
        try:
            client = _get_client()
            job_config = bigquery.QueryJobConfig()

            effective_limit = max_bytes_billed if max_bytes_billed is not None else BIGQUERY_MAX_BYTES_BILLED
            if effective_limit > 0:
                job_config.maximum_bytes_billed = effective_limit

            query_job = client.query(sql, job_config=job_config, project=project, timeout=BIGQUERY_TIMEOUT_SECONDS)
            results = query_job.result(timeout=BIGQUERY_TIMEOUT_SECONDS)

            rows = []
            for row in results:
                rows.append(dict(row))
                if len(rows) >= max_rows:
                    break

            total_rows = results.total_rows
            bytes_processed = query_job.total_bytes_processed or 0

            if not rows:
                return (
                    f"Query returned 0 rows.\n\n"
                    f"_Processed: {_format_bytes(bytes_processed)}_"
                )

            # Build markdown table
            columns = list(rows[0].keys())
            lines = [f"## Query Results\n"]

            # Header
            lines.append("| " + " | ".join(str(c) for c in columns) + " |")
            lines.append("| " + " | ".join("---" for _ in columns) + " |")

            # Rows
            for row in rows:
                vals = []
                for c in columns:
                    v = row[c]
                    if v is None:
                        vals.append("")
                    else:
                        s = str(v)
                        # Truncate long values in table cells
                        if len(s) > 100:
                            s = s[:97] + "..."
                        # Escape pipe characters
                        s = s.replace("|", "\\|")
                        vals.append(s)
                lines.append("| " + " | ".join(vals) + " |")

            # Footer
            truncated = " (truncated)" if len(rows) < total_rows else ""
            lines.append(
                f"\n_Showing {len(rows)} of {total_rows:,} rows{truncated}. "
                f"Processed: {_format_bytes(bytes_processed)}._"
            )

            return "\n".join(lines)
        except Exception as e:
            return f"**Error running query:** {e}"

    @mcp.tool()
    async def bigquery_preview_table(
        table: str,
        dataset: Optional[str] = None,
        project: Optional[str] = None,
        max_rows: int = 10,
    ) -> str:
        """Preview the first N rows of a BigQuery table without running a full query.

        Uses the BigQuery Storage Read API for efficient preview with zero query cost.

        Args:
            table: Table name
            dataset: Dataset name (default: karisma_live)
            project: GCP project containing the table (default: vision-radiology)
            max_rows: Number of rows to preview (default 10, max 100)
        """
        from google.cloud import bigquery

        dataset = dataset or BIGQUERY_DEFAULT_DATASET
        project = project or BIGQUERY_DATA_PROJECT
        max_rows = min(max_rows, 100)
        try:
            client = _get_client()
            table_ref = f"{project}.{dataset}.{table}"

            # Use list_rows which reads directly from storage (no query cost)
            rows_iter = client.list_rows(table_ref, max_results=max_rows)
            rows = [dict(row) for row in rows_iter]

            if not rows:
                return f"Table `{table_ref}` is empty."

            columns = list(rows[0].keys())
            lines = [f"## Preview: `{table_ref}` (first {len(rows)} rows)\n"]

            lines.append("| " + " | ".join(str(c) for c in columns) + " |")
            lines.append("| " + " | ".join("---" for _ in columns) + " |")

            for row in rows:
                vals = []
                for c in columns:
                    v = row[c]
                    if v is None:
                        vals.append("")
                    else:
                        s = str(v)
                        if len(s) > 80:
                            s = s[:77] + "..."
                        s = s.replace("|", "\\|")
                        vals.append(s)
                lines.append("| " + " | ".join(vals) + " |")

            return "\n".join(lines)
        except Exception as e:
            return f"**Error previewing table:** {e}"

    logger.info("BigQuery tools registered (5 tools)")
