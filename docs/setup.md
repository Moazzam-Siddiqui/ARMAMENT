# Setup

From an empty machine to a working agent. Every step here has been run on
Windows 11 with Docker Desktop; the differences elsewhere are noted.

## What you need first

- **Python 3.11 or newer**
- **Docker**, running
- **A Groq API key** — free, from [console.groq.com](https://console.groq.com)
- **A Daytona API key** — optional, for the sandbox; free tier at
  [app.daytona.io](https://app.daytona.io)

## 1. Install this project

```bash
git clone https://github.com/Moazzam-Siddiqui/ARMAMENT
cd ARMAMENT

python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e .
```

## 2. Fill in the settings

```bash
cp .env.example .env
```

Open `.env` and set:

| Setting | Value |
|---|---|
| `SENTINEL_AUTH_TOKEN` | Any long random string. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `SENTINEL_HOST` | `0.0.0.0` — required, see below |
| `GROQ_API_KEY` | From the Groq console |
| `DAYTONA_API_KEY` | From Daytona, optional |

`.env` is gitignored. Nothing in it is ever committed.

**Why `SENTINEL_HOST=0.0.0.0`.** The harness runs inside a container, where
`localhost` means the container itself, so a server bound to loopback is
unreachable. Binding to `0.0.0.0` also exposes the port to your local network,
where the bearer token is the only thing standing in front of container
restarts. That is why it is opt-in and logs a warning rather than being the
default.

## 3. Set up Daytona (optional, for the sandbox)

Skip this and the agent still investigates and remediates — it just cannot write
and run its own analysis code.

1. Sign up at [app.daytona.io](https://app.daytona.io).
2. Go to [dashboard/keys](https://app.daytona.io/dashboard/keys) → **Create Key**.
3. Tick exactly these scopes and nothing else:
   - `write:sandboxes`
   - `delete:sandboxes`
   - `write:snapshots`
   - `delete:snapshots`
4. Copy the key into `DAYTONA_API_KEY` in `.env`.
5. **Set a default region** in the Daytona dashboard. Without one, registering
   the provider fails with *"This organization does not have a default region."*

## 4. Start the harness

TrueForge does not run natively on Windows. Its server fails at startup on an
ESM path (`Received protocol 'c:'`) and its local sandbox is macOS/Linux only.
Running it in Docker avoids both.

```bash
git clone https://github.com/truefoundry/trueforge
cd trueforge
cp packages/trueforge/.env.example packages/trueforge/.env
```

Before starting it, add a `docker-compose.override.yml` next to
`docker-compose.yml`:

```yaml
# The server binds localhost by default, which Docker's port mapping cannot
# reach: traffic arrives on the container's eth0 address, not loopback. Their
# .env.example notes production images set this; Dockerfile.dev does not.
services:
  server:
    environment:
      HOST: 0.0.0.0
```

Then:

```bash
docker compose up --build -d
```

The first build takes several minutes. When it finishes:

```bash
curl http://localhost:8791/healthz
# {"status":"ok","version":"0.1.4"}
```

If that returns nothing at all rather than an error, the `HOST` override is
missing.

Keep this checkout **outside** the ARMAMENT repository.

## 5. Start this project's two processes

Each needs its own terminal, and both stay running.

```bash
python -m sentinel_ops
```

```
[sentinel-ops] listening on http://0.0.0.0:8931/mcp
[sentinel-ops] managing containers labelled sentinel.managed=true
[sentinel-ops] bound to 0.0.0.0: this port is reachable from the network ...
```

```bash
python scripts/groq_shim.py
```

```
[groq-shim] listening on http://0.0.0.0:8932/v1  ->  https://api.groq.com/openai/v1
```

## 6. Configure the agent

```bash
python scripts/provision.py
```

```
harness      http://localhost:8791
sentinel-ops http://host.docker.internal:8931/mcp
groq via     http://host.docker.internal:8932/v1

model provider
  ok    model-providers/groq
connector
  ok    mcp-servers/sentinel-ops
sandbox
  ok    sandbox-providers/daytona
agent
  ok    agents/sentinel (created)
```

This registers everything through TrueForge's REST API. It is safe to re-run:
each resource is created or replaced. Re-run it after changing the instructions,
the model, or any key.

Without a Daytona key you will see `skip  no DAYTONA_API_KEY set` instead, and
the agent is configured without a sandbox rather than with a broken one.

## 7. Give the agent something to look after

Only containers carrying the managed label are visible. Anything else on your
machine is invisible and unreachable.

```bash
docker run -d --label sentinel.managed=true --name checkout-api \
  --memory 256m postgres:16-alpine \
  sh -c 'echo "checkout-api listening on :8080"; \
         echo "INFO: db pool size=20"; \
         echo "ERROR: connection pool exhausted after 30s"; \
         sleep 7200'
```

## 8. Use it

Open <http://localhost:8791>, start a session with the **sentinel** agent, and
ask it something real:

> checkout-api is throwing errors. Investigate and fix it.

It will investigate on its own, then stop and ask before restarting anything.

## Checking everything is up

```bash
curl http://localhost:8791/healthz    # harness
curl http://localhost:8931/healthz    # sentinel-ops
curl http://localhost:8932/healthz    # groq-shim
docker ps --filter "label=sentinel.managed=true"
```

## Starting again later

Docker containers restart with Docker Desktop, but the two Python processes do
not. After a reboot:

```bash
# terminal 1
python -m sentinel_ops
# terminal 2
python scripts/groq_shim.py
```

The harness and its configuration survive restarts — Postgres keeps them — so
`provision.py` only needs re-running when something changes.
