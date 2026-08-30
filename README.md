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

## Documentation

| | |
|---|---|
| [docs/setup.md](docs/setup.md) | From an empty machine to a working agent |
| [docs/architecture.md](docs/architecture.md) | How the pieces fit, and which one is the AI |
| [docs/tools.md](docs/tools.md) | All seven tools, their arguments and their risks |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Every failure hit while building this, and its fix |

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

## Running it

Full instructions, including the Daytona scopes and the Docker override the
harness needs, are in [docs/setup.md](docs/setup.md). In outline:

```bash
# once
python -m venv .venv && .venv/Scripts/activate && pip install -e .
cp .env.example .env                 # then fill in the keys

# the harness, in its own checkout of trueforge
docker compose up --build -d         # with HOST: 0.0.0.0 overridden

# this project, one terminal each
python -m sentinel_ops               # MCP server on :8931
python scripts/groq_shim.py          # model shim on :8932

# configure the agent; safe to re-run
python scripts/provision.py
```

Then open <http://localhost:8791>, start a session with the `sentinel` agent,
and give it something real:

> checkout-api is throwing errors. Investigate and fix it.

Only containers labelled `sentinel.managed=true` are visible to it:

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
