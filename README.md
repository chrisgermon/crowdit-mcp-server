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

Available service names: `halopsa`, `xero`, `front`, `sharepoint`, `quoter`, `pax8`, `bigquery`, `aws_rds`, `aws`, `azure`, `forticloud`, `maxotel`, `ubuntu`, `visionrad`, `cipp`, `salesforce`, `gcp`, `dicker`, `ingram`, `carbon`, `ninjaone`, `crowdit`, `auvik`, `metabase`, `n8n`, `gorelo`, `email`, `jira`, `linear`, `digitalocean`, `github`, `server`, `cloud_run`

### Option 2: Use separate MCP servers per service

For the lowest token usage, run individual MCP servers per integration (e.g., one for HaloPSA, one for Xero). This way each Claude session only loads the tools it actually needs.

## Environment Variables

See Google Secret Manager in `crowdmcp` project for required secrets.

## API Specifications

- **ingram.json** - OpenAPI 3.0 specification for Ingram Micro Reseller API v6 (Australia)
