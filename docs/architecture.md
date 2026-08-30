# Architecture

How the pieces fit together, and which of them is doing the thinking.

## The short version

Four processes run at once. Only one of them is code in this repository.

| Process | Port | Whose code | What it does |
|---|---|---|---|
| TrueForge harness | 8791 | TrueFoundry's, in Docker | Runs the agent loop |
| sentinel-ops | 8931 | **this repo** | Offers seven tools over MCP |
| groq-shim | 8932 | **this repo** | Makes the harness and Groq compatible |
| Your services | — | — | Containers the agent looks after |

The model itself runs at Groq, on their hardware. Nothing in this repository
contains a model, and nothing here is "the AI".

## Where the agent actually is

The word *agent* describes an arrangement, not a file. It is three things that
only become an agent together:

1. **A model** — `openai/gpt-oss-120b`, running at Groq. It produces text. It
   cannot touch anything.
2. **A description** — which model to use, what the instructions are, which
   tools are allowed, which need approval. This lives in
   [`scripts/provision.py`](../scripts/provision.py), in `INSTRUCTIONS` and the
   manifest below it.
3. **A loop** — TrueForge reads the description, sends the instructions and the
   tool list to the model, notices when the model asks for a tool, runs it,
   feeds the result back, and repeats.

So the agent is assembled at run time out of your description, Groq's model, and
TrueForge's loop. The description is the only part written here.

## Where MCP is

MCP — Model Context Protocol — is a standard shape for describing tools so any
AI system can use them without custom glue. `sentinel_ops/` is an MCP server: an
ordinary web service that answers two kinds of question.

- *What tools do you have?* — names, parameters, descriptions, and whether each
  one is destructive.
- *Please run this tool with these arguments.* — it runs it and returns the
  result.

There is no intelligence in it. `search_logs` receives a service name and a word,
asks Docker for that container's recent output, keeps the matching lines, and
returns them. It has no idea why anyone wanted them.

## The full path of one tool call

When the agent decides to read some logs:

```
model at Groq
  "I want search_logs(service='checkout-api', contains='ERROR')"
        │
        ▼
TrueForge  — sees a tool call, checks whether it needs approval
        │    (read-only, so no)
        ▼
groq-shim is not involved here; this direction goes straight to the connector
        │
        ▼
sentinel-ops  — checks the bearer token, checks the Host header,
        │        resolves 'checkout-api' within the managed label
        ▼
Docker Engine API  — returns the raw log stream
        │
        ▼
sentinel-ops  — strips Docker's frame headers, filters for ERROR
        │
        ▼
TrueForge  — appends the result to the conversation
        │
        ▼
model at Groq  — reads it and decides what to do next
```

The model never reaches Docker. It can only ask TrueForge, and TrueForge is
where the approval gate lives. That is what makes the gate meaningful rather
than advisory: there is no other path.

## The approval gate

Two lines, in two files, are the whole safety mechanism.

In [`sentinel_ops/tools/write.py`](../sentinel_ops/tools/write.py), the
dangerous tools declare themselves:

```python
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    ...
)
```

In [`scripts/provision.py`](../scripts/provision.py), the agent is told to stop
on anything wearing that label:

```python
"require_approval_for_tools": ["@destructive"]
```

`@destructive` is not a list of tool names. It resolves against the annotations
the connector reports about itself. No approval code is written anywhere in this
project — the connector's honesty about its own tools is the mechanism.

The consequence is worth stating plainly: a tool that mutates something while
claiming `read_only_hint=True` would run unattended, with no error and no
warning. That is why [`best_practices.md`](../best_practices.md) treats a
mislabelled annotation as a blocking review defect rather than a style note.

## The blast radius

A container is invisible to the agent unless it carries the managed label,
`sentinel.managed=true` by default.

"Invisible" is literal. `DockerClient.resolve()` in
[`sentinel_ops/docker_client.py`](../sentinel_ops/docker_client.py) lists only
labelled containers and matches within that set, so an unlabelled container
cannot be reached by name, by id, or by prefix. Every tool — including all three
destructive ones — goes through that one function.

This is deliberate. The alternative is a line in the prompt asking the model not
to touch production, which is a request rather than a boundary.

## Why the shim exists

TrueForge replays an assistant turn back to the provider verbatim, including the
model's reasoning. Its own source comments on this:

```
// (thinking_blocks, reasoning_content, …); we intentionally forward them for replay.
```

Groq rejects that field:

```
'messages.2' : for 'role:assistant' the following must be satisfied
[('messages.2' : property 'reasoning_content' is unsupported)]
```

Every Groq model that supports tool calling also reasons, so the second step of
every tool loop failed. Neither side can be configured around it:
`reasoning_effort` accepts only low/medium/high and still returns reasoning,
`reasoning_format` is not forwarded by the harness, and the replay is deliberate
rather than a setting.

The provider's base URL *is* configurable, so
[`scripts/groq_shim.py`](../scripts/groq_shim.py) sits between them, strips that
one key on the way out, and passes everything else through untouched. The
harness keeps its own record of the reasoning; only the copy sent back to Groq
is trimmed.

It also absorbs rate limits. The free Groq tier allows 8,000 tokens per minute
and one investigation costs several times that across its steps. The harness
reports a 429 as a failed turn rather than retrying, so the shim waits the
interval Groq names and retries instead.

## The sandbox

When the model wants to compute something rather than just read it, it writes
code — and that code never runs on your machine. TrueForge provisions a Daytona
sandbox and runs it there.

Secrets stay in the harness and are never passed into the sandbox, so code the
model writes has nothing valuable to reach even in principle.

The sandbox is separate from the seven MCP tools. Those reach your real Docker
containers; the sandbox is a scratch space for analysis.

## File map

| Path | What it is |
|---|---|
| [`sentinel_ops/tools/read.py`](../sentinel_ops/tools/read.py) | The four read-only tools |
| [`sentinel_ops/tools/write.py`](../sentinel_ops/tools/write.py) | The three destructive tools |
| [`sentinel_ops/docker_client.py`](../sentinel_ops/docker_client.py) | Talks to Docker; holds the label boundary |
| [`sentinel_ops/server.py`](../sentinel_ops/server.py) | HTTP surface, bearer auth, Host allowlist |
| [`sentinel_ops/config.py`](../sentinel_ops/config.py) | Settings, validated at startup |
| [`sentinel_ops/audit.py`](../sentinel_ops/audit.py) | Append-only record of state changes |
| [`scripts/provision.py`](../scripts/provision.py) | Creates the agent, connector, model and sandbox |
| [`scripts/groq_shim.py`](../scripts/groq_shim.py) | Compatibility and rate-limit handling for Groq |
