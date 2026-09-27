from __future__ import annotations

import httpx
from firefox_bridge.client import FirefoxBridgeClient


def test_client_health_uses_configured_transport() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    bridge_client = FirefoxBridgeClient(
        base_url="http://bridge.test",
        token="secret",
        client=http_client,
    )
    try:
        assert bridge_client.health() == {"status": "ok"}
    finally:
        bridge_client.close_client()

    assert requests[0].url == "http://bridge.test/health"
    assert "Authorization" not in requests[0].headers


def test_client_sends_bearer_token_and_json() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "tab-1"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    bridge_client = FirefoxBridgeClient(
        base_url="http://bridge.test",
        token="secret",
        client=http_client,
    )
    try:
        assert bridge_client.open_tab("https://example.test") == {"id": "tab-1"}
    finally:
        bridge_client.close_client()

    assert requests[0].headers["authorization"] == "Bearer secret"
    assert requests[0].content == b'{"url":"https://example.test"}'
