"""OpenAI Ads MCP Server, typed tools for the OpenAI Advertiser API."""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .client import __version__
from ._core import mcp
from .tools_account import *
from .tools_campaigns import *
from .tools_adgroups import *
from .tools_ads import *
from .tools_insights import *
from .tools_audiences import *
from .tools_conversions import *
from .helpers import *

# Module-level ASGI app for `uvicorn openai_ads_mcp:app` (Render / any PaaS).
# Built only when MCP_TRANSPORT is http/sse so local stdio imports stay simple.
app = None


def _hosted_app_if_configured():
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in {"http", "sse"}:
        return None
    from .hosted import build_hosted_app

    return build_hosted_app(transport)


def main() -> None:
    """Entry point for stdio (local) or http/sse (hosted custom MCP clients)."""
    global app
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if transport in {"http", "sse"}:
        import uvicorn

        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "8000"))
        asgi_app = app or _hosted_app_if_configured()
        app = asgi_app
        uvicorn.run(asgi_app, host=host, port=port, log_level="info")
        return

    raise ValueError(
        f"Unknown MCP_TRANSPORT '{transport}'. Use 'stdio', 'http', or 'sse'."
    )


# Eager ASGI export when hosted env is already set (e.g. Render start / uvicorn).
try:
    app = _hosted_app_if_configured()
except ValueError:
    # Missing MCP_BEARER_TOKEN during import — main() will raise with a clear error.
    app = None



__all__ = [
    "main",
    "mcp",
    "app",
    "__version__",
]
