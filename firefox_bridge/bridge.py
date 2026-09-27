"""WebSocket and HTTP polling transport management."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from .config import Settings
from .errors import (
    ExtensionCommandError,
    ExtensionCommandTimeoutError,
    ExtensionUnavailableError,
    InvalidExtensionMessageError,
)
from .logging_config import get_logger

logger = get_logger()

_COMMANDS = frozenset(
    {
        "tabs.list",
        "tabs.open",
        "tabs.activate",
        "tabs.close",
        "tab.navigate",
        "tab.snapshot",
        "tab.click",
        "tab.fill",
        "tab.text",
        "tab.select_dropdown",
        "tab.select_dropdown_option",
        "tab.download",
    }
)
_HTTP_POLL_SECONDS = 1.0
_HTTP_STALE_SECONDS = 5.0
# What the extension is allowed to self-report on each poll. Anything else is
# dropped rather than merged, so a malformed report cannot overwrite state the
# bridge derived for itself.
_HTTP_DIAGNOSTIC_KEYS = (
    "status",
    "error",
    "websocket",
    "websocket_error",
    "websocket_attempts",
    "websocket_state",
    "websocket_exit",
    "websocket_url",
    "websocket_close",
)


@dataclass(slots=True)
class ExtensionConnection:
    websocket: WebSocket
    extension: Any = None
    closed: bool = False
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)
    _pending_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise ExtensionUnavailableError()
        try:
            async with self._send_lock:
                if self.closed:
                    raise ExtensionUnavailableError()
                await self.websocket.send_json(message)
        except ExtensionUnavailableError:
            raise
        except (OSError, RuntimeError, WebSocketDisconnect) as exc:
            self.closed = True
            raise ExtensionUnavailableError() from exc

    async def fail_pending(self, error: Exception) -> None:
        async with self._pending_lock:
            pending = list(self.pending.values())
            self.pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    async def close(self, code: int = 1000, reason: str = "connection closed") -> None:
        if not self.closed:
            self.closed = True
            try:
                await self.websocket.close(code=code, reason=reason)
            except (OSError, RuntimeError, WebSocketDisconnect):
                pass
        await self.fail_pending(ExtensionUnavailableError("Firefox extension disconnected"))


class FirefoxBridge:
    """Manage one authenticated extension through WebSocket or HTTP polling."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._connection: ExtensionConnection | None = None
        self._state_lock = asyncio.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=50)
        self._last_event: dict[str, Any] | None = None
        self._http_pending: dict[str, asyncio.Future[Any]] = {}
        self._http_pending_lock = asyncio.Lock()
        self._http_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._http_connected = False
        self._http_last_seen = 0.0
        # Identity and last transport error the extension reports on each poll.
        # Without this the bridge cannot tell which extension build is attached,
        # which is exactly the question you need answered when a temporary
        # add-on is reloaded underneath it.
        self._http_extension: dict[str, Any] = {"name": "firefox-extension", "transport": "http"}
        self._http_diagnostics: dict[str, Any] = {}
        self._http_diagnostics_logged: dict[str, Any] = {}

    @property
    def connected(self) -> bool:
        connection = self._connection
        return connection is not None and not connection.closed

    def _http_available_locked(self) -> bool:
        if not self._http_connected:
            return False
        if time.monotonic() - self._http_last_seen <= _HTTP_STALE_SECONDS:
            return True
        self._http_connected = False
        while True:
            try:
                self._http_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        return False

    async def status(self) -> dict[str, Any]:
        async with self._state_lock:
            http_connected = self._http_available_locked()
            websocket_connected = self._connection is not None and not self._connection.closed
            connected = websocket_connected or http_connected
            if not connected:
                extension = None
            elif websocket_connected and self._connection is not None:
                extension = self._connection.extension
            else:
                extension = dict(self._http_extension)
            if extension is not None and http_connected and self._http_diagnostics:
                # Only while the extension is still polling. Once the socket is
                # authenticated the HTTP loop stands down, and a report from
                # before that is no longer a description of anything current.
                extension = {**extension, "http_diagnostics": dict(self._http_diagnostics)}
            return {"connected": connected, "extension": extension}

    async def events(self) -> list[dict[str, Any]]:
        async with self._state_lock:
            return list(self._events)

    @property
    def last_event(self) -> dict[str, Any] | None:
        return self._last_event

    async def _register(self, websocket: WebSocket, extension: Any) -> ExtensionConnection:
        connection = ExtensionConnection(websocket=websocket, extension=extension)
        async with self._state_lock:
            previous = self._connection
            self._connection = connection
        if previous is not None and previous is not connection:
            await previous.close(code=1000, reason="replaced by a new extension connection")
        return connection

    async def _unregister(self, connection: ExtensionConnection) -> None:
        async with self._state_lock:
            if self._connection is connection:
                self._connection = None
        connection.closed = True
        await connection.fail_pending(ExtensionUnavailableError("Firefox extension disconnected"))

    async def _authenticate(self, websocket: WebSocket) -> tuple[bool, Any]:
        try:
            received = await asyncio.wait_for(websocket.receive(), timeout=self.settings.timeout)
        except (TimeoutError, WebSocketDisconnect):
            await self._reject_authentication(websocket)
            return False, None

        if received.get("type") == "websocket.disconnect":
            return False, None
        raw_message = received.get("text")
        if raw_message is None:
            raw_message = received.get("bytes")
        if isinstance(raw_message, bytes):
            try:
                raw_message = raw_message.decode("utf-8")
            except UnicodeDecodeError:
                await self._reject_authentication(websocket)
                return False, None
        if not isinstance(raw_message, str):
            await self._reject_authentication(websocket)
            return False, None
        try:
            message = json.loads(raw_message)
        except (TypeError, ValueError):
            await self._reject_authentication(websocket)
            return False, None
        if not isinstance(message, dict) or message.get("type") != "authenticate":
            await self._reject_authentication(websocket)
            return False, None

        supplied_token = message.get("token")
        if not isinstance(supplied_token, str) or not secrets.compare_digest(
            supplied_token.encode("utf-8"), self.settings.token.encode("utf-8")
        ):
            await self._reject_authentication(websocket)
            return False, None

        extension = message.get("extension")
        if extension is None:
            extension = {
                key: message[key]
                for key in ("name", "version")
                if key in message
            } or {"name": "firefox-extension"}
        return True, extension

    async def _reject_authentication(self, websocket: WebSocket) -> None:
        try:
            await websocket.send_json(
                {
                    "type": "authenticated",
                    "ok": False,
                    "error": {"code": "unauthorized", "message": "Authentication failed"},
                }
            )
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
        try:
            await websocket.close(code=1008, reason="authentication failed")
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass

    async def handle_websocket(self, websocket: WebSocket) -> None:
        await websocket.accept()
        authenticated, extension = await self._authenticate(websocket)
        if not authenticated:
            return
        connection = await self._register(websocket, extension)
        try:
            await connection.send_json(
                {"type": "authenticated", "ok": True, "extension": extension}
            )
            await self._receive_messages(connection)
        except ExtensionUnavailableError:
            pass
        finally:
            await self._unregister(connection)

    async def _receive_messages(self, connection: ExtensionConnection) -> None:
        while not connection.closed:
            try:
                message = await connection.websocket.receive_json()
            except WebSocketDisconnect:
                return
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            except (OSError, RuntimeError):
                return
            if not isinstance(message, dict):
                continue
            if message.get("type") == "event":
                await self._record_event(message)
                continue
            if message.get("type") == "response":
                await self._resolve_response(connection.pending, message, connection._pending_lock)

    async def _resolve_response(
        self,
        pending: dict[str, asyncio.Future[Any]],
        message: dict[str, Any],
        pending_lock: asyncio.Lock,
    ) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, str):
            return
        async with pending_lock:
            future = pending.pop(request_id, None)
        if future is None or future.done():
            return
        if message.get("ok") is True:
            future.set_result(message.get("result"))
        else:
            error = message.get("error")
            future.set_exception(ExtensionCommandError(_error_message(error), error))

    async def _record_event(self, message: dict[str, Any]) -> None:
        event = {"event": message.get("event"), "data": message.get("data", {})}
        async with self._state_lock:
            self._last_event = event
            self._events.append(event)

    async def poll_http(self, report: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Wait briefly for the next command from the HTTP-polling extension."""
        async with self._state_lock:
            self._http_connected = True
            self._http_last_seen = time.monotonic()
            if report is not None:
                self._record_http_report_locked(report)
        try:
            return await asyncio.wait_for(self._http_queue.get(), timeout=_HTTP_POLL_SECONDS)
        except TimeoutError:
            return None
        finally:
            async with self._state_lock:
                self._http_last_seen = time.monotonic()

    def _record_http_report_locked(self, report: dict[str, Any]) -> None:
        identity = report.get("extension")
        if isinstance(identity, dict) and identity:
            self._http_extension = {
                key: identity[key] for key in ("name", "version") if key in identity
            } or {"name": "firefox-extension"}
            self._http_extension["transport"] = "http"
        diagnostics = report.get("diagnostics")
        self._http_diagnostics = {
            key: diagnostics[key]
            for key in _HTTP_DIAGNOSTIC_KEYS
            if isinstance(diagnostics, dict) and key in diagnostics
        }
        # This runs on every 1 Hz poll, so only report a change. A per-poll log
        # line would bury everything else in the server log.
        if (
            self._http_diagnostics.get("websocket") is not True
            and self._http_diagnostics != self._http_diagnostics_logged
        ):
            # The extension is up but its socket is not. Surface the reason once
            # instead of leaving `transport: http` to be guessed at.
            self._http_diagnostics_logged = dict(self._http_diagnostics)
            logger.info(
                "Extension is polling over HTTP, not WebSocket",
                extra={"client": "http", "diagnostics": self._http_diagnostics},
            )

    async def submit_http_response(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict) or not isinstance(message.get("id"), str):
            logger.warning("Received invalid extension response", extra={"client": "http"})
            raise InvalidExtensionMessageError("Invalid extension response")
        request_id = message.get("id")
        async with self._state_lock:
            self._http_connected = True
            self._http_last_seen = time.monotonic()
        logger.debug(
            "Received extension response",
            extra={"client": "http", "request_id": request_id},
        )
        await self._resolve_response(self._http_pending, message, self._http_pending_lock)
        logger.debug("Resolved extension response", extra={"client": "http"})

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if method not in _COMMANDS:
            raise ValueError(f"Unsupported extension method: {method}")
        request_id = str(uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        message = {
            "type": "request",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        async with self._state_lock:
            connection = self._connection
            websocket_connected = connection is not None and not connection.closed
            http_connected = self._http_available_locked()
            if not websocket_connected and not http_connected:
                logger.warning("Command rejected: extension unavailable", extra={"method": method})
                raise ExtensionUnavailableError()
            if websocket_connected and connection is not None:
                pending = connection.pending
                pending_lock = connection._pending_lock
                transport = "websocket"
            else:
                pending = self._http_pending
                pending_lock = self._http_pending_lock
                transport = "http"
            async with pending_lock:
                pending[request_id] = future
            if not websocket_connected:
                self._http_queue.put_nowait(message)

        logger.debug(
            "Sent command to extension",
            extra={"method": method, "transport": transport},
        )

        try:
            if websocket_connected and connection is not None:
                await connection.send_json(message)
            return await asyncio.wait_for(future, timeout=self.settings.timeout)
        except TimeoutError as exc:
            raise ExtensionCommandTimeoutError() from exc
        finally:
            async with pending_lock:
                pending_value = pending.pop(request_id, None)
            if pending_value is not None and not pending_value.done():
                pending_value.cancel()

    async def close(self) -> None:
        async with self._state_lock:
            connection = self._connection
            self._connection = None
            self._http_connected = False
            self._http_pending.clear()
            while True:
                try:
                    self._http_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        if connection is not None:
            await connection.close()


def _error_message(error: Any) -> str:
    if isinstance(error, dict):
        for key in ("message", "detail", "error"):
            value = error.get(key)
            if isinstance(value, str) and value:
                return value
    if isinstance(error, str) and error:
        return error
    return "Firefox extension command failed"


__all__ = ["ExtensionConnection", "FirefoxBridge"]
