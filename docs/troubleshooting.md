# Troubleshooting

Every failure below was hit while building this, with the cause and the fix.
They are grouped by where the symptom appears.

## The harness will not start

### `Received protocol 'c:'`

```
Failed to start server: Only URLs with a scheme in: file, data, and node are
supported by the default ESM loader. On Windows, absolute paths must be valid
file:// URLs. Received protocol 'c:'
```

TrueForge 0.1.4 does not run natively on Windows. There is no fixed release to
upgrade to.

Run it in Docker instead — see [setup.md](setup.md#4-start-the-harness). The
container is Linux, so the path handling that fails on Windows never runs.

### `Local sandbox fallback is unavailable ... (got win32)`

The same cause. TrueForge's built-in local sandbox supports macOS and Linux
only. Running the harness in Docker sidesteps it, but note that the *configured*
sandbox provider is Daytona regardless — the local fallback is not exposed
through the API.

## The harness starts but answers nothing

### `curl http://localhost:8791/healthz` returns empty, not an error

The server binds `localhost` **inside** the container. Docker's port mapping
delivers traffic to the container's `eth0` address, so the connection is
accepted and then dropped — which looks like an empty reply rather than a
refusal.

Add a `docker-compose.override.yml` beside their `docker-compose.yml`:

```yaml
services:
  server:
    environment:
      HOST: 0.0.0.0
```

Then `docker compose up -d server`.

To confirm what it is bound to:

```bash
docker exec trueforge-server-1 sh -c "cat /proc/net/tcp6 | awk 'NR>1 {print \$2, \$4}'"
```

Port `2256` is 8790 in hex. An address of all zeros ending `01000000` is IPv6
loopback — that is the broken state.

### `Failed to start server: the database system is starting up`

Harmless. The server starts before Postgres finishes and retries every two
seconds. It is only a problem if it never stops.

## The harness cannot reach sentinel-ops

### `Invalid Host header` / SSE `421`

```
failed to connect (tried streamable-http, sse):
  "Streamable HTTP error: Error POSTing to endpoint: Invalid Host header"
  "SSE error: Non-200 status code (421)"
```

The MCP SDK rejects requests whose `Host` header it does not recognise. This is
DNS-rebinding protection, and it is worth keeping.

The harness arrives as `host.docker.internal`, which is allowed by default in
[`config.py`](../sentinel_ops/config.py). If you reach the server by some other
name, add it:

```bash
SENTINEL_ALLOWED_HOSTS=my-host,another-name
```

`localhost`, `127.0.0.1` and `host.docker.internal` are always allowed, each
with and without the port.

### Connection refused from inside the container

`sentinel-ops` is bound to loopback. Set `SENTINEL_HOST=0.0.0.0` in `.env` and
restart it. Confirm from inside the container:

```bash
docker exec trueforge-server-1 node -e "fetch('http://host.docker.internal:8931/healthz').then(r=>r.text()).then(console.log)"
```

### `Tool 'get_tool_info' is not allowed on MCP server sentinel-ops`

TrueForge's deferred tool loading calling meta-tools that our allowlist does not
include. Fixed by preloading schemas, which the provisioning script already
does (`"preload": True`). Only reappears if deferred loading is turned back on.

## Model errors

### `property 'reasoning_content' is unsupported`

```
Request failed (400): 'messages.2' : for 'role:assistant' the following must be
satisfied[('messages.2' : property 'reasoning_content' is unsupported)]
```

The harness replays the model's reasoning back to the provider and Groq rejects
it. Every Groq model that can call tools also reasons, so this breaks the second
step of every tool loop.

Run [`scripts/groq_shim.py`](../scripts/groq_shim.py) and point the provider at
it — `provision.py` does this by default. If you see this error, the shim is not
running, or `GROQ_BASE_URL` is set to Groq directly.

`reasoning_effort` does not help: it accepts only low/medium/high and still
returns reasoning. `reasoning_format: hidden` does suppress it, but the harness
does not forward that parameter.

### `Request too large ... Limit 8000, Requested 34317`

Groq counts the **reserved** output against the per-minute budget before
generating anything. Declaring a 32k output ceiling made a 1,500-token prompt
into a 34,000-token request.

`provision.py` sets `max_output_tokens` and `max_tokens` to 2048. If you raise
them, keep `input + max_tokens` under 8,000.

### `Rate limit reached ... Please try again in 10.68s`

The free tier allows 8,000 tokens per minute; one investigation costs three to
six times that across its steps. The harness reports a 429 as a failed turn
rather than retrying.

The shim absorbs this, waiting the interval Groq names and retrying up to four
times. Turns therefore *pause* rather than fail. If a turn still dies, raise
`MAX_RATE_LIMIT_RETRIES` in the shim.

There is no way to avoid the pauses on the free tier. Nothing is broken.

## Sandbox errors

### `This organization does not have a default region`

```
DaytonaError: This organization does not have a default region.
Please open the Daytona Dashboard to set a default region.
```

The key is fine; the account is not configured. Open the Daytona dashboard and
set a default shared region (US or EU). Check with:

```bash
curl https://app.daytona.io/api/regions -H "Authorization: Bearer $DAYTONA_API_KEY"
```

An empty `[]` means no region is set.

### `sandbox-providers/daytona -> HTTP 500`

Almost always the region problem above. Read the harness logs for the real
cause, which the 500 hides:

```bash
docker compose logs server --tail 40
```

## Agent behaviour

### It asks permission instead of acting

If the instructions tell the agent that destructive actions "need human
approval", it reads that as an instruction to ask, and offers a choice instead
of calling the tool. That looks safe but is worse: the real gate never fires,
and the safety becomes a matter of the model's manners.

The instructions now tell it to call the tool and that approval is enforced
around it. Keep that wording if you edit them.

### It invents parameter names

Symptoms: calls with `service_id` instead of `service`, or `query` instead of
`contains`.

Caused by deferred tool loading — without schemas the model guesses. Preloading
fixes it and costs less overall, because the guessing wasted several steps and
still failed.

### It cannot see a container that exists

Working as designed. Only containers labelled `sentinel.managed=true` are
visible; unlabelled ones cannot be resolved by name or id.

```bash
docker ps --filter "label=sentinel.managed=true"
```

To bring an existing container into scope it has to be recreated with the label
— Docker cannot add a label to a running container.

## Provisioning

### `UnicodeEncodeError: 'charmap' codec can't encode character`

The Windows console is cp1252 and API error messages contain characters it
cannot encode, so printing a failure crashed instead of reporting it. Fixed in
`provision.py`, which reconfigures stdout to UTF-8 at startup.

### `Unrecognized key: "name"`

Replacing an agent takes the manifest alone. The name is fixed at creation and
is rejected on update. Already handled.

## Working out what actually happened

Harness logs, which carry the real error behind an HTTP 500:

```bash
cd path/to/trueforge && docker compose logs server --tail 40
```

What the harness thinks our tools are, and whether it can connect at all:

```bash
curl http://localhost:8791/api/v1/mcp-servers/sentinel-ops/tools
```

What was actually done to your containers:

```bash
cat sentinel-audit.log
```

Whether a restart was real — compare `started_at` before and after:

```bash
docker inspect checkout-api --format '{{.State.StartedAt}}'
```
