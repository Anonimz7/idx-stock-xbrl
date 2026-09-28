"""Check the bridge and extension before spending a browser workflow on them.

Opening the IDX profile takes a dozen paced steps and about half a minute. If
the extension is not connected, every one of those steps fails the same way, and
the user is left reading a timeout instead of the one fact that matters: start
the bridge and press Connect.

The bridge reports an unavailable extension as HTTP 503, and a bridge that is
not running at all as a connection error. Both mean the same thing to the person
running the program, so both become the same typed error.
"""

from __future__ import annotations

from typing import Any

import httpx

from firefox_bridge.client import FirefoxBridgeClient, FirefoxBridgeClientError

from .errors import ExtensionDisconnected

# The bridge answers 503 when it is up but has no extension attached.
EXTENSION_UNAVAILABLE_STATUS = 503


def _fetch_status(
    client: FirefoxBridgeClient,
) -> tuple[dict[str, Any] | None, ExtensionDisconnected | None]:
    """Read `/api/v1/status` once and classify it.

    Fetching once matters: asking twice for one fact leaves a window in which the
    extension could connect between the two reads, so the check and the reported
    version would describe different moments.
    """
    try:
        status = client.status()
    except httpx.ConnectError as error:
        return None, ExtensionDisconnected(
            f"Bridge tidak berjalan di {client.base_url} ({error.__class__.__name__})"
        )
    except (FirefoxBridgeClientError, httpx.HTTPError) as error:
        return None, ExtensionDisconnected(f"Bridge tidak bisa dihubungi: {error}")

    if not status.get("connected"):
        return status, ExtensionDisconnected(
            "Bridge berjalan, tetapi extension tidak terhubung"
        )
    return status, None


def probe_extension(client: FirefoxBridgeClient) -> ExtensionDisconnected | None:
    """Return why the extension is unusable, or None when it is ready.

    Never raises for the ordinary "not connected" answer -- that is a result to
    report, not an exception to propagate out of a diagnostic.
    """
    return _fetch_status(client)[1]


def ensure_extension_ready(client: FirefoxBridgeClient) -> str:
    """Raise `ExtensionDisconnected` unless the extension can be used.

    Returns the extension version, which the caller can log so the run records
    which build produced the files.
    """
    status, problem = _fetch_status(client)
    if problem is not None:
        raise problem
    assert status is not None  # noqa: S101 - narrowed by `problem is None`
    extension = status.get("extension") or {}
    return str(extension.get("version") or "")


__all__ = [
    "EXTENSION_UNAVAILABLE_STATUS",
    "ensure_extension_ready",
    "probe_extension",
]
