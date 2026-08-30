"""A compatibility shim that lets the harness talk to Groq.

TrueForge replays an assistant turn back to the provider verbatim, including
the `reasoning_content` its own comment calls out as intentional:

    // (thinking_blocks, reasoning_content, ...); we intentionally forward them
    // for replay.

Groq rejects that field outright:

    'messages.2' : for 'role:assistant' the following must be satisfied
    [('messages.2' : property 'reasoning_content' is unsupported)]

Every Groq chat model that can call tools also reasons, so the second step of
any tool loop fails. Nothing on either side can be configured around it:
`reasoning_effort` only accepts low/medium/high and still returns reasoning,
`reasoning_format` is not forwarded by the harness, and the replay is
deliberate rather than a setting.

The provider's base URL is configurable though, so this sits in between and
strips the offending keys from outbound requests. It changes nothing else: the
harness keeps its own record of the model's reasoning, and only the copy sent
back to Groq is trimmed.

    python scripts/groq_shim.py

Then point the model provider at this process instead of Groq directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys

import httpx
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

UPSTREAM = "https://api.groq.com/openai/v1"

# Reasoning carried on an assistant message. Groq accepts none of these back.
STRIPPED_MESSAGE_KEYS = ("reasoning_content", "reasoning", "thinking_blocks")

# The free tier allows 8k tokens per minute, and a single investigation runs
# several model calls in well under that. The harness surfaces a 429 as a failed
# turn rather than retrying, so the wait is absorbed here instead.
MAX_RATE_LIMIT_RETRIES = 4
FALLBACK_RETRY_SECONDS = 12.0
MAX_RETRY_SECONDS = 60.0

# Hop-by-hop headers, plus ones that describe a body we may have rewritten.
SKIP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
SKIP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}

log = logging.getLogger("groq-shim")


def sanitise(body: bytes) -> tuple[bytes, int]:
    """Remove reasoning keys from any assistant message in the request body.

    Returns the body to send and how many keys were removed. A body that is not
    JSON, or has no messages, is passed through untouched rather than rejected:
    this shim should never be the reason a request fails.
    """
    if not body:
        return body, 0

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body, 0

    if not isinstance(payload, dict):
        return body, 0

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return body, 0

    removed = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        for key in STRIPPED_MESSAGE_KEYS:
            if key in message:
                del message[key]
                removed += 1

    if removed == 0:
        return body, 0
    return json.dumps(payload).encode(), removed


def retry_delay(response: httpx.Response, body: str) -> float:
    """How long to wait before retrying a rate-limited request.

    Prefers the Retry-After header, falls back to the "try again in 10.68s"
    Groq puts in the error message, and finally to a fixed pause.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), MAX_RETRY_SECONDS)
        except ValueError:
            pass

    match = re.search(r"try again in ([0-9.]+)s", body)
    if match:
        try:
            # A small margin: the reported figure is the moment the window
            # reopens, and arriving exactly then tends to be rejected again.
            return min(float(match.group(1)) + 1.0, MAX_RETRY_SECONDS)
        except ValueError:
            pass

    return FALLBACK_RETRY_SECONDS


async def proxy(request: Request) -> Response:
    path = request.path_params["path"]
    url = f"{UPSTREAM}/{path}"

    body, removed = sanitise(await request.body())
    if removed:
        log.info("stripped %d reasoning key(s) from %s", removed, path)

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in SKIP_REQUEST_HEADERS
    }

    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))

    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        upstream_request = client.build_request(
            request.method, url, content=body, headers=headers, params=request.query_params
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            log.error("upstream request failed: %s", exc)
            return JSONResponse(
                {"error": {"message": f"Cannot reach Groq: {exc}", "type": "upstream_error"}},
                status_code=502,
            )

        if upstream.status_code != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
            break

        # Read and close before waiting, so the connection is not held open
        # across the pause. The read can itself fail if Groq drops the
        # connection while returning the 429, which would otherwise escape as an
        # unhandled 500 and leak the client.
        try:
            error_body = (await upstream.aread()).decode(errors="replace")
        except httpx.HTTPError as exc:
            await upstream.aclose()
            await client.aclose()
            log.error("reading rate-limit response failed: %s", exc)
            return JSONResponse(
                {"error": {"message": f"Cannot reach Groq: {exc}", "type": "upstream_error"}},
                status_code=502,
            )
        await upstream.aclose()
        delay = retry_delay(upstream, error_body)
        log.warning(
            "rate limited, waiting %.1fs then retrying (attempt %d/%d)",
            delay,
            attempt + 1,
            MAX_RATE_LIMIT_RETRIES,
        )
        await asyncio.sleep(delay)

    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in SKIP_RESPONSE_HEADERS
    }

    async def stream():
        # The client outlives this handler because the body is streamed, so both
        # it and the response are closed here rather than by the caller.
        # Decoded rather than raw: the content-encoding header is dropped below,
        # so forwarding still-compressed bytes would leave the caller unable to
        # read them.
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "groq-shim", "upstream": UPSTREAM})


app = Starlette(
    routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route("/v1/{path:path}", proxy, methods=["GET", "POST", "DELETE"]),
    ]
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s", stream=sys.stdout)
    load_dotenv(override=False)

    port = int(os.environ.get("GROQ_SHIM_PORT") or 8932)
    # 0.0.0.0 for the same reason sentinel-ops needs it: a containerised harness
    # cannot reach the host on loopback. No credential lives here -- the
    # harness sends its own Authorization header straight through.
    host = os.environ.get("GROQ_SHIM_HOST") or "0.0.0.0"

    log.info("listening on http://%s:%d/v1  ->  %s", host, port, UPSTREAM)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
