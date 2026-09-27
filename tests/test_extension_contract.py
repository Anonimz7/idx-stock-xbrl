"""Contract tests between the Python client and the Firefox extension.

The extension runs as injected JavaScript and cannot import Python constants, so
the snapshot element budget is duplicated in two places. These tests fail loudly
if the three copies drift apart, because a lower extension cap silently
truncates snapshots and can drop IDX report links without any visible error.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from firefox_bridge.idx.browser_flow import SNAPSHOT_ELEMENTS, SNAPSHOT_ELEMENTS_COMPACT

EXTENSION = Path(__file__).resolve().parent.parent / "extension"
CAPTURED_VERSION = "0.1.8"
# Firefox terminates a non-persistent MV3 background page after
# `extensions.background.idle.timeout`, which defaults to 30s and is capped at
# 300s. Nothing in a loopback extension can revive a suspended page, because a
# suspended page runs no timers, so the extension has to keep calling a
# WebExtension API and stay strictly below that threshold.
FIREFOX_IDLE_TIMEOUT_MS = 30000
# Firefox loads every entry of `background.scripts` into one shared global scope,
# so a top-level `const` with the same name in two files is a fatal SyntaxError
# that leaves the background page with no `onMessage` listener at all.
BACKGROUND_SCRIPTS = ("page_functions.js", "background.js")
TOP_LEVEL_STATE = re.compile(r"^(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
TOP_LEVEL_BINDING = re.compile(
    r"^(?:const|let|var|class|function)\s+([A-Za-z_$][\w$]*)", re.MULTILINE
)


def _read(name: str) -> str:
    return (EXTENSION / name).read_text(encoding="utf-8")


def _element_cap(source: str, name: str) -> int:
    match = re.search(rf"const {name}\s*=\s*(\d+)\s*;", source)
    assert match is not None, f"{name} not found in extension source"
    return int(match.group(1))


def test_background_scripts_share_one_scope_without_redeclared_names() -> None:
    """A redeclaration across background scripts kills the whole background page."""

    owners: dict[str, list[str]] = {}
    for name in BACKGROUND_SCRIPTS:
        for declared in TOP_LEVEL_BINDING.findall(_read(name)):
            owners.setdefault(declared, []).append(name)

    clashes = {name: files for name, files in owners.items() if len(files) > 1}
    assert not clashes, f"redeclared across background scripts: {clashes}"


def test_page_functions_declares_no_top_level_state() -> None:
    """`executeScript({ func })` carries the function body only.

    A module-level constant would be undefined inside the page, so every helper
    in page_functions.js has to be self-contained.
    """

    offenders = TOP_LEVEL_STATE.findall(_read("page_functions.js"))
    assert not offenders, f"page_functions.js has page-invisible state: {offenders}"


def test_manifest_is_valid_json() -> None:
    manifest = json.loads(_read("manifest.json"))

    assert manifest["manifest_version"] == 3
    assert manifest["version"] == CAPTURED_VERSION
    assert manifest["browser_specific_settings"]["gecko"]["id"]


def test_download_permission_is_present_for_cookie_preserving_saves() -> None:
    permissions = json.loads(_read("manifest.json"))["permissions"]

    assert "downloads" in permissions
    assert {"tabs", "scripting", "storage"} <= set(permissions)


def test_snapshot_cap_matches_the_client_request() -> None:
    assert _element_cap(_read("background.js"), "MAX_SNAPSHOT_ELEMENTS") == SNAPSHOT_ELEMENTS
    assert _page_element_cap(_read("page_functions.js")) == SNAPSHOT_ELEMENTS


def _page_element_cap(source: str) -> int:
    match = re.search(r"const elementLimit\s*=\s*(\d+)\s*;", source)
    assert match is not None, "function-local elementLimit not found in page_functions.js"
    return int(match.group(1))


def test_compact_snapshot_budget_fits_inside_the_extension_cap() -> None:
    assert SNAPSHOT_ELEMENTS_COMPACT < SNAPSHOT_ELEMENTS


def test_page_functions_clamps_with_a_function_local_limit() -> None:
    source = _read("page_functions.js")
    body = source.split("function snapshotPage", 1)[1]

    assert "const elementLimit = 2000;" in body
    assert "const MAX_SNAPSHOT_ELEMENTS" not in source


def test_retry_backoff_stays_short_for_a_loopback_bridge() -> None:
    assert _element_cap(_read("background.js"), "MAX_RETRY_MS") <= 3000


def test_event_page_keepalive_runs_faster_than_firefox_idle_timeout() -> None:
    """The event page keepalive must beat Firefox's idle shutdown.

    Without this the background page is discarded mid-run, the poll loop dies
    with it, and the bridge starts answering every command with
    "extension unavailable".
    """

    period = _element_cap(_read("background.js"), "KEEPALIVE_INTERVAL_MS")
    assert 0 < period < FIREFOX_IDLE_TIMEOUT_MS


def test_event_page_keepalive_calls_a_webextension_api() -> None:
    """Only a WebExtension API call resets Firefox's idle timer.

    Per toolkit/components/extensions/parent/ext-backgroundPage.js the
    exemptions are devtools, an open native messaging port, a pending async
    listener promise, an active StreamFilter, and a parent API call
    ("parentapicall"). A pending fetch() and an open WebSocket are not on that
    list, which is why an open socket alone is not a keepalive.
    """

    source = _read("background.js")
    body = source.split("function startKeepalive", 1)[1].split("\nfunction ", 1)[0]

    assert "browser.runtime." in body
    assert "KEEPALIVE_INTERVAL_MS" in body
    for non_keepalive in ("fetch(", "new WebSocket", "setTimeout("):
        assert non_keepalive not in body, f"{non_keepalive} cannot hold the event page open"


def test_websocket_is_the_primary_transport_with_http_fallback() -> None:
    source = _read("background.js")

    # The socket must be opened from the converted URL, never from the raw
    # stored setting, which defaults to a `ws://` scheme and may be edited to
    # `http://` in the popup.
    assert "wsUrlInUse = webSocketUrl();" in source
    assert "new WebSocket(wsUrlInUse)" in source
    # The HTTP loop must stand down while the socket is authenticated, or every
    # run pays for both transports at once.
    assert "if (wsAuthenticated) {" in source
    assert source.index("if (wsAuthenticated) {") < source.index("/extension/poll")


def test_reported_extension_version_matches_the_manifest() -> None:
    manifest = json.loads(_read("manifest.json"))

    assert _element_cap_str(_read("background.js"), "EXTENSION_VERSION") == manifest["version"]


def _element_cap_str(source: str, name: str) -> str:
    match = re.search(rf'const {name}\s*=\s*"([^"]+)"\s*;', source)
    assert match is not None, f"{name} not found in extension source"
    return match.group(1)


def test_bridge_url_must_be_loopback() -> None:
    source = _read("background.js")

    assert "LOOPBACK_HOSTS" in source
    assert "127.0.0.1" in source
    assert "localhost" in source


def test_http_poll_carries_extension_identity_and_transport_diagnostics() -> None:
    """The bridge cannot inspect the background page, so the poll must self-report.

    Without this, `/api/v1/status` cannot say which build is attached or why a
    live WebSocket never came up, and the only clue is a log line that never
    appears because the connection was blocked before it reached the network.
    """
    source = _read("background.js")

    assert "function bridgeSelfReport(" in source
    assert 'postHttp("/extension/poll", bridgeSelfReport())' in source
    for field in ("status", "error", "websocket", "websocket_error", "websocket_attempts"):
        assert f"{field}:" in source, f"diagnostics is missing {field}"


def test_websocket_loop_reports_why_it_stopped() -> None:
    """A frozen attempt counter cannot be read on its own.

    It looks identical whether the loop gave up or is still blocked awaiting a
    socket, and those need opposite fixes. Every exit path therefore has to
    record a reason, and that reason has to reach the bridge.
    """

    source = _read("background.js")

    assert "function stopWebSocketLoop(" in source
    assert "websocket_exit: wsExitReason" in source
    assert "websocket_state: wsLoopState" in source
    # Each of the three ways out of the loop has to name itself.
    for reason in (
        "disconnected by the user",
        "settings.enabled is false",
        "superseded by a newer generation",
    ):
        assert reason in source, f"loop exit is unattributed: {reason}"


def test_websocket_attempts_are_counted_before_the_socket_is_opened() -> None:
    """`new WebSocket` reports failure asynchronously, so the counter has to move
    before the call. A counter that increments afterwards reads zero forever and
    makes 'never attempted' look identical to 'never reported'."""

    source = _read("background.js")
    increment = source.index("wsAttempts += 1;")
    open_socket = source.index("new WebSocket(")

    assert increment < open_socket


def test_extension_pages_csp_allows_the_loopback_socket() -> None:
    """Firefox upgrades an insecure `ws://` request to `wss://` unless the
    extension's own policy says otherwise.

    The symptom is silent and misleading: the socket is created, the `error`
    event fires, and the connection never reaches the network, so a plain HTTP
    server sees no attempt at all. Reporting it as "WebSocket connection failed"
    sends you looking at the server instead of the policy. `connect-src` is also
    pinned to loopback here, which is tighter than the default, not looser.
    """

    manifest = json.loads(_read("manifest.json"))
    policy = manifest["content_security_policy"]["extension_pages"]

    assert "connect-src" in policy
    for scheme in ("ws://127.0.0.1:*", "ws://localhost:*", "wss://127.0.0.1:*"):
        assert scheme in policy, f"connect-src is missing {scheme}"
    # A host pattern with an explicit port is silently granted but matches
    # nothing (Firefox bug 1362809), so the port has to be the CSP `*` wildcard.
    assert "ws://127.0.0.1:8765" not in policy
    # The MV3 script restrictions must stay in place.
    assert "script-src 'self'" in policy
    assert "unsafe-eval" not in policy


def test_websocket_close_code_is_reported() -> None:
    """`error` fires before `close` and carries no code, so the code is the only
    thing that separates 'never established' from 'established then dropped'."""

    source = _read("background.js")

    assert "websocket_close: lastWebSocketClose" in source
    assert "lastWebSocketClose = `${event.code}" in source


def test_background_script_does_not_use_window_open() -> None:
    """`window.open` from a background page bypasses the extension APIs.

    `page_functions.js` is injected into the page and may legitimately read
    `window.location`; only the background script is guarded here.
    """

    offenders = [
        line
        for line in _read("background.js").splitlines()
        if re.search(r"\bwindow\.(open|location|alert)\b", line)
    ]
    assert not offenders, f"background.js uses a window API: {offenders}"


def test_removed_download_message_handler_is_gone() -> None:
    """`bridge-download` was dead code that could only ever fail."""

    assert "bridge-download" not in _read("background.js")
    assert "bridge-download" not in _read("popup.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_background_page_boots_and_registers_one_message_listener() -> None:
    """Load the background scripts the way Firefox does and prove the page boots.

    `node --check` parses each file in isolation, so it cannot see a redeclared
    top-level binding in the scope that `background.scripts` shares. That failure
    leaves the extension with no `onMessage` listener, which the popup reports as
    "Could not establish connection. Receiving end does not exist."

    The same harness proves the keepalive was armed and that both transports
    derive their URL from the one configured address.
    """

    report = _run_harness()

    assert report["messageListenerCount"] == 1
    assert report["pageHelpersPresent"] == [
        "snapshotPage",
        "clickPage",
        "fillPage",
        "downloadPage",
    ]
    assert report["intervalPeriodsMs"], "the event page keepalive was not armed"
    assert all(0 < period < FIREFOX_IDLE_TIMEOUT_MS for period in report["intervalPeriodsMs"])
    assert report["transports"] == {
        "webSocketUrl": "ws://127.0.0.1:8765/extension",
        "httpBridgeUrl": "http://127.0.0.1:8765/extension/poll",
    }


def _run_harness() -> dict[str, object]:
    harness = Path(__file__).parent / "extension_harness.js"
    result = subprocess.run(
        ["node", str(harness), str(EXTENSION)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("name", ["background.js", "page_functions.js", "popup.js"])
def test_extension_sources_use_lf_endings_only(name: str) -> None:
    assert "\r" not in _read(name)
