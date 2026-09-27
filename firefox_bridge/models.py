"""Pydantic request models used by the bridge REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenTabRequest(RequestModel):
    url: str = Field(min_length=1)
    active: bool | None = None


class NavigateRequest(RequestModel):
    url: str = Field(min_length=1)


class SnapshotRequest(RequestModel):
    max_elements: int | None = Field(default=None, ge=1)


class ClickRequest(RequestModel):
    ref: str | int


class FillRequest(RequestModel):
    ref: str | int
    value: str


class TextRequest(RequestModel):
    max_chars: int | None = Field(default=None, ge=1)


class DownloadRequest(RequestModel):
    ref: str | int
    filename: str | None = None


class SelectDropdownRequest(RequestModel):
    ref: str | int
    value: str


class AuthenticateMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    token: str
    extension: Any | None = None


class ExtensionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    id: str
    ok: bool
    result: Any | None = None
    error: Any | None = None


__all__ = [
    "AuthenticateMessage",
    "ClickRequest",
    "DownloadRequest",
    "ExtensionResponse",
    "FillRequest",
    "NavigateRequest",
    "OpenTabRequest",
    "RequestModel",
    "SnapshotRequest",
    "TextRequest",
]
