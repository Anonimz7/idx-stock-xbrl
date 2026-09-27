"""Configuration and token storage for the local Firefox bridge."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 10.0
TOKEN_ENV = "FIREFOX_BRIDGE_TOKEN"
TOKEN_FILE_ENV = "FIREFOX_BRIDGE_TOKEN_FILE"
URL_ENV = "FIREFOX_BRIDGE_URL"
PORT_ENV = "FIREFOX_BRIDGE_PORT"
TIMEOUT_ENV = "FIREFOX_BRIDGE_TIMEOUT"


def default_token_file() -> Path:
    """Return the platform-specific token-file location."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    else:
        root = Path.home() / ".config"
    return root / "firefox-bridge" / "token"


def _read_or_create_token(path: Path) -> str:
    path = path.expanduser()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    if value:
        return value

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as token_file:
            token_file.write(token)
            token_file.write("\n")
    except FileExistsError:
        # Another process may have initialized the file between the read and
        # the exclusive create. Prefer its value if it is non-empty.
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
        with path.open("w", encoding="utf-8", newline="\n") as token_file:
            token_file.write(token)
            token_file.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


def _parse_port(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError(f"{PORT_ENV} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{PORT_ENV} must be between 1 and 65535")
    return port


def _parse_timeout(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default
    try:
        timeout = float(value)
    except ValueError as exc:
        raise ValueError(f"{TIMEOUT_ENV} must be a number") from exc
    if timeout <= 0:
        raise ValueError(f"{TIMEOUT_ENV} must be greater than zero")
    return timeout


def _normalise_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{URL_ENV} must be an http(s) URL")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{URL_ENV} contains an invalid port") from exc
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings shared by the server, client, and MCP adapter."""

    token: str = field(repr=False)
    url: str = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT
    token_file: Path = field(default_factory=default_token_file)

    @property
    def host(self) -> str:
        """Return the URL host without changing the loopback bind default."""
        return urlsplit(self.url).hostname or DEFAULT_HOST

    @property
    def bind_host(self) -> str:
        """The server bind address; it is intentionally loopback by default."""
        return DEFAULT_HOST

    @property
    def websocket_url(self) -> str:
        """Return the corresponding WebSocket URL for the extension."""
        parsed = urlsplit(self.url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, "/extension", "", ""))

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from environment variables and the token store."""
        token_value = os.environ.get(TOKEN_ENV)
        token_file_value = os.environ.get(TOKEN_FILE_ENV)
        token_file = (
            Path(token_file_value).expanduser()
            if token_file_value and token_file_value.strip()
            else default_token_file()
        )
        token = token_value if token_value and token_value.strip() else _read_or_create_token(token_file)

        raw_url = os.environ.get(URL_ENV)
        raw_port = os.environ.get(PORT_ENV)
        if raw_url and raw_url.strip():
            url = _normalise_url(raw_url)
            parsed = urlsplit(url)
            default_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            port = _parse_port(raw_port, default_port)
        else:
            port = _parse_port(raw_port, DEFAULT_PORT)
            url = f"http://{DEFAULT_HOST}:{port}"

        timeout = _parse_timeout(os.environ.get(TIMEOUT_ENV), DEFAULT_TIMEOUT)
        return cls(token=token, url=url, port=port, timeout=timeout, token_file=token_file)


def get_settings() -> Settings:
    """Load the current process settings.

    The function intentionally does not cache values so test runners and
    long-lived processes can change the environment without restarting.
    """
    return Settings.from_env()


load_settings = get_settings


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "PORT_ENV",
    "Settings",
    "TIMEOUT_ENV",
    "TOKEN_ENV",
    "TOKEN_FILE_ENV",
    "URL_ENV",
    "default_token_file",
    "get_settings",
    "load_settings",
]
