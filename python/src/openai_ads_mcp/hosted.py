"""Hosted HTTP / SSE transport for custom MCP clients.

Matches the webloom pattern used by link-finder-mcp and mcp-google-ads:
  - Product auth: OPENAI_ADS_API_KEY in the server environment (no OAuth yet)
  - Transport auth: MCP_BEARER_TOKEN as Authorization: Bearer …
  - Streamable HTTP at /mcp (recommended for Render and any remote client)
  - Unauthenticated / and /healthz for PaaS health probes
  - CORS enabled for browser-based MCP clients
"""

from __future__ import annotations

import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from ._core import MCP_HTTP_PATH, mcp
from .client import __version__

# Reject copy-pasted docs / example values so HTTP deploys cannot ship with a
# publicly known bearer token.
_MIN_BEARER_TOKEN_LEN = 24
_PLACEHOLDER_BEARER_TOKENS = frozenset(
    {
        "change_me_to_a_long_random_string",
        "change_me",
        "changeme",
        "your_mcp_bearer_token",
        "your-mcp-bearer-token",
        "replace_me",
        "replace-me",
        "secret",
        "password",
        "token",
        "bearer",
        "mcp_bearer_token",
        "openai_ads_mcp_http_token",
    }
)


def mcp_bearer_token() -> str:
    """Shared secret clients must send as Authorization: Bearer <token>.

    Prefers MCP_BEARER_TOKEN (webloom convention); falls back to
    OPENAI_ADS_MCP_HTTP_TOKEN for compatibility with the Node runtime.
    """
    return (
        os.getenv("MCP_BEARER_TOKEN", "").strip()
        or os.getenv("OPENAI_ADS_MCP_HTTP_TOKEN", "").strip()
    )


def validate_mcp_bearer_token(token: str | None = None) -> str:
    """Return a usable bearer token or raise ValueError."""
    value = (token if token is not None else mcp_bearer_token()).strip()
    if not value:
        raise ValueError(
            "MCP_BEARER_TOKEN is required when MCP_TRANSPORT is 'http' or 'sse'. "
            "Generate one with: openssl rand -hex 32 "
            "(OPENAI_ADS_MCP_HTTP_TOKEN is accepted as an alias.)"
        )
    if value.lower() in _PLACEHOLDER_BEARER_TOKENS:
        raise ValueError(
            "MCP_BEARER_TOKEN looks like a documentation placeholder. "
            "Generate a real secret with: openssl rand -hex 32"
        )
    if len(value) < _MIN_BEARER_TOKEN_LEN:
        raise ValueError(
            f"MCP_BEARER_TOKEN must be at least {_MIN_BEARER_TOKEN_LEN} characters. "
            "Generate one with: openssl rand -hex 32"
        )
    return value


async def _health(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def _root(_request: Request) -> JSONResponse:
    from ._core import is_readonly_mode

    mcp_path = MCP_HTTP_PATH if MCP_HTTP_PATH.startswith("/") else f"/{MCP_HTTP_PATH}"
    return JSONResponse(
        {
            "name": "openai-ads-mcp",
            "version": __version__,
            "transport": "streamable-http",
            "mcp_path": mcp_path,
            "auth": "bearer",
            "oauth": False,
            "readonly": is_readonly_mode(),
            "note": (
                "OpenAI Ads has no OAuth yet. Connect with URL + "
                "Authorization: Bearer <MCP_BEARER_TOKEN>. "
                "Read-only by default; set OPENAI_ADS_MCP_ALLOW_WRITES=1 to edit campaigns."
            ),
        }
    )


def build_hosted_app(transport: str = "http"):
    """Build a Starlette app guarded by bearer-token + CORS middleware."""
    transport = transport.strip().lower()
    if transport not in {"http", "sse"}:
        raise ValueError(f"Unsupported hosted transport '{transport}'. Use 'http' or 'sse'.")

    # Hosted deployments stay read-only unless writes are explicitly enabled.
    # Tool registration already happened at import; this keeps env + docs aligned
    # and fails closed if someone enables writes without a bearer token.
    from ._core import is_readonly_mode

    if not is_readonly_mode():
        os.environ.setdefault("OPENAI_ADS_MCP_ALLOW_WRITES", "1")
    else:
        os.environ["OPENAI_ADS_MCP_READONLY"] = "1"

    token = validate_mcp_bearer_token()

    mcp_base = MCP_HTTP_PATH.rstrip("/") or "/mcp"
    public_exact = {"/", "/healthz", "/health", "/ready"}
    protected_prefixes = (mcp_base, "/sse", "/messages", "/mcp")

    class BearerTokenMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method.upper() == "OPTIONS":
                return await call_next(request)

            path = request.url.path
            if path in public_exact:
                return await call_next(request)

            protected = any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in protected_prefixes
            )
            if not protected:
                return await call_next(request)

            auth_header = request.headers.get("Authorization", "")
            presented = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            authorized = (
                bool(presented)
                and len(presented) == len(token)
                and hmac.compare_digest(presented, token)
            )
            if not authorized:
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "Unauthorized. Send 'Authorization: Bearer <MCP_BEARER_TOKEN>'."
                    },
                )
            return await call_next(request)

    # Keep FastMCP's own Starlette app (and its lifespan) — do not Mount it.
    if transport == "http":
        app = mcp.streamable_http_app()
    else:
        app = mcp.sse_app()

    app.add_route("/", _root, methods=["GET"])
    app.add_route("/healthz", _health, methods=["GET"])
    app.add_route("/health", _health, methods=["GET"])
    app.add_route("/ready", _health, methods=["GET"])

    # Starlette runs middleware in reverse add order: CORS outermost, then bearer.
    app.add_middleware(BearerTokenMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app
