# ARMAMENT

An on-call incident response agent built on [TrueForge](https://trueforge.dev).

The agent watches real running services, investigates when something breaks, and
proposes a fix — but it stops and asks a human before it touches anything that
cannot be undone.

## What a run looks like

Given nothing but *"checkout-api is throwing errors, investigate and fix it"*,
the agent works through it on its own:

```
list_services        → checkout-api is unhealthy
get_service_health   → running, no OOM kill
search_logs          → "connection pool exhausted after 30s"
get_service_stats    → memory and CPU are fine, so not resource exhaustion
search_logs (×4)     → narrowed to the pool, not the database

restart_service      ⏸  PAUSED — waiting for a human
                        reason: "Connection pool exhausted error observed in
                        logs; service appears hung without handling requests.
                        Restart to reset connection pool."
```

Everything above the pause is automatic. The pause is the point.

## Design

**Real systems, not mocks.** The agent reaches live infrastructure through an
MCP server that fronts the Docker Engine API — real container state, real logs,
real health.

**Sandboxed execution.** Code the model writes never runs on the host. It
executes in an isolated Daytona sandbox, and secrets stay in the harness.

**Approval before anything irreversible.** Restart, memory changes and rollback
are annotated as destructive at the MCP layer, and the harness gates on those
annotations. The connector's honesty about its own tools is what arms the gate,
which is why a mislabelled tool is treated as a blocking review defect.

```
TrueForge harness  (Docker, :8791)
  agent "sentinel"  ── sandbox: Daytona, for code the model writes
                    ── approval required for @destructive tools
        │                                    │
        │ MCP, streamable HTTP               │ OpenAI-compatible
        ▼                                    ▼
  sentinel-ops  (:8931)                groq-shim  (:8932)
     read   → list_services, get_service_health,      │
               search_logs, get_service_stats         ▼
     write  → restart_service, raise_memory_limit,  Groq
               rollback_deploy
        │ Docker Engine API
        ▼
  services labelled sentinel.managed=true
```

### Blast radius

A container is invisible to the agent unless it carries
`sentinel.managed=true`. Not merely filtered from listings — unlabelled
containers cannot be resolved by name or by id, so no tool can reach one even
if the model asks for it directly. The boundary is a wall in the code rather
than a request in a prompt.

### Why there is a shim

TrueForge replays an assistant turn back to the model provider verbatim,
reasoning included; its source calls this intentional. Groq rejects that field,
so the second step of every tool loop failed. Nothing on either side configures
around it: `reasoning_effort` only accepts low/medium/high and still returns
reasoning, `reasoning_format` is not forwarded, and every Groq model that can
call tools also reasons.

[scripts/groq_shim.py](scripts/groq_shim.py) sits in between, strips that one
key, and passes everything else through. It also absorbs rate limits: the free
tier allows 8k tokens per minute and one investigation costs several times that
across its steps, so the shim waits the interval Groq names and retries rather
than letting the turn die mid-loop.

## Requirements

- Python 3.11 or newer
- A running Docker daemon
- A [Groq](https://console.groq.com) API key
- A [Daytona](https://app.daytona.io) API key, for the sandbox — optional, but
  without it the agent cannot run code it writes

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; use source .venv/bin/activate elsewhere
pip install -e .

cp .env.example .env            # then fill in the keys
```

The Daytona key needs the scopes `write:sandboxes`, `delete:sandboxes`,
`write:snapshots` and `delete:snapshots`, and the organisation needs a default
region set, or registering the provider fails.

### Start the harness

TrueForge does not run natively on Windows (its server fails to start on an ESM
path, and its local sandbox is macOS/Linux only), so it runs in Docker:

```bash
git clone https://github.com/truefoundry/trueforge && cd trueforge
cp packages/trueforge/.env.example packages/trueforge/.env
docker compose up --build -d
```

Add a `docker-compose.override.yml` alongside it, or the server binds loopback
inside the container and the published port answers with nothing:

```yaml
services:
  server:
    environment:
      HOST: 0.0.0.0
```

The harness is then on `http://localhost:8791`.

### Start this project

Two processes, each in its own terminal:

```bash
python -m sentinel_ops       # MCP server on :8931
python scripts/groq_shim.py  # model shim on :8932
```

`SENTINEL_HOST` must be `0.0.0.0` for a containerised harness to reach the MCP
server: inside the container, loopback is the container itself. That also
exposes the port to the local network, where the bearer token is the only
control in front of container restarts, so it is opt-in and warns when set.

### Configure the agent

```bash
python scripts/provision.py
```

This registers the model provider, the connector, the sandbox and the agent
through TrueForge's API. It is written as a script rather than documented as a
click-path so the configuration is reviewable and reproducible, and it is safe
to re-run.

Then open `http://localhost:8791` and start a session with the `sentinel` agent.

### Give it something to look at

Only labelled containers are visible:

```bash
docker run -d --label sentinel.managed=true --name checkout-api \
  --memory 256m postgres:16-alpine \
  sh -c 'echo "checkout-api listening on :8080"; echo "ERROR: connection pool exhausted after 30s"; sleep 7200'
```

## Audit log

Every state-changing attempt is appended to `sentinel-audit.log` as JSON, from
inside the code path that performs the work, so nothing acts unrecorded.
Failures are logged too:

```json
{"at": "2026-08-26T08:46:54Z", "action": "restart_service",
 "service": "checkout-api", "outcome": "succeeded",
 "reason": "Connection pool exhausted error observed in logs; service appears
            hung without handling requests. Restart to reset connection pool."}
```

## Development

Work lands through pull requests. Review rules for this repo are in
[best_practices.md](best_practices.md); the safety rules there are treated as
blocking.

## License

MIT. See [LICENSE](LICENSE).
