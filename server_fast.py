"""
Crowd IT MCP Server - Fast Startup Entry Point

This entry point starts uvicorn immediately (< 2 seconds) and loads
the full MCP tool suite in the background. During loading, health
checks pass and MCP requests receive a friendly "loading" message.

The original server.py is imported as a module in a background thread,
and its tools are transferred to the live FastMCP instance once loaded.

Usage:
    python server_fast.py

Environment:
    PORT           - HTTP port (default 8080)
    ENABLED_SERVICES - Comma-separated list of services to load (optional)
"""

import sys
import os
import time
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

# Timing
_start = time.time()

def _t():
    return f"t={time.time() - _start:.2f}s"

print(f"[FAST] Starting fast entry point at {_t()}", file=sys.stderr, flush=True)

# Minimal imports only - these are fast
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import PlainTextResponse, HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

print(f"[FAST] Core imports done at {_t()}", file=sys.stderr, flush=True)

# ============================================================================
# Create empty FastMCP instance (tools loaded in background)
# ============================================================================

mcp = FastMCP(
    name="crowdit-mcp-server",
    instructions="Crowd IT Unified MCP Server. Tools are loading in the background - if you see 0 tools, wait a few seconds and reconnect.",
)

# Track loading state
_tools_loaded = threading.Event()
_tools_loading_error: str | None = None
_tool_count = 0

print(f"[FAST] FastMCP instance created at {_t()}", file=sys.stderr, flush=True)


# ============================================================================
# Background Tool Loader
# ============================================================================

def _load_tools_background():
    """Import server.py and transfer all tools to our FastMCP instance.

    This runs in a background thread so uvicorn can start immediately.
    We import the original server module which registers all tools on ITS
    mcp instance, then we copy them to ours.
    """
    global _tools_loading_error, _tool_count

    try:
        print(f"[FAST] Background tool loading starting at {_t()}", file=sys.stderr, flush=True)

        # Import the original server module - this triggers all @mcp.tool() decorators
        # and register_*_tools() calls on its own `mcp` instance
        import importlib

        # We need to prevent server.py's __main__ block from running
        # by importing it as a module (not executing it as __main__)
        spec = importlib.util.spec_from_file_location(
            "crowdit_server",
            os.path.join(os.path.dirname(__file__), "server.py")
        )
        server_module = importlib.util.module_from_spec(spec)

        # Override __name__ so the if __name__ == "__main__" block doesn't execute
        server_module.__name__ = "crowdit_server"

        print(f"[FAST] Loading server module at {_t()}", file=sys.stderr, flush=True)
        spec.loader.exec_module(server_module)
        print(f"[FAST] Server module loaded at {_t()}", file=sys.stderr, flush=True)

        # Get the source mcp instance from the imported module
        source_mcp = server_module.mcp
        source_tools = source_mcp._tool_manager._tools

        print(f"[FAST] Found {len(source_tools)} tools to transfer at {_t()}", file=sys.stderr, flush=True)

        # Transfer all tools to our live mcp instance
        for name, tool in source_tools.items():
            mcp._tool_manager._tools[name] = tool

        _tool_count = len(mcp._tool_manager._tools)
        print(f"[FAST] {_tool_count} tools registered at {_t()}", file=sys.stderr, flush=True)

        # Also transfer the instructions if the source has better ones
        if hasattr(source_mcp, '_instructions') and source_mcp._instructions:
            mcp._instructions = source_mcp._instructions

        # Initialize lazy configs in the loaded module so tools never see None.
        # When running server_fast we never run server.py's lifespan, so
        # _initialize_configs_once() would otherwise never run → 'NoneType' has no attribute 'is_configured'.
        try:
            server_module._initialize_configs_once()
            print(f"[FAST] Configs initialized at {_t()}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[FAST] Config init warning: {e}", file=sys.stderr, flush=True)

        # Store reference to server module for config access
        # (needed by status page, callbacks, etc.)
        _load_tools_background.server_module = server_module

        _tools_loaded.set()
        print(f"[FAST] ✅ All tools loaded successfully at {_t()}", file=sys.stderr, flush=True)

    except Exception as e:
        import traceback
        _tools_loading_error = str(e)
        print(f"[FAST] ❌ Tool loading FAILED at {_t()}: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        _tools_loaded.set()  # Unblock waiters even on failure


# ============================================================================
# Simple API Key Auth (loads from env or Secret Manager lazily)
# ============================================================================

class SimpleAPIKeyMiddleware(BaseHTTPMiddleware):
    """Lightweight API key middleware for the fast entry point."""

    PUBLIC_PATHS = {"/health", "/status", "/", "/debug/mcp", "/callback", "/sharepoint-callback"}

    def __init__(self, app):
        super().__init__(app)
        self._valid_keys: set | None = None
        self._on_cloud_run = bool(os.getenv("K_SERVICE"))

    def _load_keys(self):
        keys = set()
        # From environment
        env_key = os.getenv("MCP_API_KEY", "").strip()
        if env_key:
            keys.add(env_key)
        # From MCP_API_KEYS (comma-separated)
        multi = os.getenv("MCP_API_KEYS", "").strip()
        if multi:
            keys.update(k.strip() for k in multi.replace("\n", ",").split(",") if k.strip())

        # Try Secret Manager if on Cloud Run and no env keys
        if not keys and self._on_cloud_run:
            try:
                from app.core.config import get_secret_sync
                for secret_name in ("MCP_API_KEY", "MCP_API_KEYS"):
                    try:
                        val = get_secret_sync(secret_name)
                        if val:
                            keys.update(k.strip() for k in val.replace("\n", ",").split(",") if k.strip())
                    except Exception:
                        pass
            except Exception:
                pass

        self._valid_keys = keys
        if keys:
            print(f"[FAST] {len(keys)} API key(s) loaded", file=sys.stderr, flush=True)
        else:
            print(f"[FAST] No API keys configured (auth disabled)", file=sys.stderr, flush=True)

    async def dispatch(self, request, call_next):
        path = request.url.path

        if request.method == "OPTIONS":
            return await call_next(request)
        if path in self.PUBLIC_PATHS or path.startswith("/.well-known/"):
            return await call_next(request)

        if path == "/mcp":
            accept = (request.headers.get("Accept") or "").lower()
            required = ("application/json", "text/event-stream")
            if not all(r in accept for r in required):
                merged = accept
                for r in required:
                    if r not in merged:
                        merged = f"{merged}, {r}" if merged else r
                headers = []
                for k, v in request.scope.get("headers", []):
                    if k.lower() == b"accept":
                        continue
                    headers.append((k, v))
                headers.append((b"accept", merged.encode("utf-8")))
                request.scope["headers"] = headers

        # Lazy load keys on first protected request
        if self._valid_keys is None:
            await asyncio.to_thread(self._load_keys)

        if not self._valid_keys:
            # No keys configured - allow all (or rely on Cloud Run IAM)
            return await call_next(request)

        # Check for API key
        provided = (
            request.query_params.get("api_key")
            or request.headers.get("X-API-Key")
            or ""
        ).strip()
        if not provided:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                provided = auth[7:].strip()

        if provided and provided in self._valid_keys:
            return await call_next(request)

        if not provided and self._on_cloud_run:
            return await call_next(request)

        return PlainTextResponse("Unauthorized", status_code=401)


# ============================================================================
# Route handlers
# ============================================================================

def _get_integration_statuses() -> list[dict]:
    """Check each integration's config status."""
    statuses = []
    server_mod = getattr(_load_tools_background, "server_module", None)
    if not server_mod:
        return statuses

    integrations = [
        ("AWS", "_aws_config"),
        ("Azure", None),  # ADC on Cloud Run
        ("Email / Calendar", "_email_config"),
        ("Linear", "_linear_config"),
        ("Notion", "_notion_config"),
        ("DigitalOcean", "_do_config"),
        ("Proxmox", "_proxmox_config"),
        ("Xero", "_xero_config"),
        ("GCP Compute", None),  # ADC on Cloud Run
        ("Gorelo", "_gorelo_config"),
        ("Pax8", "_pax8_config"),
        ("NetBird", "_netbird_config"),
        ("CIPP", "_cipp_config"),
        ("Cloudflare", "_cloudflare_config"),
        ("Acronis", "_acronis_config"),
        ("Partner Center", "_partner_center_config"),
        ("Teams", "_teams_config"),
    ]

    for name, config_attr in integrations:
        if config_attr is None:
            # ADC-based services — always available on Cloud Run
            statuses.append({"name": name, "status": "ok", "detail": "ADC (Cloud Run)"})
            continue
        cfg = getattr(server_mod, config_attr, None)
        if cfg is None:
            statuses.append({"name": name, "status": "error", "detail": "Config failed to load"})
        elif hasattr(cfg, "is_configured") and cfg.is_configured:
            statuses.append({"name": name, "status": "ok", "detail": "Configured"})
        else:
            statuses.append({"name": name, "status": "not_configured", "detail": "Missing credentials"})

    return statuses


async def home_route(request):
    loaded = _tools_loaded.is_set()
    status = "ready" if loaded and not _tools_loading_error else "loading" if not loaded else "error"

    integrations_html = ""
    if loaded and not _tools_loading_error:
        integrations = _get_integration_statuses()
        rows = ""
        for i in integrations:
            if i["status"] == "ok":
                badge = '<span style="color:#16a34a;font-weight:bold">&#x2713; Connected</span>'
            elif i["status"] == "not_configured":
                badge = '<span style="color:#d97706;font-weight:bold">&#x26A0; Not Configured</span>'
            else:
                badge = '<span style="color:#dc2626;font-weight:bold">&#x2717; Error</span>'
            rows += f"<tr><td>{i['name']}</td><td>{badge}</td><td style='color:#6b7280'>{i['detail']}</td></tr>\n"
        integrations_html = f"""
        <h2 style="margin-top:1.5em">Integrations</h2>
        <table style="border-collapse:collapse;width:100%;max-width:600px">
        <tr style="border-bottom:2px solid #e5e7eb;text-align:left"><th style="padding:8px">Service</th><th style="padding:8px">Status</th><th style="padding:8px">Detail</th></tr>
        {rows}
        </table>"""

    return HTMLResponse(f"""<html>
<head><meta charset="utf-8"><title>Crowd IT MCP Server</title>
<style>body{{font-family:system-ui,sans-serif;max-width:700px;margin:2em auto;padding:0 1em}}
table td,table th{{padding:8px;border-bottom:1px solid #e5e7eb}}</style></head>
<body>
<h1>Crowd IT MCP Server</h1>
<p>Status: <b>{status}</b> | Tools: <b>{_tool_count}</b> | Uptime: {round(time.time() - _start)}s</p>
<p>MCP endpoint: <code>/mcp</code> (streamable-http)</p>
{integrations_html}
</body></html>""")

async def health_route(request):
    return PlainTextResponse("OK")

async def status_route(request):
    """JSON status endpoint for programmatic access."""
    loaded = _tools_loaded.is_set()
    return JSONResponse({
        "status": "ready" if loaded and not _tools_loading_error else "loading" if not loaded else "error",
        "tool_count": _tool_count,
        "uptime_seconds": round(time.time() - _start, 1),
        "integrations": _get_integration_statuses() if loaded and not _tools_loading_error else [],
    })


async def debug_mcp_route(request):
    return JSONResponse({
        "status": "ready" if _tools_loaded.is_set() and not _tools_loading_error else "loading",
        "server": "crowdit-mcp-server (fast)",
        "mcp_endpoint": "/mcp",
        "transport": "streamable-http",
        "stateless": True,
        "tool_count": _tool_count,
        "tools_loaded": _tools_loaded.is_set(),
        "loading_error": _tools_loading_error,
        "uptime_seconds": round(time.time() - _start, 1),
    })


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))

    # Create MCP ASGI app
    mcp_app = mcp.http_app(stateless_http=True)

    @asynccontextmanager
    async def lifespan(app):
        """Start tool loading in background during server startup."""
        # Start background tool loading
        loader_thread = threading.Thread(target=_load_tools_background, daemon=True)
        loader_thread.start()
        print(f"[FAST] Tool loader started in background at {_t()}", file=sys.stderr, flush=True)

        # Initialize FastMCP's session manager
        async with mcp_app.lifespan(app):
            yield

    # Build Starlette app with routes
    app = Starlette(
        routes=[
            Route("/", home_route),
            Route("/health", health_route),
            Route("/status", status_route),
            Route("/debug/mcp", debug_mcp_route),
        ],
        lifespan=lifespan,
    )

    # Add auth middleware
    app.add_middleware(SimpleAPIKeyMiddleware)

    # Mount MCP app
    app.mount("/", mcp_app)

    print(f"[FAST] Starting uvicorn on 0.0.0.0:{port} at {_t()}", file=sys.stderr, flush=True)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        timeout_keep_alive=5,
        access_log=False,
        log_level="info",
    )
