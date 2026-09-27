"""Typed errors raised by the extension connection layer."""

from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    """Base class for errors that can be mapped to an HTTP response."""

    status_code = 500


class ExtensionUnavailableError(BridgeError):
    status_code = 503

    def __init__(self, message: str = "Firefox extension is not connected") -> None:
        super().__init__(message)


class ExtensionCommandError(BridgeError):
    status_code = 502

    def __init__(self, message: str, error: Any = None) -> None:
        super().__init__(message)
        self.error = error


class ExtensionCommandTimeoutError(BridgeError):
    status_code = 504

    def __init__(self, message: str = "Firefox extension command timed out") -> None:
        super().__init__(message)


class InvalidExtensionMessageError(BridgeError):
    status_code = 502


# Compatibility names for callers that prefer shorter error names.
NoExtensionError = ExtensionUnavailableError
CommandError = ExtensionCommandError
BridgeTimeoutError = ExtensionCommandTimeoutError


__all__ = [
    "BridgeError",
    "BridgeTimeoutError",
    "CommandError",
    "ExtensionCommandError",
    "ExtensionCommandTimeoutError",
    "ExtensionUnavailableError",
    "InvalidExtensionMessageError",
    "NoExtensionError",
]
