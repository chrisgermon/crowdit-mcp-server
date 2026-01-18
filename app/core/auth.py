import os
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from app.core.config import get_secret_sync

logger = logging.getLogger(__name__)

# API Key validation middleware
class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware to validate API key for MCP endpoints."""

    # Paths that don't require API key authentication
    PUBLIC_PATHS = {"/health", "/status", "/callback", "/sharepoint-callback", "/"}

    def __init__(self, app, api_key: str = None):
        super().__init__(app)
        # Store the initial key but defer Secret Manager lookup to first request
        self._api_key = api_key or os.getenv("MCP_API_KEY")
        self._key_loaded = self._api_key is not None
        if self._api_key:
            logger.info("🔐 API Key authentication enabled for MCP endpoints")
        else:
            logger.info("🔑 API Key will be loaded from Secret Manager on first request")

    @property
    def api_key(self):
        """Lazily load API key from Secret Manager if not already set."""
        if not self._key_loaded:
            self._api_key = get_secret_sync("MCP_API_KEY")
            self._key_loaded = True
            if self._api_key:
                logger.info("🔐 API Key loaded from Secret Manager")
            else:
                logger.warning("⚠️ No MCP_API_KEY configured - endpoints are unprotected!")
        return self._api_key
    
    async def dispatch(self, request, call_next):
        path = request.url.path
        
        # Allow public paths without authentication
        if path in self.PUBLIC_PATHS:
            return await call_next(request)
        
        # If no API key is configured, allow all requests (backwards compatible)
        if not self.api_key:
            return await call_next(request)
        
        # Check for API key in query params or headers
        provided_key = (
            request.query_params.get("api_key") or 
            request.headers.get("X-API-Key") or
            request.headers.get("Authorization", "").replace("Bearer ", "")
        )
        
        if provided_key != self.api_key:
            logger.warning(f"🚫 Unauthorized request to {path} from {request.client.host if request.client else 'unknown'}")
            return PlainTextResponse("Unauthorized - Invalid or missing API key", status_code=401)
        
        return await call_next(request)
