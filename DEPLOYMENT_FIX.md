# Cloud Run Deployment Fix

## Problem
The MCP server was failing to deploy to Cloud Run with the error:
```
The user-provided container failed to start and listen on the port defined provided by the PORT=8080 environment variable within the allocated timeout.
```

## Root Cause
The container had a **race condition in the startup sequence**:

1. A raw socket server was binding to port 8080 immediately during module import
2. This socket server was designed to handle Cloud Run health checks while the rest of the app initialized
3. However, **the socket was being closed before uvicorn actually started**
4. When uvicorn tried to bind to port 8080, either:
   - The port wasn't fully released yet (EADDRINUSE)
   - Cloud Run's health checks came in during uvicorn startup and failed
   - The server wasn't responding to health checks before the timeout

## Solution

### 1. Removed Quick Socket Server (server.py, lines 16-57)
- Deleted the pre-startup socket server hack that was causing port conflicts
- Let uvicorn handle the server lifecycle properly
- The `/health` route is now the only health check mechanism

### 2. Improved Uvicorn Configuration (server.py, line ~12810)
Added explicit Cloud Run-optimized settings:
```python
uvicorn.run(
    app, 
    host="0.0.0.0", 
    port=port,
    timeout_keep_alive=5,      # Reduce keep-alive timeout
    timeout_notify=30,         # Timeout for ASGI startup notification
    access_log=True,           # Enable access logs for debugging
    log_level="info"           # Set appropriate log level
)
```

### 3. Dockerfile Improvements
- Added `-u` flag to Python for unbuffered output (better for container logs)
- Added explicit environment variable for uvicorn timeout

### 4. Startup Flow Changes
**Before:**
```
1. Module imports (slow)
2. Quick socket server starts (port 8080)
3. Module continues loading (slow)
4. Socket server closes
5. Uvicorn starts and binds to port 8080
6. Race condition: port not released or health checks fail
```

**After:**
```
1. Module imports with better logging
2. No socket server interfering
3. Module continues loading
4. Uvicorn starts cleanly and binds to port 8080
5. Health checks properly handled by /health route
```

## Why This Works Better for Cloud Run

1. **Simple, predictable startup**: One server listening, not two sequential servers
2. **Proper health check handling**: Cloud Run can hit `/health` at any time once uvicorn binds
3. **Cleaner shutdown**: No socket cleanup needed before uvicorn starts
4. **Better logging**: Unbuffered Python output means real-time logs in Cloud Logging
5. **Proper timeout behavior**: Uvicorn's timeout settings handle the startup phase correctly

## Testing the Fix

To verify the deployment works:

```bash
# Deploy to Cloud Run
./deploy.sh

# Check the latest revision status
gcloud run services describe crowdit-mcp-server --region australia-southeast1 --project crowdmcp

# Check recent logs for startup messages
gcloud run revisions list --service crowdit-mcp-server --region australia-southeast1 --project crowdmcp
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=crowdit-mcp-server" --limit 50 --format json | jq '.[] | .textPayload' | head -30
```

## Deployment Command

```bash
cd /Users/chrisgermon/Documents/GitHub/crowdit-mcp-server
./deploy.sh
```

This will rebuild the container with the fixed startup sequence and deploy to Cloud Run.
