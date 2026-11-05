from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sentinel_ops.server import BearerAuthMiddleware


async def protected(_request):
    return PlainTextResponse("protected")


async def health(_request):
    return PlainTextResponse("healthy")


def make_client() -> TestClient:
    app = Starlette(
        routes=[
            Route("/mcp", protected),
            Route("/api/services", protected),
            Route("/healthz", health),
        ]
    )
    app.add_middleware(
        BearerAuthMiddleware,
        token="correct-token",
        protected_path=("/mcp", "/api"),
    )
    return TestClient(app)


def test_mcp_rejects_missing_and_invalid_tokens() -> None:
    client = make_client()
    assert client.get("/mcp").status_code == 401
    assert (
        client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )


def test_mcp_accepts_valid_token() -> None:
    response = make_client().get(
        "/mcp", headers={"Authorization": "Bearer correct-token"}
    )
    assert response.status_code == 200
    assert response.text == "protected"


def test_operator_api_requires_the_same_bearer_token() -> None:
    client = make_client()
    assert client.get("/api/services").status_code == 401
    response = client.get(
        "/api/services", headers={"Authorization": "Bearer correct-token"}
    )
    assert response.status_code == 200


def test_health_endpoint_is_unprotected() -> None:
    response = make_client().get("/healthz")
    assert response.status_code == 200
    assert response.text == "healthy"
