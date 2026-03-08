import os
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from app.core.config import get_secret_sync

logger = logging.getLogger(__name__)


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
    """

    # Paths that don't require API key authentication
    PUBLIC_PATHS = {"/health", "/status", "/callback", "/sharepoint-callback", "/"}

    def __init__(self, app, api_key: str = None):
        super().__init__(app)
        # Seed from the legacy single-key param/env (may be empty)
        self._seed_key = api_key or os.getenv("MCP_API_KEY")
        self._valid_keys: set = set()
        self._keys_loaded = False

        if self._seed_key:
            logger.info("🔐 API Key authentication enabled (will merge with Secret Manager on first request)")
        else:
            logger.info("🔑 API Keys will be loaded from Secret Manager on first request")

    def _load_keys(self):
        """Load and merge keys from all sources (called once on first request)."""
        keys = set()

        # 1. Legacy single key from env / constructor arg
        if self._seed_key:
            keys.add(self._seed_key)

        # 2. Legacy single key from Secret Manager (MCP_API_KEY)
        legacy = get_secret_sync("MCP_API_KEY")
        if legacy:
            keys.update(_parse_keys(legacy))

        # 3. Multi-key secret (MCP_API_KEYS) — comma or newline separated
        multi = get_secret_sync("MCP_API_KEYS")
        if multi:
            keys.update(_parse_keys(multi))

        self._valid_keys = keys
        self._keys_loaded = True

        if keys:
            logger.info(f"🔐 {len(keys)} API key(s) loaded and active")
        else:
            logger.warning("⚠️ No API keys configured — endpoints are unprotected!")

    @property
    def valid_keys(self) -> set:
        """Lazily load all valid API keys on first request."""
        if not self._keys_loaded:
            self._load_keys()
        return self._valid_keys

    async def dispatch(self, request, call_next):
        path = request.url.path

        # Allow CORS preflight requests through without authentication
        # OPTIONS requests don't carry auth headers and must pass for CORS to work
        if request.method == "OPTIONS":
            return await call_next(request)

        # Allow public paths without authentication
        if path in self.PUBLIC_PATHS:
            return await call_next(request)

        # If no keys configured, allow all requests (backward compatible)
        if not self.valid_keys:
            return await call_next(request)

        # Check for API key in query params or headers
        provided_key = (
            request.query_params.get("api_key") or
            request.headers.get("X-API-Key") or
            ""
        )

        # Extract Bearer token from Authorization header if no key found yet
        if not provided_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided_key = auth_header[7:]  # len("Bearer ") == 7

        if not provided_key or provided_key not in self.valid_keys:
            client_host = request.client.host if request.client else "unknown"
            logger.warning(
                f"Unauthorized {request.method} {path} from {client_host} "
                f"(key provided: {bool(provided_key)})"
            )
            return PlainTextResponse("Unauthorized - Invalid or missing API key", status_code=401)

        return await call_next(request)
