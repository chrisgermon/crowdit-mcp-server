# Crowd IT MCP Server

Unified MCP (Model Context Protocol) server for Crowd IT business integrations.

## Integrations

- **HaloPSA** - Ticket management, client lookup
- **Xero** - Invoicing, accounting, payroll
- **Front** - Email/conversation management
- **Quoter** - Quotes and proposals

## Deployment

Automatically deployed to Cloud Run on push to `main` branch.

**Cloud Run URL:** https://crowdit-mcp-server-348600156950.australia-southeast1.run.app

## Local Development

```bash
pip install -r pyproject.toml
python server.py
```

## Environment Variables

See Google Secret Manager in `crowdmcp` project for required secrets.
