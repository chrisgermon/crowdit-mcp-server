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

async def home_route(request):
    loaded = _tools_loaded.is_set()
    status = "ready" if loaded and not _tools_loading_error else "loading" if not loaded else "error"
    return HTMLResponse(f"""<html><body>
<h1>Crowd IT MCP Server</h1>
<p>Status: <b>{status}</b> | Tools: {_tool_count}</p>
<p>MCP endpoint: <code>/mcp</code> (streamable-http)</p>
</body></html>""")

async def health_route(request):
    return PlainTextResponse("OK")

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
