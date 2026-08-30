# Tool reference

The seven tools `sentinel-ops` exposes. Four read, three change things.

Every tool resolves its `service` argument through the managed label first. A
container without that label cannot be named, so every tool below is confined to
the same set.

## Read-only

Annotated `read_only_hint=True`. These run without asking anyone.

### `list_services`

No arguments.

Every managed service with its state, health, uptime and restart count. The
place to start: it shows what exists and what is unhealthy.

```json
[
  {
    "name": "checkout-api",
    "id": "083ff85235e4",
    "image": "postgres:16-alpine",
    "state": "running",
    "status": "running",
    "health": "none",
    "started_at": "2026-08-26T08:11:21.096Z",
    "restart_count": 0
  }
]
```

`restart_count` counts Docker's *automatic* restarts under a restart policy, not
manual ones. A restart through `restart_service` does not increment it — compare
`started_at` instead.

### `get_service_health`

| Argument | Type | Required |
|---|---|---|
| `service` | string | yes |

Detail for one service: state, exit code, whether it was killed for exceeding
its memory limit, and the last five health-check probes.

The fields that decide what to do next are `oomKilled` and `health.status`.

### `search_logs`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `service` | string | — | required |
| `contains` | string | none | case-insensitive substring filter |
| `tail` | integer | 200 | 1–1000, how many recent lines to scan |
| `since_seconds` | integer | none | only lines from the last N seconds |

Recent log lines, optionally filtered.

`contains` filters the lines that `tail` fetched — it does not search further
back. To search deeper, raise `tail` as well.

Docker multiplexes stdout and stderr into a framed stream with eight-byte
headers per chunk. Those are stripped before the model sees anything, so the
output is clean text.

### `get_service_stats`

| Argument | Type | Required |
|---|---|---|
| `service` | string | yes |

Current CPU and memory:

```json
{
  "name": "checkout-api",
  "cpu_percent": null,
  "memory_used_mb": 0.39,
  "memory_limit_mb": 256.0,
  "memory_percent": 0.15
}
```

`cpu_percent` is `null` on a container's first sample. CPU percentage needs two
readings and Docker reports an empty previous one, so this returns nothing
rather than a misleading zero. Memory is unaffected.

## Destructive

Annotated `destructive_hint=True`. The harness pauses on each of these and shows
a human the tool name, the arguments, and the reason before anything runs.

All three require a `reason` of at least ten characters. That text appears in
the approval dialog and in the audit log — it is written for the person
deciding, not for the record.

### `restart_service`

| Argument | Type | Required |
|---|---|---|
| `service` | string | yes |
| `reason` | string | yes, min 10 chars |

Restarts in place. Image and configuration unchanged. In-flight requests are
dropped.

Use for a service that has crashed, hung, or exhausted a pool of connections.

A restart destroys whatever was in memory, which is often the evidence. Read the
logs first.

### `raise_memory_limit`

| Argument | Type | Required | Range |
|---|---|---|---|
| `service` | string | yes | |
| `limit_mb` | integer | yes | 16–32768 |
| `reason` | string | yes | min 10 chars |

Raises the memory ceiling of a running service, applied without a restart.

Only justified when the evidence shows a memory ceiling was the problem —
`get_service_health` reporting `oomKilled`, or `get_service_stats` showing usage
sitting at the limit.

Swap is pinned to the same value. Left alone, Docker grants swap at twice the
limit, which converts an out-of-memory crash into silent thrashing that is
harder to diagnose than the crash was.

Raising the ceiling relieves a symptom. A genuine leak reaches the new limit too.

### `rollback_deploy`

| Argument | Type | Required |
|---|---|---|
| `service` | string | yes |
| `image` | string | yes |
| `reason` | string | yes, min 10 chars |

Rebuilds the service on a different image tag, keeping ports, mounts,
environment, resource limits and restart policy.

Use when an incident began immediately after a deploy and the logs point at new
code.

**This is the only unrecoverable operation here.** Docker cannot swap the image
of an existing container, so the container is destroyed and a new one built.
Anything written inside it that is not on a mounted volume is lost.

It is ordered to survive failure:

1. The replacement image must already exist locally. It is checked **before**
   anything is torn down — pulling mid-incident could stall for minutes, and a
   typo in a tag must not destroy a running service.
2. The managed label must be present on the rebuilt spec, or the replacement
   would fall outside the agent's reach and be unfixable by it.
3. If the rebuild fails anyway, it is retried once on the original image.
4. If that also fails, the error says plainly that the service is down and needs
   manual recovery.

Rolling back to the image already running is rejected rather than performed.

## Errors

Failures are returned with the protocol's `isError` flag rather than as ordinary
text, so the model treats them as failures instead of findings. The message is a
plain sentence it can act on:

```
No managed service named "jenkins". It either does not exist or is not
labelled as managed, which puts it outside this agent's reach.
```

```
Image "checkout-api:v99" is not present locally. Pull it first; this tool
will not fetch images while a service is being rebuilt.
```

## The audit log

Every destructive attempt is appended to `sentinel-audit.log` as one JSON object
per line, written from inside the code path that performs the work. Failures are
recorded before the error is raised, so a refused action still leaves a trace.

```json
{"at": "2026-08-26T08:46:54.330Z", "action": "restart_service",
 "service": "checkout-api", "outcome": "succeeded",
 "reason": "Connection pool exhausted error observed in logs; service appears hung without handling requests. Restart to reset connection pool."}
```

Audit writes never raise. Losing a log line must not turn a completed
remediation into a reported failure, which would invite the agent to retry
something that already happened.
