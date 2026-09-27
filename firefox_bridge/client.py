"""Synchronous HTTP client for the Firefox bridge REST API."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings, get_settings


class FirefoxBridgeClientError(Exception):
    """Base error for client-side bridge failures."""


class FirefoxBridgeHTTPError(FirefoxBridgeClientError, httpx.HTTPStatusError):
    """An HTTP error returned by the bridge."""

    def __init__(self, response: httpx.Response) -> None:
        detail: Any
        try:
            body = response.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
        except ValueError:
            detail = response.text
        httpx.HTTPStatusError.__init__(
            self,
            f"Bridge request failed with HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
        self.detail = detail
        self.status_code = response.status_code


class FirefoxBridgeClient:
    """Synchronous client for the local Firefox bridge."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        *,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        active_settings = settings or get_settings()
        self.base_url = (base_url or active_settings.url).rstrip("/")
        self.token = active_settings.token if token is None else token
        self.timeout = active_settings.timeout if timeout is None else timeout
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
        )

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
        response = self._client.request(
            method,
            self._url(path),
            headers=headers,
            json=body,
            timeout=self.timeout,
        )
        if response.is_error:
            raise FirefoxBridgeHTTPError(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

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
            body={"ref": ref, "value": value},
        )

    def text(self, tab_id: str, max_chars: int | None = None) -> Any:
        body = {} if max_chars is None else {"max_chars": max_chars}
        return self._request(
            "POST",
            f"/api/v1/tabs/{tab_id}/text",
            body=body,
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

    def close_client(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> FirefoxBridgeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close_client()


__all__ = [
    "FirefoxBridgeClient",
    "FirefoxBridgeClientError",
    "FirefoxBridgeHTTPError",
]
