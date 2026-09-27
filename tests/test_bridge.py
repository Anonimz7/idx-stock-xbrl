from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from firefox_bridge import bridge as bridge_module
from firefox_bridge.app import create_app
from firefox_bridge.config import Settings


def make_settings(tmp_path: Path, token: str = "test-token") -> Settings:
    return Settings(
        token=token,
        url="http://testserver",
        token_file=tmp_path / "token",
        timeout=1.0,
    )


def test_token_auth_and_public_health(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/v1/status").status_code == 401
        assert client.get(
            "/api/v1/status", headers={"Authorization": "Bearer wrong"}
        ).status_code == 401
        response = client.get(
            "/api/v1/status", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200
        assert response.json() == {"connected": False, "extension": None}


def test_command_without_extension_returns_503(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/tabs", headers={"Authorization": "Bearer test-token"}
        )
    assert response.status_code == 503


def test_websocket_authentication_and_request_response(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/extension") as websocket:
        websocket.send_json(
            {
                "type": "authenticate",
                "token": "test-token",
                "extension": {"name": "test-extension", "version": "1"},
            }
        )
        authenticated = websocket.receive_json()
        assert authenticated == {
            "type": "authenticated",
            "ok": True,
            "extension": {"name": "test-extension", "version": "1"},
        }

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                client.get,
                "/api/v1/tabs",
                headers={"Authorization": "Bearer test-token"},
            )
            request = websocket.receive_json()
            assert request["type"] == "request"
            assert request["method"] == "tabs.list"
            assert request["params"] == {}
            websocket.send_json(
                {
                    "type": "response",
                    "id": request["id"],
                    "ok": True,
                    "result": [{"id": "tab-1", "url": "https://example.test"}],
                }
            )
            response = pending.result(timeout=2)
        assert response.status_code == 200
        assert response.json()[0]["id"] == "tab-1"


def test_websocket_rejects_invalid_token(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as client, client.websocket_connect("/extension") as websocket:
        websocket.send_json({"type": "authenticate", "token": "wrong"})
        response = websocket.receive_json()

    assert response["type"] == "authenticated"
    assert response["ok"] is False


def test_http_poll_transport_authentication_and_request_response(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            poll = executor.submit(
                client.post,
                "/extension/poll",
                headers=headers,
                json={},
            )
            time.sleep(0.1)
            tabs = executor.submit(client.get, "/api/v1/tabs", headers=headers)
            command = poll.result(timeout=2).json()["command"]
            assert command["type"] == "request"
            assert command["method"] == "tabs.list"
            response = client.post(
                "/extension/response",
                headers=headers,
                json={
                    "type": "response",
                    "id": command["id"],
                    "ok": True,
                    "result": [{"id": "tab-http"}],
                },
            )
            assert response.status_code == 200
            tabs_response = tabs.result(timeout=2)
        assert tabs_response.status_code == 200
        assert tabs_response.json()[0]["id"] == "tab-http"
        status = client.get("/api/v1/status", headers=headers).json()
        assert status["connected"] is True


def test_http_poll_reports_extension_identity_and_diagnostics(tmp_path: Path) -> None:
    """The extension cannot be inspected, so it self-reports on every poll.

    This is what makes `/api/v1/status` able to say *which* build is attached
    instead of hardcoding a name, and why a live WebSocket failure is visible
    without opening the add-on's developer tools.
    """
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer test-token"}
    report = {
        "extension": {"name": "firefox-extension", "version": "0.1.7"},
        "diagnostics": {
            "status": "connected",
            "error": "",
            "websocket": False,
            "websocket_error": "WebSocket connection failed",
            "websocket_attempts": 3,
            "websocket_state": "exited",
            "websocket_exit": "settings.enabled is false",
            "websocket_url": "ws://127.0.0.1:8765/extension",
            "unrecognised": "must be dropped",
        },
    }
    with TestClient(app) as client:
        client.post("/extension/poll", headers=headers, json=report)
        status = client.get("/api/v1/status", headers=headers).json()

    assert status["connected"] is True
    assert status["extension"]["name"] == "firefox-extension"
    assert status["extension"]["version"] == "0.1.7"
    assert status["extension"]["transport"] == "http"
    reported = status["extension"]["http_diagnostics"]
    assert reported == {key: value for key, value in report["diagnostics"].items() if key != "unrecognised"}
    # An unrecognised key must not reach `/api/v1/status`.
    assert "unrecognised" not in reported


def test_http_poll_without_report_keeps_working(tmp_path: Path) -> None:
    """An older extension posts an empty body; it must still register as connected."""
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        client.post("/extension/poll", headers=headers, json={})
        status = client.get("/api/v1/status", headers=headers).json()

    assert status["connected"] is True
    assert status["extension"] == {"name": "firefox-extension", "transport": "http"}


def test_http_poll_without_body_is_tolerated(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        client.post("/extension/poll", headers=headers)
        status = client.get("/api/v1/status", headers=headers).json()

    assert status["connected"] is True


def test_stale_http_diagnostics_are_dropped_once_the_socket_authenticates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report from before the socket came up describes nothing current.

    Presenting it next to a healthy WebSocket is how a fixed fault keeps looking
    like a live one, which is the exact confusion the diagnostics exist to avoid.
    """
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer test-token"}
    # In production the HTTP loop stops polling once the socket authenticates, so
    # the last report goes stale on its own. Age it out here instead of waiting.
    monkeypatch.setattr(bridge_module, "_HTTP_STALE_SECONDS", -1.0)

    with TestClient(app) as client, client.websocket_connect("/extension") as websocket:
        # Authenticate before polling. A `client.post` blocks the TestClient
        # portal for the length of the poll, which is long enough to outlast the
        # bridge's own authentication timeout.
        websocket.send_json(
            {
                "type": "authenticate",
                "token": "test-token",
                "extension": {"name": "firefox-extension", "version": "0.1.7"},
            }
        )
        assert websocket.receive_json()["ok"] is True
        client.post(
            "/extension/poll",
            headers=headers,
            json={"diagnostics": {"websocket": False, "websocket_attempts": 12}},
        )
        status = client.get("/api/v1/status", headers=headers).json()

    assert status["connected"] is True
    assert status["extension"] == {"name": "firefox-extension", "version": "0.1.7"}


def test_transport_diagnostic_is_logged_once_per_change(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """This handler runs at 1 Hz, so an unchanged diagnostic must not log again."""
    app = create_app(make_settings(tmp_path))
    headers = {"Authorization": "Bearer test-token"}

    def poll(websocket: bool, error: str) -> None:
        client.post(
            "/extension/poll",
            headers=headers,
            json={"diagnostics": {"websocket": websocket, "websocket_error": error}},
        )

    with TestClient(app) as client, caplog.at_level(logging.INFO, logger="firefox_bridge"):
        poll(False, "ECONNREFUSED")
        poll(False, "ECONNREFUSED")
        poll(False, "ECONNREFUSED")
        poll(True, "")
        poll(False, "next failure")

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage() == "Extension is polling over HTTP, not WebSocket"
    ]
    # Three distinct states: first failure, socket healthy, then a new failure.
    assert len(messages) == 2
