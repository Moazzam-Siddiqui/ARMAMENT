"""HTTP surface for the MCP server.

TrueForge connects to remote MCP servers over streamable HTTP with static
header auth, so this wraps the SDK's ASGI app in a bearer check. The server is
run stateless: the harness holds the conversation state, so there is nothing
here worth keeping between calls, and statelessness removes a class of
cross-session bugs.
"""

from __future__ import annotations

import secrets

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from .config import Config
from .docker_client import DockerClient
from .tools.read import register_read_tools
from .tools.write import register_write_tools

SERVER_NAME = "sentinel-ops"
SERVER_VERSION = "0.1.0"


def build_mcp(docker: DockerClient) -> MCPServer:
    mcp = MCPServer(name=SERVER_NAME, version=SERVER_VERSION)
    register_read_tools(mcp, docker)
    register_write_tools(mcp, docker)
    return mcp


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Rejects any request to the MCP endpoint without the shared secret.

    The token is compared in constant time, so it cannot be recovered one byte
    at a time by measuring how long a rejection takes.
    """

    def __init__(self, app: ASGIApp, token: str, protected_path: str) -> None:
        super().__init__(app)
        self._token = token
        self._protected_path = protected_path

    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith(self._protected_path):
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or not secrets.compare_digest(
            header.removeprefix("Bearer ").strip(), self._token
        ):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32001, "message": "Unauthorized"},
                    "id": None,
                },
                status_code=401,
            )

        return await call_next(request)


async def _healthz(_request: Request) -> JSONResponse:
    """Unauthenticated: checks the process is alive without exposing anything."""
    return JSONResponse({"status": "ok", "server": SERVER_NAME})


def create_app(config: Config, docker: DockerClient) -> Starlette:
    mcp = build_mcp(docker)

    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        # Left enabled, with the harness's own hostname named explicitly rather
        # than the protection switched off.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(config.allowed_hosts),
            allowed_origins=["*"],
        ),
    )
    app.router.routes.append(Route("/healthz", _healthz, methods=["GET"]))
    app.add_middleware(
        BearerAuthMiddleware, token=config.auth_token, protected_path="/mcp"
    )
    return app
