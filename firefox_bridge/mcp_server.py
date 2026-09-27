"""MCP v2 stdio adapter for the Firefox bridge."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from .client import FirefoxBridgeClient

mcp = MCPServer(
    name="firefox-bridge",
    version="0.1.0",
    instructions="Control the local Firefox browser through the Firefox Bridge REST API.",
)
_client: FirefoxBridgeClient | None = None


def get_client() -> FirefoxBridgeClient:
    global _client
    if _client is None:
        _client = FirefoxBridgeClient()
    return _client


def set_client(client: FirefoxBridgeClient | None) -> None:
    global _client
    _client = client


@mcp.tool()
def browser_status() -> dict[str, Any]:
    """Return bridge and Firefox extension connection status."""
    return get_client().status()


@mcp.tool()
def browser_tabs() -> list[dict[str, Any]]:
    """List tabs currently open in Firefox."""
    return get_client().tabs()


@mcp.tool()
def browser_open_tab(url: str, active: bool | None = None) -> dict[str, Any]:
    """Open a URL in a Firefox tab."""
    return get_client().open_tab(url, active)


@mcp.tool()
def browser_activate_tab(tab_id: str) -> dict[str, Any]:
    """Activate an existing Firefox tab."""
    return get_client().activate_tab(tab_id)


@mcp.tool()
def browser_close_tab(tab_id: str) -> dict[str, Any]:
    """Close an existing Firefox tab."""
    return get_client().close_tab(tab_id)


@mcp.tool()
def browser_navigate(tab_id: str, url: str) -> dict[str, Any]:
    """Navigate an existing Firefox tab to a URL."""
    return get_client().navigate(tab_id, url)


@mcp.tool()
def browser_snapshot(tab_id: str, max_elements: int | None = None) -> dict[str, Any]:
    """Return an accessibility-oriented snapshot of a Firefox tab."""
    return get_client().snapshot(tab_id, max_elements)


@mcp.tool()
def browser_click(tab_id: str, ref: str | int) -> dict[str, Any]:
    """Click an element identified by a snapshot reference."""
    return get_client().click(tab_id, ref)


@mcp.tool()
def browser_fill(tab_id: str, ref: str | int, value: str) -> dict[str, Any]:
    """Fill an input element identified by a snapshot reference."""
    return get_client().fill(tab_id, ref, value)


@mcp.tool()
def browser_page_text(tab_id: str, max_chars: int | None = None) -> dict[str, Any]:
    """Return visible page text, optionally truncated by character count."""
    return get_client().text(tab_id, max_chars)


@mcp.tool()
def browser_download(tab_id: str, ref: str | int, filename: str | None = None) -> dict[str, Any]:
    """Trigger a browser download for an element identified by a snapshot reference."""
    return get_client().download(tab_id, ref, filename)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()


__all__ = [
    "browser_activate_tab",
    "browser_click",
    "browser_close_tab",
    "browser_download",
    "browser_fill",
    "browser_navigate",
    "browser_open_tab",
    "browser_page_text",
    "browser_snapshot",
    "browser_status",
    "browser_tabs",
    "get_client",
    "main",
    "mcp",
    "set_client",
]
