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

Node.js >= 22.14 and a running Docker daemon.

## Running it

```bash
# Start the TrueForge harness (UI at http://localhost:8790)
npx @truefoundry/trueforge@latest
```

Setup for the MCP server and agent configuration is documented as it lands.

## Development

Work lands through pull requests. Review rules for this repo are in
[best_practices.md](best_practices.md).

## License

MIT. See [LICENSE](LICENSE).
