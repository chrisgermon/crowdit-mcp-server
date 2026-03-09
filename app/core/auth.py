import os
import time
import asyncio
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from app.core.config import get_secret_sync

logger = logging.getLogger(__name__)


def _mask_key(key: str) -> str:
    """Mask a key for safe logging: show first 4 and last 4 chars."""
    if len(key) <= 10:
        return key[:2] + "***" + key[-2:] if len(key) > 4 else "***"
    return key[:4] + "***" + key[-4:]


def _parse_keys(raw: str) -> set:
    """Parse a comma-separated or newline-separated list of API keys into a set."""
    if not raw:
        return set()
    keys = {k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()}
    return keys


# API Key validation middleware
class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware to validate API key for MCP endpoints.

    Supports multiple API keys via the MCP_API_KEYS secret (comma-separated).
    Falls back to the legacy MCP_API_KEY secret/env var for backward compatibility.
    Both secrets are merged, so you can use either or both simultaneously.

    When running on Cloud Run with --no-allow-unauthenticated, requests have
    already been authenticated by Cloud Run IAM. In this case, the API key is
    optional — if provided it must be valid, but if omitted the request is
    allowed through (Cloud Run IAM is sufficient).
    """

    # Paths that don't require API key authentication
    PUBLIC_PATHS = {"/health", "/status", "/callback", "/sharepoint-callback", "/", "/debug/mcp"}

    # Path prefixes that don't require API key authentication
    # .well-known paths are OAuth discovery endpoints required by MCP spec
    PUBLIC_PREFIXES = ("/.well-known/",)

    # Minimum seconds between Secret Manager retry attempts after a failure
    _RETRY_COOLDOWN = 30

    def __init__(self, app, api_key: str = None):
        super().__init__(app)
        # Seed from the legacy single-key param/env (may be empty)
        self._seed_key = (api_key or os.getenv("MCP_API_KEY") or "").strip() or None
        self._valid_keys: set = set()
        self._keys_loaded = False
        self._last_load_attempt = 0.0
        self._load_error: str | None = None

        # Detect Cloud Run environment — K_SERVICE is always set on Cloud Run
        self._on_cloud_run = bool(os.getenv("K_SERVICE"))

        if self._on_cloud_run:
            logger.info("[AUTH] Running on Cloud Run — API key is optional (Cloud Run IAM provides authentication)")
        if self._seed_key:
            logger.info("[AUTH] API Key authentication enabled (will merge with Secret Manager on first request)")
        else:
            logger.info("[AUTH] API Keys will be loaded from Secret Manager on first request")

    def _load_keys_sync(self):
        """Load and merge keys from all sources (runs in thread pool)."""
        keys = set()
        sources = []

        # 1. Legacy single key from env / constructor arg
        if self._seed_key:
            keys.add(self._seed_key)
            sources.append(f"env/constructor: {_mask_key(self._seed_key)}")

        # 2. Legacy single key from Secret Manager (MCP_API_KEY)
        try:
            legacy = get_secret_sync("MCP_API_KEY")
            if legacy:
                parsed = _parse_keys(legacy)
                keys.update(parsed)
                sources.append(f"MCP_API_KEY secret: {len(parsed)} key(s) [{', '.join(_mask_key(k) for k in parsed)}]")
            else:
                sources.append("MCP_API_KEY secret: empty/not found")
        except Exception as e:
            sources.append(f"MCP_API_KEY secret: ERROR - {e}")
            self._load_error = str(e)

        # 3. Multi-key secret (MCP_API_KEYS) — comma or newline separated
        try:
            multi = get_secret_sync("MCP_API_KEYS")
            if multi:
                parsed = _parse_keys(multi)
                keys.update(parsed)
                sources.append(f"MCP_API_KEYS secret: {len(parsed)} key(s) [{', '.join(_mask_key(k) for k in parsed)}]")
            else:
                sources.append("MCP_API_KEYS secret: empty/not found")
        except Exception as e:
            sources.append(f"MCP_API_KEYS secret: ERROR - {e}")
            self._load_error = str(e)

        self._valid_keys = keys
        self._last_load_attempt = time.monotonic()

        # Log detailed results
        for src in sources:
            logger.info(f"[AUTH] Key source: {src}")

        if keys:
            logger.info(f"[AUTH] {len(keys)} unique API key(s) loaded and active")
            self._keys_loaded = True
            self._load_error = None
        else:
            # DON'T set _keys_loaded = True on failure — allow retry
            logger.warning("[AUTH] No API keys loaded from any source! Will retry on next request.")

    async def _ensure_keys_loaded(self):
        """Ensure keys are loaded, using thread pool to avoid blocking the event loop."""
        if self._keys_loaded:
            return

        # Cooldown: don't retry too frequently after failures
        now = time.monotonic()
        if self._last_load_attempt > 0 and (now - self._last_load_attempt) < self._RETRY_COOLDOWN:
            return

        logger.info("[AUTH] Loading API keys from Secret Manager (async)...")
        try:
            await asyncio.to_thread(self._load_keys_sync)
        except Exception as e:
            logger.error(f"[AUTH] Failed to load keys: {e}")
            self._last_load_attempt = time.monotonic()
            self._load_error = str(e)

    async def dispatch(self, request, call_next):
        path = request.url.path

        # Log all requests to /mcp for debugging Claude connector issues
        if path.startswith("/mcp"):
            client_host = request.client.host if request.client else "unknown"
            logger.info(
                f"[AUTH] MCP request: {request.method} {path} from {client_host} "
                f"headers={dict((k, v) for k, v in request.headers.items() if k.lower() in ('content-type', 'accept', 'authorization', 'x-api-key', 'origin', 'user-agent'))} "
                f"on_cloud_run={self._on_cloud_run}"
            )

        # Allow CORS preflight requests through without authentication
        # OPTIONS requests don't carry auth headers and must pass for CORS to work
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow public paths without authentication
        if path in self.PUBLIC_PATHS or path.startswith(self.PUBLIC_PREFIXES):
            return await call_next(request)

        # Ensure keys are loaded (non-blocking)
        await self._ensure_keys_loaded()

        # If no keys configured, allow all requests (backward compatible)
        if not self._valid_keys:
            if self._load_error:
                logger.warning(f"[AUTH] Allowing request to {path} — no keys loaded (last error: {self._load_error})")
            return await call_next(request)

        # Check for API key in query params or headers
        provided_key = (
            request.query_params.get("api_key") or
            request.headers.get("X-API-Key") or
            ""
        ).strip()

        # Extract Bearer token from Authorization header if no key found yet
        if not provided_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided_key = auth_header[7:].strip()

        if provided_key:
            # Key was provided — it must be valid regardless of environment
            if provided_key in self._valid_keys:
                return await call_next(request)
            # Invalid key — reject
            client_host = request.client.host if request.client else "unknown"
            masked_provided = _mask_key(provided_key)
            masked_valid = ", ".join(_mask_key(k) for k in self._valid_keys)
            logger.warning(
                f"[AUTH] 401 Unauthorized: {request.method} {path} from {client_host} — "
                f"provided key: {masked_provided}, valid keys: [{masked_valid}]"
            )
            return PlainTextResponse("Unauthorized - Invalid or missing API key", status_code=401)

        # No API key provided
        if self._on_cloud_run:
            # On Cloud Run with --no-allow-unauthenticated, the request has
            # already been authenticated by Cloud Run IAM at the infrastructure
            # level. Allow it through without an API key.
            return await call_next(request)

        # Not on Cloud Run and no key provided — reject
        client_host = request.client.host if request.client else "unknown"
        logger.warning(
            f"[AUTH] 401 Unauthorized (no key): {request.method} {path} from {client_host}"
        )
        return PlainTextResponse("Unauthorized - Invalid or missing API key", status_code=401)
