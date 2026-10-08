from __future__ import annotations

import httpx
import pytest
from firefox_bridge.client import (
    FirefoxBridgeAuthError,
    FirefoxBridgeClient,
    FirefoxBridgeConnectionError,
    FirefoxBridgeExtensionError,
    FirefoxBridgeExtensionUnavailableError,
    FirefoxBridgeHTTPError,
    FirefoxBridgeTimeoutError,
)


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


def _mock_client(handler, **kwargs):
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return FirefoxBridgeClient(
        base_url="http://bridge.test",
        token="secret",
        client=http_client,
        max_retries=kwargs.pop("max_retries", 0),
        retry_backoff=kwargs.pop("retry_backoff", 0),
        **kwargs,
    )


def test_client_maps_401_to_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid or missing authentication token"})

    bridge_client = _mock_client(handler)
    try:
        with pytest.raises(FirefoxBridgeAuthError) as exc_info:
            bridge_client.tabs()
        assert exc_info.value.status_code == 401
        # stays catchable as the generic HTTP error for old callers
        assert isinstance(exc_info.value, FirefoxBridgeHTTPError)
    finally:
        bridge_client.close_client()


def test_client_maps_503_and_504_to_typed_errors() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "Firefox extension is not connected"})

    def timed_out(request: httpx.Request) -> httpx.Response:
        return httpx.Response(504, json={"detail": "Firefox extension command timed out"})

    for handler, error_type, status in (
        (unavailable, FirefoxBridgeExtensionUnavailableError, 503),
        (timed_out, FirefoxBridgeTimeoutError, 504),
    ):
        bridge_client = _mock_client(handler)
        try:
            with pytest.raises(error_type) as exc_info:
                bridge_client.tabs()
            assert exc_info.value.status_code == status
        finally:
            bridge_client.close_client()


def test_client_502_carries_server_error_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={"detail": "INVALID_PARAMS: ref is required", "error": {"code": "INVALID_PARAMS"}},
        )

    bridge_client = _mock_client(handler)
    try:
        with pytest.raises(FirefoxBridgeExtensionError) as exc_info:
            bridge_client.click("tab-1", "x")
        assert exc_info.value.server_error == {"code": "INVALID_PARAMS"}
    finally:
        bridge_client.close_client()


def test_client_retries_transient_errors_then_succeeds() -> None:
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) < 3:
            return httpx.Response(502, json={"detail": "boom"})
        return httpx.Response(200, json={"id": "tab-9"})

    bridge_client = _mock_client(handler, max_retries=3)
    try:
        assert bridge_client.open_tab("https://example.test") == {"id": "tab-9"}
    finally:
        bridge_client.close_client()
    assert len(attempts) == 3


def test_client_does_not_retry_auth_errors() -> None:
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(401, json={"detail": "nope"})

    bridge_client = _mock_client(handler, max_retries=5)
    try:
        with pytest.raises(FirefoxBridgeAuthError):
            bridge_client.tabs()
    finally:
        bridge_client.close_client()
    assert len(attempts) == 1


def test_client_wraps_transport_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    bridge_client = _mock_client(handler)
    try:
        with pytest.raises(FirefoxBridgeTimeoutError):
            bridge_client.tabs()
    finally:
        bridge_client.close_client()


def test_client_wraps_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    bridge_client = _mock_client(handler)
    try:
        with pytest.raises(FirefoxBridgeConnectionError):
            bridge_client.tabs()
    finally:
        bridge_client.close_client()


def test_client_construction_survives_bracketed_ipv6_no_proxy(monkeypatch) -> None:
    # Regression: httpx crashed at construction when no_proxy held "[::1]".
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,[::1]")
    monkeypatch.setenv("NO_PROXY", "[::1]")
    bridge_client = FirefoxBridgeClient(base_url="http://bridge.test", token="t")
    try:
        assert bridge_client.base_url == "http://bridge.test"
    finally:
        bridge_client.close_client()
    # the process environment must be left untouched
    assert "[::1]" in __import__("os").environ["no_proxy"]


def test_wait_for_text_polls_until_match() -> None:
    bodies = [
        httpx.Response(200, json={"text": "loading..."}),
        httpx.Response(200, json={"text": "still loading..."}),
        httpx.Response(200, json={"text": "Laporan Keuangan Q1 2025"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return bodies.pop(0)

    bridge_client = _mock_client(handler)
    try:
        found = bridge_client.wait_for_text(
            "tab-1", "laporan keuangan", timeout=5, poll_interval=0.001
        )
        assert "Laporan Keuangan" in found
    finally:
        bridge_client.close_client()


def test_wait_for_text_times_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "nothing relevant"})

    bridge_client = _mock_client(handler)
    try:
        with pytest.raises(FirefoxBridgeTimeoutError):
            bridge_client.wait_for_text(
                "tab-1", "laporan keuangan", timeout=0.05, poll_interval=0.001
            )
    finally:
        bridge_client.close_client()


def test_wait_until_uses_custom_predicate() -> None:
    bodies = [
        httpx.Response(200, json={"text": "count: 1"}),
        httpx.Response(200, json={"text": "count: 2"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return bodies.pop(0)

    bridge_client = _mock_client(handler)
    try:
        found = bridge_client.wait_until(
            "tab-1",
            lambda page: "count: 2" in page,
            timeout=5,
            poll_interval=0.001,
            description="counter",
        )
        assert found == "count: 2"
    finally:
        bridge_client.close_client()


def test_managed_tab_closes_on_success_and_on_error() -> None:
    for fail_inside in (False, True):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path.endswith("/tabs/open"):
                return httpx.Response(200, json={"id": "tab-7"})
            return httpx.Response(200, json={"ok": True})

        bridge_client = _mock_client(handler)
        try:
            try:
                with bridge_client.managed_tab("https://example.test") as info:
                    assert info == {"id": "tab-7"}
                    if fail_inside:
                        raise RuntimeError("boom inside")
            except RuntimeError:
                assert fail_inside
            assert any(url.endswith("/tabs/tab-7/close") for url in calls), calls
        finally:
            bridge_client.close_client()


def test_managed_tab_swallows_close_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tabs/open"):
            return httpx.Response(200, json={"id": "tab-8"})
        return httpx.Response(503, json={"detail": "gone"})

    bridge_client = _mock_client(handler)
    try:
        with bridge_client.managed_tab("https://example.test"):
            pass  # close failing with 503 must not raise here
    finally:
        bridge_client.close_client()
