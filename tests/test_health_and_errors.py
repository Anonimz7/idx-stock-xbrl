"""The health gate, the error taxonomy, and the exit code they make reachable.

CLI-002 has promised exit code `3` for "bridge/extension gagal" since the task
list was written. Nothing could produce it: the CLI caught every failure as a
generic `Exception`, printed one message, and returned `1`. A run that lost the
extension and a run that hit one stale element looked identical, and the run
carried on repeating the same failure for every remaining stock.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from firefox_bridge.cli import (
    EXIT_BRIDGE_UNAVAILABLE,
    EXIT_FAILURES,
    EXIT_INVALID_INPUT,
    EXIT_SUCCESS,
    _as_downloader_error,
    run,
)
from firefox_bridge.downloader.errors import (
    DownloaderError,
    DownloadTimeout,
    ExtensionDisconnected,
    IntegrityError,
    StaleReference,
)
from firefox_bridge.downloader.health import ensure_extension_ready, probe_extension

# --- the taxonomy itself -------------------------------------------------


def test_only_losing_the_extension_is_fatal() -> None:
    """`fatal` is the question "could the next stock work?", nothing else.

    A stale ref is expected: IDX re-renders. Killing the run over it would mean
    one flaky page ends a batch of hundreds of stocks.
    """
    assert ExtensionDisconnected.fatal is True
    for error_type in (StaleReference, DownloadTimeout, IntegrityError, DownloaderError):
        assert error_type.fatal is False, f"{error_type.__name__} should not stop the run"


def test_every_taxonomy_member_shares_one_base() -> None:
    for error_type in (ExtensionDisconnected, StaleReference, DownloadTimeout, IntegrityError):
        assert issubclass(error_type, DownloaderError)


# --- transport failures become the taxonomy ------------------------------


def test_a_refused_connection_becomes_extension_disconnected() -> None:
    failure = _as_downloader_error(httpx.ConnectError("refused"))
    assert isinstance(failure, ExtensionDisconnected)
    assert failure.fatal is True


def test_http_503_becomes_extension_disconnected() -> None:
    """The bridge answers 503 when it is up but has no extension attached."""
    failure = _as_downloader_error(_client_error(503))
    assert isinstance(failure, ExtensionDisconnected)


def test_other_http_errors_are_not_treated_as_a_disconnect() -> None:
    failure = _as_downloader_error(_client_error(400))
    assert not isinstance(failure, ExtensionDisconnected), "a 400 must not stop the run"
    assert failure.fatal is False


def test_a_taxonomy_member_passes_through_unchanged() -> None:
    original = StaleReference("ref basi")
    assert _as_downloader_error(original) is original


def test_an_unexpected_error_is_wrapped_but_never_fatal() -> None:
    failure = _as_downloader_error(ZeroDivisionError("division by zero"))
    assert isinstance(failure, DownloaderError)
    assert "division by zero" in str(failure)
    assert failure.fatal is False


def _client_error(status_code: int) -> Exception:
    """Build the error the client raises for a given HTTP status.

    httpx refuses to read `error.response.text` without a request attached, which
    is exactly what the client does while building its message.
    """
    from firefox_bridge.client import FirefoxBridgeHTTPError

    request = httpx.Request("GET", "http://127.0.0.1:8765/api/v1/status")
    return FirefoxBridgeHTTPError(httpx.Response(status_code, request=request))


# --- the health gate -----------------------------------------------------


class FakeClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765") -> None:
        self.base_url = base_url
        self.statuses: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def status(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.statuses.pop(0)


def test_a_connected_extension_passes_the_gate() -> None:
    client = FakeClient()
    client.statuses.append({"connected": True, "extension": {"version": "0.1.8"}})
    assert ensure_extension_ready(client) == "0.1.8"


def test_a_bridge_with_no_extension_is_reported_not_raised() -> None:
    client = FakeClient()
    client.statuses.append({"connected": False})
    problem = probe_extension(client)
    assert isinstance(problem, ExtensionDisconnected)
    assert "tidak terhubung" in str(problem)


def test_a_bridge_that_is_not_running_is_reported_with_its_url() -> None:
    client = FakeClient(base_url="http://127.0.0.1:9999")
    client.error = httpx.ConnectError("connection refused")
    problem = probe_extension(client)
    assert isinstance(problem, ExtensionDisconnected)
    assert "127.0.0.1:9999" in str(problem), "the message should say where it looked"


def test_an_unexpected_status_shape_is_not_treated_as_connected() -> None:
    client = FakeClient()
    client.statuses.append({})
    assert isinstance(probe_extension(client), ExtensionDisconnected)


# --- the exit code -------------------------------------------------------


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    import firefox_bridge.cli as cli

    monkeypatch.setattr(cli, "FirefoxBridgeClient", lambda *_args, **_kwargs: client)


def test_a_disconnected_extension_exits_3_before_touching_the_browser(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate exists to stop a dozen paced steps from failing one at a time."""
    client = FakeClient()
    client.statuses.append({"connected": False})
    _patch_client(monkeypatch, client)

    code = run(["--stocks", "NCKL", "--year", "2025", "--all-detected"])

    assert code == EXIT_BRIDGE_UNAVAILABLE
    output = capsys.readouterr().err
    assert "PRAJAMAL" in output
    assert "Connect" in output, "the message should say what to do about it"
    assert "STEP 1" not in output, "the browser workflow must not have started"


def test_a_valid_run_still_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    client.statuses.append({"connected": True, "extension": {"version": "0.1.8"}})
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(
        "firefox_bridge.cli.download_all_detected",
        lambda *_args, **_kwargs: [],
    )

    assert run(["--stocks", "NCKL", "--year", "2025", "--all-detected"]) == EXIT_SUCCESS


def test_an_invalid_stock_code_still_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeClient()
    _patch_client(monkeypatch, client)

    assert run(["--stocks", "../evil"]) == EXIT_INVALID_INPUT
    assert "tidak valid" in capsys.readouterr().err


def test_a_fatal_failure_mid_run_exits_3_and_stops_early(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of `fatal`: one clear stop, not the same failure per stock.

    Three stocks were requested. The first loses the extension, so the run must
    not attempt the other two -- each attempt would burn half a minute and
    produce a message identical to the first.
    """
    attempted: list[str] = []

    def explode(client: Any, stock: str, *args: Any, **kwargs: Any) -> list[Any]:
        attempted.append(stock)
        raise httpx.ConnectError("connection refused")

    client = FakeClient()
    client.statuses.append({"connected": True, "extension": {"version": "0.1.8"}})
    _patch_client(monkeypatch, client)
    monkeypatch.setattr("firefox_bridge.cli.download_all_detected", explode)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    code = run(["--stocks", "NCKL,BBCA,ITMG", "--year", "2025", "--all-detected", "--delay", "1"])

    assert code == EXIT_BRIDGE_UNAVAILABLE
    assert attempted == ["NCKL"], "the run kept going after a fatal failure"
    assert "run dihentikan" in capsys.readouterr().err


def test_a_non_fatal_failure_exits_1_and_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One bad stock must not cost the others."""
    attempted: list[str] = []

    def sometimes(client: Any, stock: str, *args: Any, **kwargs: Any) -> list[Any]:
        attempted.append(stock)
        if stock == "BBCA":
            raise StaleReference("ref basi")
        return []

    client = FakeClient()
    client.statuses.append({"connected": True, "extension": {"version": "0.1.8"}})
    _patch_client(monkeypatch, client)
    monkeypatch.setattr("firefox_bridge.cli.download_all_detected", sometimes)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    code = run(["--stocks", "NCKL,BBCA,ITMG", "--year", "2025", "--all-detected", "--delay", "1"])

    assert code == EXIT_FAILURES
    assert attempted == ["NCKL", "BBCA", "ITMG"], "a per-stock failure stopped the batch"
    assert "StaleReference" in capsys.readouterr().err


def test_the_failure_line_names_the_error_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A reader should not have to parse an Indonesian sentence to know what broke."""
    client = FakeClient()
    client.statuses.append({"connected": True, "extension": {"version": "0.1.8"}})
    _patch_client(monkeypatch, client)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def explode(client: Any, stock: str, *args: Any, **kwargs: Any) -> list[Any]:
        raise IntegrityError("arsip bukan ZIP")

    monkeypatch.setattr("firefox_bridge.cli.download_all_detected", explode)

    run(["--stocks", "NCKL", "--year", "2025", "--all-detected", "--delay", "1"])

    assert "IntegrityError" in capsys.readouterr().err


def test_exit_codes_are_distinct() -> None:
    assert len({EXIT_SUCCESS, EXIT_FAILURES, EXIT_INVALID_INPUT, EXIT_BRIDGE_UNAVAILABLE}) == 4
