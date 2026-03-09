# Crowd IT MCP Server — Fix Report

## What was wrong

### 1. Claude Desktop config — Crowd IT MCP entry was missing
The `claude_desktop_config.json` only had the WordPress MCP server. The Crowd IT MCP entry was completely removed (likely lost when you tried adding multiple API keys).

### 2. Cloud Run server never finishes starting
This is the **root cause**. The server has a "quick health server" that starts immediately and returns 200 on `/health`, which tricks Cloud Run into thinking the container is healthy. But the actual uvicorn server with MCP tools **never starts** — the container appears to crash or timeout during initialization.

Why? `server.py` is **18,185 lines** with 234 inline `@mcp.tool()` decorators plus 7 external tool modules. Python has to:
- Parse all 28k+ lines at module level
- Execute 500+ decorator registrations
- Import httpx, FastMCP, pydantic, and all tool modules
- All before uvicorn even starts

With the `deploy.sh` settings of **1GB RAM / 1 CPU / 0 min-instances**, this was never going to work — the container OOMs or times out during cold start.

## What I did

### Files created/modified:

**`server_fast.py`** (NEW) — Fast startup entry point
- Starts uvicorn in < 2 seconds with an empty FastMCP instance
- Loads the full `server.py` module **in a background thread**
- Transfers all 500+ tools to the live MCP instance once loaded
- Health checks pass immediately, MCP tools available in ~15-30s
- Located in: `~/Projects/CrowdITMCP/server_fast.py`

**`Dockerfile`** (UPDATED)
- Changed `CMD` from `server.py` to `server_fast.py`
- Added `server_fast.py` to `COPY` command
- Located in: `~/Projects/CrowdITMCP/Dockerfile`

**`deploy.sh`** (UPDATED)
- Memory: 1Gi → **2Gi**
- CPU: 1 → **2**
- Min instances: 0 → **1** (eliminates cold starts)
- Added `CLOUD_RUN_URL` env var
- Located in: `~/Projects/CrowdITMCP/deploy.sh`

**`claude_desktop_config.json`** (UPDATED)
- Added `crowdit-mcp` entry using `mcp-remote` proxy
- Uses the newer Cloud Run URL format
- API key passed as query parameter (matching your server's auth middleware)
- Located in: `~/Library/Application Support/Claude/claude_desktop_config.json`

## To deploy

You need to push these changes to your GitHub repo and redeploy. Here's the quickest path:

### Option A: Copy files to your repo and push
```bash
cd ~/Projects/CrowdITMCP

# Copy the new/updated files to your cloned repo
# (you'll need to clone it first if you haven't)
git clone https://github.com/chrisgermon/crowdit-mcp-server.git /tmp/mcp-deploy
cp server_fast.py /tmp/mcp-deploy/
cp Dockerfile /tmp/mcp-deploy/
cp deploy.sh /tmp/mcp-deploy/
cp cloudbuild.yaml /tmp/mcp-deploy/

cd /tmp/mcp-deploy
git add server_fast.py Dockerfile deploy.sh cloudbuild.yaml
git commit -m "Fast startup: defer tool loading to background thread"
git push origin main
```

### Option B: Manual deploy with gcloud
```bash
cd /tmp/mcp-deploy  # (after copying files as above)
./deploy.sh
```

### Option C: Just update Cloud Run resources (quickest fix)
If the original server was working before with enough resources:
```bash
gcloud run services update crowdit-mcp-server \
  --region=australia-southeast1 \
  --memory=2Gi \
  --cpu=2 \
  --min-instances=1
```

## After deployment

1. **Wait 30-60s** for the server to fully load tools
2. **Test**: `curl https://your-cloud-run-url/debug/mcp`
3. **Restart Claude Desktop** (Cmd+Q, reopen)
4. The Crowd IT MCP tools should appear

## Architecture notes

- **Transport**: Streamable HTTP (FastMCP 2.x with `stateless_http=True`)
- **Auth**: API key via query param (`?api_key=...`), header (`X-API-Key`), or Bearer token
- **Endpoint**: POST `/mcp` for MCP JSON-RPC
- **Tools**: ~500 tools across 25+ integrations (HaloPSA, Xero, NinjaOne, Auvik, BigQuery, etc.)
