"""Contract for the by-URL download route, across all four layers.

A download addressed by URL crosses four files that cannot import each other: the
extension registers a handler, the bridge allow-lists the method, the app serves a
route, and the client posts to it. Each layer names the same thing independently,
so any of them can be renamed or dropped without a single Python import breaking.

That is exactly the failure this file exists to catch. The ref-based route already
ships with contract coverage for the same reason; a silent mismatch here would
present as `Unsupported extension method` or a 404 on a live run, long after the
edit that caused it.
"""

from __future__ import annotations

from pathlib import Path

from firefox_bridge.app import _API_PREFIX
from firefox_bridge.bridge import _COMMANDS
from firefox_bridge.client import FirefoxBridgeClient
from firefox_bridge.models import DownloadUrlRequest

EXTENSION = Path(__file__).resolve().parent.parent / "extension"
PRODUCTIONS = Path(__file__).resolve().parent.parent

METHOD = "download.by_url"
ROUTE = "/download-by-url"
# What the client asks for must be exactly what the app mounts, and both must
# name the method the bridge allow-lists and the extension implements.
URL = f"{_API_PREFIX}{ROUTE}"


def _read(relative: str) -> str:
    return (PRODUCTIONS / relative).read_text(encoding="utf-8")


def test_extension_registers_the_by_url_method() -> None:
    """The handler exists, so a request cannot reach an unhandled method."""
    background = _read("extension/background.js")

    assert f'["{METHOD}"' in background


def test_bridge_allow_lists_the_method() -> None:
    """The bridge refuses unknown methods before they reach the extension."""
    assert METHOD in _COMMANDS


def test_app_serves_the_route_the_client_posts_to() -> None:
    """A route the client targets but the app does not mount is a live 404."""
    app = _read("firefox_bridge/app.py")

    # The decorator is an f-string, so assert the source fragment that composes
    # the very URL the client posts to, not a bare route literal.
    assert f"{{_API_PREFIX}}{ROUTE}" in app
    assert f'"{METHOD}"' in app


def test_client_posts_to_the_route_the_app_serves() -> None:
    """Assert the pairing directly rather than by comparing two string literals."""
    seen: dict[str, object] = {}

    client = FirefoxBridgeClient()

    def fake_request(
        method: str,
        path: str,
        *,
        body: dict | None = None,
        authenticated: bool = True,
    ) -> object:
        seen["method"] = method
        seen["path"] = path
        seen["body"] = body
        seen["authenticated"] = authenticated
        return {"ok": True}

    client._request = fake_request  # type: ignore[method-assign]
    client.download_url("https://www.idx.co.id/x/instance.zip", "saham/a.zip")

    assert seen["method"] == "POST"
    assert seen["path"] == URL
    assert seen["body"] == {
        "url": "https://www.idx.co.id/x/instance.zip",
        "filename": "saham/a.zip",
    }
    assert seen["authenticated"] is True, "an unauthenticated fetch route is open"


def test_route_is_not_nested_under_a_tab() -> None:
    """Nothing about a built URL depends on a page, so no tab id is implied.

    Served under `/tabs/{tab_id}` it would invite callers to supply a tab id
    they do not have, and read as though the download were scoped to one.
    """
    app = _read("firefox_bridge/app.py")
    prefix = "{_API_PREFIX}"
    route_line = next(
        (line for line in app.splitlines() if prefix + ROUTE in line), ""
    )

    assert route_line.startswith("    @app."), "route decorator not found"
    assert "/tabs/" not in route_line
    assert "{tab_id}" not in route_line


def test_request_model_rejects_missing_or_blank_arguments() -> None:
    """Both are required: Firefox otherwise picks its own filename."""
    import pytest
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        DownloadUrlRequest(url="", filename="saham/a.zip")
    with pytest.raises(PydanticValidationError):
        DownloadUrlRequest(url="https://www.idx.co.id/x.zip", filename="")
    with pytest.raises(PydanticValidationError):
        DownloadUrlRequest(filename="saham/a.zip")

    ok = DownloadUrlRequest(url="https://www.idx.co.id/x.zip", filename="saham/a.zip")
    assert ok.url.endswith(".zip")


def test_both_download_routes_exist_side_by_side() -> None:
    """The ref route is not replaced by this one -- the page flow still needs it.

    Dropping either would break a program that currently depends on it, so the
    pair is asserted rather than assumed.
    """
    app = _read("firefox_bridge/app.py")
    background = (EXTENSION / "background.js").read_text(encoding="utf-8")

    assert "{_API_PREFIX}/tabs/{{tab_id}}/download" in app
    assert '["tab.download"' in background
    assert f'["{METHOD}"' in background
    assert "tab.download" in _COMMANDS
    assert METHOD in _COMMANDS
