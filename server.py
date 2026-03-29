import os
import logging
import importlib

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="crowdit-mcp-server",
    instructions="Crowd IT Unified MCP Server",
)

_configs_initialized = False
_aws_config = None
_email_config = None
_jira_config = None
_linear_config = None
_notion_config = None
_do_config = None
_proxmox_config = None
_xero_config = None
_gorelo_config = None


def _initialize_configs_once() -> None:
    global _configs_initialized
    global _aws_config, _email_config, _jira_config, _linear_config, _notion_config, _do_config, _proxmox_config, _xero_config, _gorelo_config
    if _configs_initialized:
        return

    try:
        from aws_tools import AWSConfig
        _aws_config = AWSConfig()
    except Exception as e:
        logger.warning(f"Failed to init AWSConfig: {e}")
        _aws_config = None

    try:
        from email_tools import EmailConfig
        _email_config = EmailConfig()
    except Exception as e:
        logger.warning(f"Failed to init EmailConfig: {e}")
        _email_config = None

    try:
        from jira_tools import JiraConfig
        _jira_config = JiraConfig()
    except Exception as e:
        logger.warning(f"Failed to init JiraConfig: {e}")
        _jira_config = None

    try:
        from linear_tools import LinearConfig
        _linear_config = LinearConfig()
    except Exception as e:
        logger.warning(f"Failed to init LinearConfig: {e}")
        _linear_config = None

    try:
        from notion_tools import NotionConfig
        _notion_config = NotionConfig()
    except Exception as e:
        logger.warning(f"Failed to init NotionConfig: {e}")
        _notion_config = None

    try:
        from digitalocean_tools import DigitalOceanConfig
        _do_config = DigitalOceanConfig()
    except Exception as e:
        logger.warning(f"Failed to init DigitalOceanConfig: {e}")
        _do_config = None

    try:
        from proxmox_tools import ProxmoxConfig
        _proxmox_config = ProxmoxConfig()
    except Exception as e:
        logger.warning(f"Failed to init ProxmoxConfig: {e}")
        _proxmox_config = None

    try:
        from xero_tools import XeroConfig
        _xero_config = XeroConfig()
    except Exception as e:
        logger.warning(f"Failed to init XeroConfig: {e}")
        _xero_config = None

    try:
        from gorelo_tools import GoreloConfig
        _gorelo_config = GoreloConfig()
    except Exception as e:
        logger.warning(f"Failed to init GoreloConfig: {e}")
        _gorelo_config = None

    _configs_initialized = True


def _enabled_services() -> set[str] | None:
    raw = (os.getenv("ENABLED_SERVICES") or "").strip()
    if not raw:
        return None
    return {s.strip().lower() for s in raw.replace("\n", ",").split(",") if s.strip()}


def _register_tools() -> None:
    enabled = _enabled_services()
    _initialize_configs_once()

    def want(name: str) -> bool:
        return True if enabled is None else name in enabled

    registrations: list[tuple[str, str, str, tuple[object, ...]]] = [
        ("aws", "aws_tools", "register_aws_tools", (_aws_config,)),
        ("azure", "azure_tools", "register_azure_tools", ()),
        ("email", "email_tools", "register_email_tools", (_email_config,)),
        ("calendar", "calendar_tools", "register_calendar_tools", (_email_config,)),
        ("jira", "jira_tools", "register_jira_tools", (_jira_config,)),
        ("linear", "linear_tools", "register_linear_tools", (_linear_config,)),
        ("notion", "notion_tools", "register_notion_tools", (_notion_config,)),
        ("digitalocean", "digitalocean_tools", "register_digitalocean_tools", (_do_config,)),
        ("proxmox", "proxmox_tools", "register_proxmox_tools", (_proxmox_config,)),
        ("xero", "xero_tools", "register_xero_tools", (_xero_config,)),
        ("gcp_compute", "gcp_compute_tools", "register_gcp_compute_tools", ()),
        ("gorelo", "gorelo_tools", "register_gorelo_tools", (_gorelo_config,)),
    ]

    for service, module_name, register_name, args in registrations:
        if not want(service):
            continue
        if any(a is None for a in args):
            logger.warning(f"Skipping {service} tools (missing configuration object)")
            continue
        try:
            mod = importlib.import_module(module_name)
            register = getattr(mod, register_name)
            register(mcp, *args)
        except Exception as e:
            logger.warning(f"Failed to register {service} tools: {e}")


_register_tools()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    app = mcp.http_app(stateless_http=True)
    uvicorn.run(app, host="0.0.0.0", port=port, access_log=False, log_level="info")
