"""Synchronous HTTP client for the Firefox bridge REST API."""

from __future__ import annotations

import contextlib
import os
import time
import warnings
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from .config import Settings, get_settings


class FirefoxBridgeClientError(Exception):
    """Base error for client-side bridge failures."""


class FirefoxBridgeHTTPError(FirefoxBridgeClientError, httpx.HTTPStatusError):
    """An HTTP error returned by the bridge."""

    def __init__(self, response: httpx.Response) -> None:
        detail: Any
        server_error: Any = None
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = body.get("detail", body)
                server_error = body.get("error")
            else:
                detail = body
        except ValueError:
            detail = response.text
        httpx.HTTPStatusError.__init__(
            self,
            f"Bridge request failed with HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
        self.detail = detail
        self.server_error = server_error
        self.status_code = response.status_code


class FirefoxBridgeAuthError(FirefoxBridgeHTTPError):
    """HTTP 401: the bridge rejected the authentication token."""


class FirefoxBridgeBadRequestError(FirefoxBridgeHTTPError):
    """HTTP 400/422: the request itself was invalid."""


class FirefoxBridgeExtensionError(FirefoxBridgeHTTPError):
    """HTTP 502: the extension received the command but failed to run it."""


class FirefoxBridgeExtensionUnavailableError(FirefoxBridgeHTTPError):
    """HTTP 503: no Firefox extension is currently connected to the bridge."""


class FirefoxBridgeTimeoutError(FirefoxBridgeClientError):
    """The bridge or extension did not answer in time.

    Raised for HTTP 504 responses and for transport-level timeouts.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class FirefoxBridgeConnectionError(FirefoxBridgeClientError):
    """The client could not reach the bridge at all (connection refused, reset...)."""


# Statuses worth retrying with backoff: the bridge is alive but the extension
# side hiccuped. 4xx (bad token, bad params) never gets better by retrying.
_RETRYABLE_HTTP_ERRORS = (
    FirefoxBridgeExtensionError,
    FirefoxBridgeExtensionUnavailableError,
    FirefoxBridgeTimeoutError,
    FirefoxBridgeConnectionError,
)

_NO_PROXY_KEYS = ("no_proxy", "NO_PROXY")


def _clean_no_proxy(value: str) -> str:
    """Drop entries httpx cannot parse.

    httpx crashes at client construction when no_proxy contains bracketed
    IPv6 literals such as "[::1]" (``InvalidURL: Invalid port: ':1]'``).
    Those entries are removed; everything else is kept verbatim.
    """
    kept = [
        part.strip()
        for part in value.split(",")
        if part.strip() and "[" not in part and "]" not in part
    ]
    return ",".join(kept)


@contextlib.contextmanager
def _sanitized_proxy_env() -> Iterator[None]:
    """Temporarily strip proxy env values that crash httpx parsing."""
    overrides = {}
    for key in _NO_PROXY_KEYS:
        raw = os.environ.get(key)
        if raw and ("[" in raw or "]" in raw):
            cleaned = _clean_no_proxy(raw)
            if cleaned != raw:
                overrides[key] = cleaned
    if not overrides:
        yield
        return
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _build_http_client(base_url: str, timeout: float) -> httpx.Client:
    """Build the httpx client, surviving broken proxy env vars.

    Falls back to ignoring the proxy environment entirely (with a warning)
    only when httpx cannot even be constructed otherwise.
    """
    try:
        with _sanitized_proxy_env():
            return httpx.Client(base_url=base_url, timeout=timeout)
    except httpx.InvalidURL:
        warnings.warn(
            "FirefoxBridgeClient: proxy environment variables are unusable "
            "(httpx failed to parse them); connecting without proxy env.",
            RuntimeWarning,
            stacklevel=2,
        )
        return httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)


def _raise_for_status(response: httpx.Response) -> None:
    """Raise the typed client error matching the HTTP status code."""
    status = response.status_code
    if status == 401:
        raise FirefoxBridgeAuthError(response)
    if status in (400, 422):
        raise FirefoxBridgeBadRequestError(response)
    if status == 502:
        raise FirefoxBridgeExtensionError(response)
    if status == 503:
        raise FirefoxBridgeExtensionUnavailableError(response)
    if status == 504:
        detail: Any
        try:
            body = response.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
        except ValueError:
            detail = response.text
        raise FirefoxBridgeTimeoutError(
            f"Bridge request timed out (HTTP {status})",
            status_code=status,
            detail=detail,
        )
    raise FirefoxBridgeHTTPError(response)


class FirefoxBridgeClient:
    """Synchronous client for the local Firefox bridge."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_backoff: float | None = None,
        *,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        active_settings = settings or get_settings()
        self.base_url = (base_url or active_settings.url).rstrip("/")
        self.token = active_settings.token if token is None else token
        self.timeout = active_settings.timeout if timeout is None else timeout
        self.max_retries = (
            active_settings.max_retries if max_retries is None else max_retries
        )
        self.retry_backoff = (
            active_settings.retry_backoff if retry_backoff is None else retry_backoff
        )
        if self.max_retries < 0:
            raise ValueError("max_retries must be zero or greater")
        if self.retry_backoff < 0:
            raise ValueError("retry_backoff must be zero or greater")
        self._owns_client = client is None
        self._client = client or _build_http_client(self.base_url, self.timeout)

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if authenticated and self.token else {}
        url = self._url(path)
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=body,
                    timeout=self.timeout,
                )
            except httpx.TimeoutException as exc:
                error: FirefoxBridgeClientError = FirefoxBridgeTimeoutError(
                    f"Bridge request timed out after {self.timeout}s: {method} {url}"
                )
                error.__cause__ = exc
            except httpx.ConnectError as exc:
                error = FirefoxBridgeConnectionError(
                    f"Could not connect to the bridge at {self.base_url}: {exc}"
                )
                error.__cause__ = exc
            except httpx.HTTPError as exc:
                error = FirefoxBridgeClientError(f"Bridge request failed: {exc}")
                error.__cause__ = exc
            else:
                if not response.is_error:
                    if response.status_code == 204 or not response.content:
                        return None
                    try:
                        return response.json()
                    except ValueError:
                        return response.text
                try:
                    _raise_for_status(response)
                except FirefoxBridgeClientError as exc:
                    error = exc
            if not isinstance(error, _RETRYABLE_HTTP_ERRORS) or attempt + 1 >= attempts:
                raise error
            time.sleep(self.retry_backoff * (2**attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    def health(self) -> Any:
        return self._request("GET", "/health", authenticated=False)

    def status(self) -> Any:
        return self._request("GET", "/api/v1/status")

    def tabs(self) -> Any:
        return self._request("GET", "/api/v1/tabs")

    def open_tab(self, url: str, active: bool | None = None) -> Any:
        body: dict[str, Any] = {"url": url}
        if active is not None:
            body["active"] = active
        return self._request("POST", "/api/v1/tabs/open", body=body)

    @contextlib.contextmanager
    def managed_tab(
        self, url: str = "about:blank", *, active: bool = True
    ) -> Iterator[Any]:
        """Open a tab, yield its info dict, and close it on exit.

        The tab is closed even if the body raises. Close failures during
        cleanup are swallowed: the tab is already gone or the bridge is
        unreachable, and cleanup must not mask the original error.
        """
        info = self.open_tab(url, active=active)
        tab_id = info.get("id") if isinstance(info, dict) else info
        try:
            yield info
        finally:
            try:
                self.close_tab(tab_id)
            except FirefoxBridgeClientError:
                pass

    def activate_tab(self, tab_id: str) -> Any:
        return self._request("POST", f"/api/v1/tabs/{tab_id}/activate")

    def close_tab(self, tab_id: str) -> Any:
        return self._request("POST", f"/api/v1/tabs/{tab_id}/close")

    def navigate(self, tab_id: str, url: str) -> Any:
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/navigate",
            body={"url": url},
        )

    def snapshot(self, tab_id: str, max_elements: int | None = None) -> Any:
        body = {} if max_elements is None else {"max_elements": max_elements}
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/snapshot",
            body=body,
        )

    def click(self, tab_id: str, ref: str | int) -> Any:
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/click",
            body={"ref": ref},
        )

    def fill(self, tab_id: str, ref: str | int, value: str) -> Any:
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/fill",
            body={"ref": ref},
        )

    def text(self, tab_id: str, max_chars: int | None = None) -> Any:
        body = {} if max_chars is None else {"max_chars": max_chars}
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/text",
            body=body,
        )

    def wait_until(
        self,
        tab_id: str,
        predicate: Callable[[str], bool],
        *,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
        description: str = "condition",
        max_chars: int = 100000,
    ) -> str:
        """Poll the tab's text until ``predicate`` accepts it.

        Returns the page text that satisfied the predicate. Raises
        :class:`FirefoxBridgeTimeoutError` when the deadline passes.
        """
        deadline = time.monotonic() + timeout
        while True:
            result = self.text(tab_id, max_chars=max_chars)
            page_text = (
                result.get("text", "") if isinstance(result, dict) else str(result)
            )
            if predicate(page_text):
                return page_text
            if time.monotonic() >= deadline:
                raise FirefoxBridgeTimeoutError(
                    f"Timed out after {timeout}s waiting for {description} "
                    f"on tab {tab_id}"
                )
            time.sleep(poll_interval)

    def wait_for_text(
        self,
        tab_id: str,
        text: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 1.0,
        case_sensitive: bool = False,
        max_chars: int = 100000,
    ) -> str:
        """Poll the tab's text until ``text`` appears; return the page text."""
        needle = text if case_sensitive else text.lower()

        def _matches(page_text: str) -> bool:
            haystack = page_text if case_sensitive else page_text.lower()
            return needle in haystack

        return self.wait_until(
            tab_id,
            _matches,
            timeout=timeout,
            poll_interval=poll_interval,
            description=f"text {text!r}",
            max_chars=max_chars,
        )

    def select_dropdown(self, tab_id: str, ref: str | int, value: str) -> Any:
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/select-dropdown",
            body={"ref": ref, "value": value},
        )

    def select_dropdown_option(self, tab_id: str, ref: str | int, value: str) -> Any:
        """Click the dropdown option matching the given text/value."""
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/select-dropdown-option",
            body={"ref": ref, "value": value},
        )

    def download(self, tab_id: str, ref: str | int, filename: str | None = None) -> Any:
        body: dict[str, Any] = {"ref": ref}
        body["filename"] = filename if filename is not None else ""
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/download",
            body=body,
        )

    def download_url(self, url: str, filename: str) -> Any:
        """Start a download addressed by URL, with no page or element involved.

        ``filename`` is the path Firefox should write, relative to the download
        root -- the same contract as :meth:`download`, so both routes land in
        staging under the caller's control.
        """
        return self._request(
            "POST",
            "/api/v1/download-by-url",
            body={"url": url, "filename": filename},
        )

    def close_client(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> FirefoxBridgeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close_client()


__all__ = [
    "FirefoxBridgeAuthError",
    "FirefoxBridgeBadRequestError",
    "FirefoxBridgeClient",
    "FirefoxBridgeClientError",
    "FirefoxBridgeConnectionError",
    "FirefoxBridgeExtensionError",
    "FirefoxBridgeExtensionUnavailableError",
    "FirefoxBridgeHTTPError",
    "FirefoxBridgeTimeoutError",
]
