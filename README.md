# Agent Harness Using TrueForge

An on-call incident response agent built on [TrueForge](https://trueforge.dev).

The agent watches real running services, investigates when something breaks, and
proposes a fix — but it stops and asks a human before it touches anything that
cannot be undone.

## Status

Early development.

## Design

Three properties the agent is built around:

**Real systems, not mocks.** The agent reaches live infrastructure through an
MCP server that fronts the Docker Engine API — real container state, real logs,
real health.

**Sandboxed execution.** Diagnostic code the model writes never runs on the
host. It executes in an isolated sandbox, and secrets stay in the harness.

**Approval before anything irreversible.** Restart, rollback, and scale are
annotated as destructive at the MCP layer, so the harness gates them on human
approval automatically. The gate fails closed: no approval, no action.

```
TrueForge harness
  agent "sentinel"  ── skills: incident-triage
                    ── sandbox: isolated diagnostic execution
                    ── approval required for destructive tools
        │ MCP over streamable HTTP
        ▼
  sentinel-ops MCP server
     read   → list_services, get_service_health, search_logs, get_stats
     write  → restart_service, rollback_deploy, scale_service
        │ Docker Engine API
        ▼
  running services
```

## Requirements

Python 3.11 or newer, and a running Docker daemon.

## Running it

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; use source .venv/bin/activate elsewhere
pip install -e .

cp .env.example .env            # then set SENTINEL_AUTH_TOKEN
python -m sentinel_ops
```

The MCP endpoint is served at `http://localhost:8931/mcp` and requires the
bearer token from `.env` on every request. `GET /healthz` is open and reports
only whether the process is alive.

Only containers labelled `sentinel.managed=true` are visible to the agent.
Anything else cannot be listed, inspected, or acted on:

```bash
docker run -d --label sentinel.managed=true --name checkout-api ...
```

Start the TrueForge harness separately, then register this server under
Settings -> Connectors with header auth:

```bash
npx @truefoundry/trueforge@latest   # UI at http://localhost:8790
```

## Development

Work lands through pull requests. Review rules for this repo are in
[best_practices.md](best_practices.md).

## License

MIT. See [LICENSE](LICENSE).
