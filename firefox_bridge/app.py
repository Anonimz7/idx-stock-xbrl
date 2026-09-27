"""FastAPI application for the local Firefox bridge."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .bridge import FirefoxBridge
from .config import Settings, get_settings
from .errors import BridgeError, ExtensionCommandError
from .logging_config import get_logger
from .models import (
    ClickRequest,
    DownloadRequest,
    FillRequest,
    NavigateRequest,
    OpenTabRequest,
    SelectDropdownRequest,
    SnapshotRequest,
    TextRequest,
)

_API_PREFIX = "/api/v1"

logger = get_logger()


def _tokens_match(candidate: str, expected: str) -> bool:
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _model_params(model: Any) -> dict[str, Any]:
    return model.model_dump(exclude_none=True)


def create_app(
    settings: Settings | None = None,
    bridge: FirefoxBridge | None = None,
) -> FastAPI:
    """Create an isolated bridge application.

    Supplying ``settings`` and ``bridge`` keeps tests independent from process
    environment and the user's token file.
    """
    active_settings = settings or (bridge.settings if bridge is not None else get_settings())
    active_bridge = bridge or FirefoxBridge(active_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        close = getattr(active_bridge, "close", None)
        if close is not None:
            await close()

    app = FastAPI(
        title="Firefox Bridge",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^moz-extension://.*$",
        allow_credentials=False,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.settings = active_settings
    app.state.bridge = active_bridge

    @app.exception_handler(BridgeError)
    async def bridge_error_handler(_: Any, exc: BridgeError) -> JSONResponse:
        content: dict[str, Any] = {"detail": str(exc)}
        if isinstance(exc, ExtensionCommandError) and exc.error is not None:
            content["error"] = jsonable_encoder(exc.error)
        return JSONResponse(status_code=exc.status_code, content=content)

    bearer = HTTPBearer(auto_error=False)

    async def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not _tokens_match(credentials.credentials, active_settings.token)
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{_API_PREFIX}/status")
    async def status(_: None = Depends(require_token)) -> dict[str, Any]:
        return await active_bridge.status()

    @app.get(f"{_API_PREFIX}/tabs")
    async def tabs(_: None = Depends(require_token)) -> Any:
        return await active_bridge.request("tabs.list", {})

    @app.post(f"{_API_PREFIX}/tabs/open")
    async def open_tab(
        payload: OpenTabRequest,
        _: None = Depends(require_token),
    ) -> Any:
        return await active_bridge.request("tabs.open", _model_params(payload))

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/activate")
    async def activate_tab(
        tab_id: str,
        _: None = Depends(require_token),
    ) -> Any:
        return await active_bridge.request("tabs.activate", {"tab_id": tab_id})

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/close")
    async def close_tab(
        tab_id: str,
        _: None = Depends(require_token),
    ) -> Any:
        return await active_bridge.request("tabs.close", {"tab_id": tab_id})

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/navigate")
    async def navigate(
        tab_id: str,
        payload: NavigateRequest,
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload)}
        return await active_bridge.request("tab.navigate", params)

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/snapshot")
    async def snapshot(
        tab_id: str,
        payload: SnapshotRequest | None = Body(default=None),
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload or SnapshotRequest())}
        return await active_bridge.request("tab.snapshot", params)

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/click")
    async def click(
        tab_id: str,
        payload: ClickRequest,
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload)}
        return await active_bridge.request("tab.click", params)

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/fill")
    async def fill(
        tab_id: str,
        payload: FillRequest,
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload)}
        return await active_bridge.request("tab.fill", params)

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/text")
    async def page_text(
        tab_id: str,
        payload: TextRequest | None = Body(default=None),
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload or TextRequest())}
        return await active_bridge.request("tab.text", params)

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/select-dropdown")
    async def select_dropdown(
        tab_id: str,
        payload: SelectDropdownRequest,
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload)}
        return await active_bridge.request("tab.select_dropdown", params)

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/select-dropdown-option")
    async def select_dropdown_option(
        tab_id: str,
        payload: SelectDropdownRequest,
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload)}
        return await active_bridge.request("tab.select_dropdown_option", params)

    @app.post(f"{_API_PREFIX}/tabs/{{tab_id}}/download")
    async def download(
        tab_id: str,
        payload: DownloadRequest,
        _: None = Depends(require_token),
    ) -> Any:
        params = {"tab_id": tab_id, **_model_params(payload)}
        return await active_bridge.request("tab.download", params)

    @app.post("/extension/poll")
    async def extension_poll(
        payload: dict[str, Any] | None = Body(default=None),
        _: None = Depends(require_token),
    ) -> dict[str, Any]:
        command = await active_bridge.poll_http(payload)
        logger.debug("Extension long-poll completed", extra={"client": "http"})
        return {"command": command}

    @app.post("/extension/response")
    async def extension_response(
        payload: dict[str, Any] = Body(...),
        _: None = Depends(require_token),
    ) -> dict[str, bool]:
        await active_bridge.submit_http_response(payload)
        logger.debug("Received command response from extension", extra={"client": "http"})
        return {"ok": True}

    @app.websocket("/extension")
    async def extension_socket(websocket: WebSocket) -> None:
        logger.info("WebSocket connection attempting authentication")
        await active_bridge.handle_websocket(websocket)

    return app


__all__ = ["create_app"]
