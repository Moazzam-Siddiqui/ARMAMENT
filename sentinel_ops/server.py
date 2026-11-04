"""HTTP surface for the MCP server and the read-only operator dashboard."""
from __future__ import annotations

import secrets

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from . import audit
from .config import Config
from .docker_client import DockerClient
from .tools.read import register_read_tools
from .tools.write import register_write_tools

SERVER_NAME = "sentinel-ops"
SERVER_VERSION = "0.2.0"

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARMAMENT | Operations</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif;background:#10141b;color:#e8edf5}
body{margin:0;padding:32px;max-width:1200px;margin-inline:auto}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:26px}p{color:#9aa8ba}
.panel{background:#19212c;border:1px solid #2b394b;border-radius:12px;padding:18px;margin:18px 0}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input{background:#0e141d;color:#fff;border:1px solid #40516a;border-radius:8px;padding:10px;min-width:280px}
button,a{background:#315b91;color:#fff;border:0;border-radius:8px;padding:10px 14px;text-decoration:none;cursor:pointer}
a{display:inline-block}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:10px;border-bottom:1px solid #2b394b;vertical-align:top}
th{color:#a9bad0;font-weight:600}
.muted{color:#9aa8ba}.status{font-weight:600}.ok{color:#69d3a0}.bad{color:#ff9d9d}
.table-wrap{overflow-x:auto}.section-title{display:flex;justify-content:space-between;align-items:center;gap:12px}
small{color:#9aa8ba}
@media(max-width:650px){body{padding:16px}.panel{padding:12px}input{min-width:0;width:100%}}
</style>
</head>
<body>
<header>
  <div><h1>ARMAMENT</h1><p>Docker operations overview · read-only dashboard</p></div>
  <a href="http://localhost:8791" target="_blank" rel="noopener">Open TrueForge agent ↗</a>
</header>
<section class="panel">
  <div class="controls">
    <label for="token">MCP bearer token</label>
    <input id="token" type="password" autocomplete="current-password" placeholder="Paste token from your private .env">
    <button id="connect">Connect & refresh</button>
    <button id="refresh">Refresh</button>
  </div>
  <p id="message" role="status" aria-live="polite">Enter your MCP token to load protected operational data.</p>
  <small>The token is kept in this browser tab's session storage. This page does not expose remediation controls; approvals remain in TrueForge.</small>
</section>
<section class="panel">
  <div class="section-title"><h2>Managed services</h2><small id="service-count">Not connected</small></div>
  <div class="table-wrap"><table><thead><tr><th>Service</th><th>State</th><th>Health</th><th>Image</th><th>Restarts</th><th>Started</th></tr></thead><tbody id="services"><tr><td colspan="6" class="muted">Connect to load services.</td></tr></tbody></table></div>
</section>
<section class="panel">
  <div class="section-title"><h2>Recent remediation audit</h2><small>Latest 100 events</small></div>
  <div class="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Service</th><th>Outcome</th><th>Reason / detail</th></tr></thead><tbody id="audit"><tr><td colspan="5" class="muted">Connect to load audit events.</td></tr></tbody></table></div>
</section>
<script>
(function(){
  const tokenInput=document.getElementById('token');
  const message=document.getElementById('message');
  const servicesBody=document.getElementById('services');
  const auditBody=document.getElementById('audit');
  const serviceCount=document.getElementById('service-count');
  tokenInput.value=sessionStorage.getItem('armament-token')||'';
  function cell(row,value){
    const td=document.createElement('td');
    td.textContent=(value===null||value===undefined||value==='')?'—':String(value);
    row.appendChild(td);
    return td;
  }
  function emptyRow(target,span,label){
    target.replaceChildren();
    const row=document.createElement('tr');
    const td=document.createElement('td');
    td.colSpan=span;td.className='muted';td.textContent=label;
    row.appendChild(td);target.appendChild(row);
  }
  async function get(path,token){
    const response=await fetch(path,{headers:{Authorization:'Bearer '+token}});
    if(response.status===401)throw new Error('Token rejected. Check SENTINEL_AUTH_TOKEN in your private .env.');
    if(!response.ok)throw new Error('Request failed ('+response.status+').');
    return response.json();
  }
  async function refresh(){
    const token=tokenInput.value.trim();
    if(!token){message.textContent='Enter the MCP bearer token first.';return;}
    sessionStorage.setItem('armament-token',token);
    message.textContent='Loading operational data…';
    try{
      const results=await Promise.all([get('/api/services',token),get('/api/audit?limit=100',token)]);
      servicesBody.replaceChildren();
      results[0].forEach(function(service){
        const row=document.createElement('tr');
        cell(row,service.name);cell(row,service.state);cell(row,service.health);
        cell(row,service.image);cell(row,service.restart_count);cell(row,service.started_at);
        servicesBody.appendChild(row);
      });
      if(!results[0].length)emptyRow(servicesBody,6,'No managed containers found. Add the configured management label to a test container.');
      serviceCount.textContent=results[0].length+' service(s)';
      auditBody.replaceChildren();
      results[1].forEach(function(event){
        const row=document.createElement('tr');
        cell(row,event.at);cell(row,event.action);cell(row,event.service);
        const outcome=cell(row,event.outcome);
        outcome.className='status '+(event.outcome==='succeeded'?'ok':'bad');
        cell(row,event.reason||event.error||JSON.stringify(event.detail||{}));
        auditBody.appendChild(row);
      });
      if(!results[1].length)emptyRow(auditBody,5,'No remediation events recorded yet.');
      message.textContent='Connected. Data refreshed successfully.';
      message.className='ok';
    }catch(error){
      message.textContent=error.message;
      message.className='bad';
    }
  }
  document.getElementById('connect').addEventListener('click',refresh);
  document.getElementById('refresh').addEventListener('click',refresh);
})();
</script>
</body>
</html>"""


def build_mcp(docker: DockerClient) -> MCPServer:
    mcp = MCPServer(name=SERVER_NAME, version=SERVER_VERSION)
    register_read_tools(mcp, docker)
    register_write_tools(mcp, docker)
    return mcp


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Protects MCP and operator API routes with constant-time bearer checks."""

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        protected_path: str | tuple[str, ...],
    ) -> None:
        super().__init__(app)
        self._token = token
        self._protected_paths = (
            (protected_path,) if isinstance(protected_path, str) else protected_path
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if not any(
            request.url.path.startswith(path) for path in self._protected_paths
        ):
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
    """Unauthenticated liveness check with no operational details."""
    return JSONResponse({"status": "ok", "server": SERVER_NAME})


async def _dashboard(_request: Request) -> HTMLResponse:
    return HTMLResponse(
        DASHBOARD_HTML,
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )


def create_app(config: Config, docker: DockerClient) -> Starlette:
    mcp = build_mcp(docker)

    app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(config.allowed_hosts),
            allowed_origins=["*"],
        ),
    )

    async def services(_request: Request) -> JSONResponse:
        try:
            return JSONResponse(await docker.list_services())
        except Exception:  # noqa: BLE001 - keep Docker internals out of API responses
            return JSONResponse(
                {"error": "Unable to query Docker. Check daemon connectivity and logs."},
                status_code=503,
            )

    async def audit_events(request: Request) -> JSONResponse:
        try:
            limit = int(request.query_params.get("limit", "100"))
            return JSONResponse(audit.recent_events(limit))
        except (ValueError, OSError):
            return JSONResponse({"error": "Invalid audit query."}, status_code=400)

    app.router.routes.extend(
        [
            Route("/", _dashboard, methods=["GET"]),
            Route("/dashboard", _dashboard, methods=["GET"]),
            Route("/healthz", _healthz, methods=["GET"]),
            Route("/api/services", services, methods=["GET"]),
            Route("/api/audit", audit_events, methods=["GET"]),
        ]
    )
    app.add_middleware(
        BearerAuthMiddleware,
        token=config.auth_token,
        protected_path=("/mcp", "/api"),
    )
    return app
