# Crowd IT MCP Server

Unified MCP (Model Context Protocol) server for Crowd IT business integrations.

## Integrations

### PSA & Service Management
- **HaloPSA** - Ticket management, client lookup, assets, contracts, projects, quotes, time tracking, knowledge base, recurring invoices, items/inventory

### Finance & Accounting
- **Xero** - Invoicing, accounting, contacts, bills, payments, credit notes, quotes, purchase orders, bank transactions, financial reports

### Communication & Collaboration
- **Front** - Email/conversation management, tagging
- **SharePoint** - Document management, file operations, site management

### Quoting & Sales
- **Quoter** - Quotes and proposals, contacts, items, templates
- **Salesforce** - CRM, SOQL queries, MLO reports, worksite reports

### Cloud & Subscription Management
- **Pax8** - Cloud subscription management, products catalog
- **CIPP** - Microsoft 365 administration, tenant management, Graph API

### Data & Analytics
- **BigQuery** - Google BigQuery data analytics, queries, dataset management
- **AWS RDS** - Database management, SQL queries

### Infrastructure & Networking
- **Azure** - Resource management, VNets, NSGs, VMs, storage, cost management, Azure AD
- **FortiCloud** - Fortinet device management, VPN status, alerts, logs, configuration
- **GCP** - Google Cloud Platform VM management, logs, gcloud CLI
- **OVHCloud** - Public Cloud (instances, volumes, images, flavors), dedicated servers, VPS, domains & DNS, IPs, vRack, billing
- **Ubuntu Server** - Remote server management via SSH
- **VisionRad Server** - Remote server management, BigQuery sync status

### Telecommunications
- **Maxotel** - VoIP services, CDR records, billing, customer management

### Distributors
- **Dicker Data** - Product search, pricing, stock availability, vendors, categories
- **Ingram Micro** - Product catalog, pricing & availability, orders, quotes, invoices, webhooks (Australia)

### ISP Services
- **Carbon (Aussie Broadband)** - ISP services, NBN address checks, service tests, usage, orders, tickets

### Automation
- **n8n** - Workflow automation management, executions, tags, variables, projects

## Deployment

Automatically deployed to Cloud Run on push to `main` branch.

**Cloud Run URL:** https://crowdit-mcp-server-348600156950.australia-southeast1.run.app

## Connecting from Claude Code (or Claude desktop)

If you see **"There was an error connecting to the MCP server. Please check your server URL and make sure your server handles auth correctly"** after being sent to a `start-auth` URL, Claude is trying to use **OAuth** to connect. This server does **not** use OAuth for the MCP endpoint — it uses **API key** auth (or Cloud Run IAM when deployed).

**Fix:** Configure the connection so Claude sends your API key on every request. That way Claude will not open the OAuth flow.

1. **Get an API key**  
   Use a key that is configured on the server (from Secret Manager `MCP_API_KEY` / `MCP_API_KEYS` or env var `MCP_API_KEY`). If no keys are set, the server may allow unauthenticated access (e.g. when using Cloud Run IAM).

2. **Add the server with an auth header**  
   In Claude Code, add the MCP server with the **URL** and a **header** so the client does not attempt OAuth:

   - **URL:** `https://crowdit-mcp-server-348600156950.australia-southeast1.run.app/mcp`
   - **Transport:** streamable-http (or HTTP, if that’s the only option).
   - **Header:**  
     - `Authorization: Bearer YOUR_MCP_API_KEY`  
     or  
     - `X-API-Key: YOUR_MCP_API_KEY`

   Example (if your client supports it):
   ```bash
   claude mcp add crowdit https://crowdit-mcp-server-348600156950.australia-southeast1.run.app/mcp -t http -H "Authorization: Bearer YOUR_MCP_API_KEY"
   ```

   If you use a config file (e.g. `.mcp.json` or Cursor/Claude settings), set:
   - `url`: `https://crowdit-mcp-server-348600156950.australia-southeast1.run.app/mcp`
   - `headers`: `{ "Authorization": "Bearer YOUR_MCP_API_KEY" }` or `{ "X-API-Key": "YOUR_MCP_API_KEY" }`

3. **Use the full `/mcp` path**  
   The MCP endpoint is `/mcp`, not the root URL. Use `...run.app/mcp` as above.

Once the API key is sent in the header, Claude should connect without opening the start-auth page.

## Local Development

```bash
pip install -r pyproject.toml
python server.py
```

## Reducing Token Usage

This server registers 500+ tools. When connected to an LLM like Claude, every tool definition is sent on each API call (~150 tokens per tool = ~75,000 tokens/turn overhead).

### Option 1: Filter services with ENABLED_SERVICES

Set the `ENABLED_SERVICES` environment variable to only load the services you need:

```bash
# Only load HaloPSA and Xero tools (~70 tools instead of 500+)
ENABLED_SERVICES=halopsa,xero python server.py
```

Available service names: `halopsa`, `xero`, `front`, `sharepoint`, `quoter`, `pax8`, `bigquery`, `aws_rds`, `aws`, `azure`, `forticloud`, `maxotel`, `ubuntu`, `visionrad`, `cipp`, `salesforce`, `gcp`, `dicker`, `ingram`, `carbon`, `ninjaone`, `crowdit`, `auvik`, `metabase`, `n8n`, `gorelo`, `email`, `jira`, `linear`, `digitalocean`, `ovhcloud`, `github`, `server`, `cloud_run`

### Option 2: Split into separate MCP servers (best token use and startup)

For the **lowest token usage** and **fastest startup**, run one MCP server per integration (e.g. one for HaloPSA, one for Xero). Each client then connects only to the servers it needs, so:

- **Token use**: Only that server’s tool definitions are sent each turn (e.g. ~70 tools instead of 500+).
- **Startup**: Smaller servers start much faster than the full monolithic server.
- **Reliability**: A failure in one integration doesn’t affect others.

Ways to do it:

- **Same codebase, multiple processes**: Run the same app multiple times with different `ENABLED_SERVICES` (e.g. `ENABLED_SERVICES=halopsa` on port 8081, `ENABLED_SERVICES=xero` on 8082) and point clients at the appropriate URL.
- **Split repos/deployments**: Maintain separate small MCP servers per integration and deploy them independently (e.g. separate Cloud Run services). Best long-term for many teams; more ops overhead.

Until you split, **Option 1 (ENABLED_SERVICES)** is the simplest way to cut tokens and speed up the single server.

## Environment Variables

See Google Secret Manager in `crowdmcp` project for required secrets.

### Linear (multi-tenant)

The Linear integration can connect to **multiple Linear workspaces / accounts**.
Each workspace is called a *tenant* and has its own Personal API key. Every
`linear_*` tool accepts an optional `tenant` argument; when omitted, the
configured default tenant is used. Use `linear_list_tenants` to see what's
configured.

Configure tenants via any of the following (combined, in this priority order):

1. **`LINEAR_TENANTS`** - JSON mapping of tenant name → API key. Stored in
   Google Secret Manager or as an env var.

   ```json
   {
     "crowdit": "lin_api_xxxxxxxxxxxxxxxxxxxx",
     "acme":    "lin_api_yyyyyyyyyyyyyyyyyyyy"
   }
   ```

   Each value may also be an object: `{"api_key": "lin_api_..."}`.

2. **`LINEAR_API_KEY_<NAME>`** - Per-tenant env vars. The suffix becomes the
   tenant name (lower-cased). For example, `LINEAR_API_KEY_ACME=lin_api_...`
   registers a tenant called `acme`.

3. **`LINEAR_API_KEY`** - Legacy single-tenant key. Registered as the tenant
   named `default`. Existing single-workspace deployments keep working with no
   changes.

**`LINEAR_DEFAULT_TENANT`** optionally names the tenant to use when a tool
call omits `tenant`. If unset, `default` is used if present, otherwise the
first registered tenant.

### OVHCloud

The OVHCloud integration uses OVH's signed-request authentication. Create an
application at https://www.ovh.com/auth/api/createApp (or the regional
equivalent), then issue a consumer key (`/auth/credential`) with the rules you
need. Set the following secrets / env vars:

- `OVH_APPLICATION_KEY`
- `OVH_APPLICATION_SECRET`
- `OVH_CONSUMER_KEY`
- `OVH_ENDPOINT` (optional, default `ovh-eu`; one of: `ovh-eu`, `ovh-us`,
  `ovh-ca`, `kimsufi-eu`, `kimsufi-ca`, `soyoustart-eu`, `soyoustart-ca`)

## API Specifications

- **ingram.json** - OpenAPI 3.0 specification for Ingram Micro Reseller API v6 (Australia)
