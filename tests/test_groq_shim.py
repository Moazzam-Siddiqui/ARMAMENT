import json

import httpx
from starlette.testclient import TestClient

from scripts.groq_shim import app, retry_delay, sanitise


def test_sanitise_removes_reasoning_metadata_from_assistant_messages() -> None:
    payload = {
        "messages": [
            {
                "role": "assistant",
                "reasoning_content": "private",
                "thinking_blocks": [],
            },
            {"role": "user", "reasoning": "keep"},
        ]
    }
    body, removed = sanitise(json.dumps(payload).encode())
    cleaned = json.loads(body)
    assert removed == 2
    assert "reasoning_content" not in cleaned["messages"][0]
    assert "thinking_blocks" not in cleaned["messages"][0]
    assert cleaned["messages"][1]["reasoning"] == "keep"


def test_sanitise_passes_through_invalid_or_unrelated_payloads() -> None:
    raw = b"not-json"
    assert sanitise(raw) == (raw, 0)
    payload = b'{"messages":[{"role":"user","content":"hello"}]}'
    assert sanitise(payload) == (payload, 0)


def test_retry_delay_prefers_retry_after_header() -> None:
    response = httpx.Response(429, headers={"Retry-After": "7"})
    assert retry_delay(response, "try again in 2s") == 7


def test_retry_delay_reads_groq_error_message() -> None:
    response = httpx.Response(429)
    assert retry_delay(response, "Please try again in 10.5s") == 11.5


def test_shim_requires_provider_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_SHIM_AUTH_TOKEN", "provider-secret")
    response = TestClient(app).post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 401


def test_shim_rejects_oversized_request_before_upstream(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_SHIM_AUTH_TOKEN", "provider-secret")
    monkeypatch.setattr("scripts.groq_shim.MAX_REQUEST_BYTES", 8)
    response = TestClient(app).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer provider-secret"},
        content=b"0123456789",
    )
    assert response.status_code == 413
