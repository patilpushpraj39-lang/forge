from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from forge_sandbox_controller.docker import IMAGE_DIGEST_PATTERN
from forge_sandbox_controller.recovery import validate_cleanup_scope

from .auth import (
    ReviewerAuthenticationError,
    ReviewerAuthenticationUnavailable,
    ReviewerAuthenticator,
    ReviewerAuthorizationError,
    ReviewerPrincipal,
)


@dataclass(frozen=True)
class ApiProfile:
    mode: Literal["development", "controlled"]
    web_origins: tuple[str, ...]


def validate_api_profile(environment: Mapping[str, str] | None = None) -> ApiProfile:
    """Validate configuration before opening databases or loading credentials."""
    values = os.environ if environment is None else environment
    mode = values.get("FORGE_API_MODE", "development").strip().casefold()
    if mode not in {"development", "controlled"}:
        raise RuntimeError("FORGE_API_MODE must be 'development' or 'controlled'")
    origin = values.get("FORGE_WEB_ORIGIN", "").strip()
    if mode == "development":
        origins = {"http://localhost:3000", "http://127.0.0.1:3000"}
        if origin:
            origins.add(origin)
        return ApiProfile("development", tuple(sorted(origins)))

    if values.get("FORGE_AUTH_MODE", "").strip().casefold() != "clerk":
        raise RuntimeError("controlled API requires FORGE_AUTH_MODE=clerk")
    database = values.get("FORGE_DATABASE_URL", "")
    try:
        parsed_database = urlsplit(database)
        valid_database = (
            parsed_database.scheme in {"postgres", "postgresql"}
            and bool(parsed_database.hostname)
            and parsed_database.path not in {"", "/"}
        )
    except ValueError:
        valid_database = False
    if not valid_database:
        raise RuntimeError("controlled API requires a PostgreSQL FORGE_DATABASE_URL")
    if values.get("FORGE_SANDBOX_BACKEND", "").strip().casefold() != "docker":
        raise RuntimeError("controlled API requires FORGE_SANDBOX_BACKEND=docker")
    if not IMAGE_DIGEST_PATTERN.fullmatch(values.get("FORGE_SANDBOX_IMAGE", "")):
        raise RuntimeError("controlled API requires a digest-pinned FORGE_SANDBOX_IMAGE")
    try:
        validate_cleanup_scope(values.get("FORGE_SANDBOX_SCOPE", ""))
    except ValueError as error:
        raise RuntimeError("controlled API requires an explicit FORGE_SANDBOX_SCOPE") from error
    backend = values.get("FORGE_ARTIFACT_BACKEND", "local").strip().casefold()
    if backend == "local":
        if not Path(values.get("FORGE_ARTIFACT_PATH", "")).is_absolute():
            raise RuntimeError("controlled API requires an absolute FORGE_ARTIFACT_PATH")
    elif backend == "s3":
        if not values.get("FORGE_ARTIFACT_BUCKET", "").strip():
            raise RuntimeError("controlled API requires FORGE_ARTIFACT_BUCKET for S3")
    else:
        raise RuntimeError("controlled API artifact backend must be 'local' or 's3'")
    try:
        parsed_origin = urlsplit(origin)
        valid_origin = (
            parsed_origin.scheme == "https" and bool(parsed_origin.hostname)
            and parsed_origin.username is None and parsed_origin.password is None
            and parsed_origin.path == "" and not parsed_origin.query
            and not parsed_origin.fragment and parsed_origin.port != 0
            and not any(character.isspace() for character in origin)
            and "*" not in origin and "\\" not in origin
        )
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise RuntimeError("controlled API requires one exact HTTPS FORGE_WEB_ORIGIN")
    parties = tuple(
        item.strip() for item in values.get("CLERK_AUTHORIZED_PARTIES", "").split(",")
        if item.strip()
    )
    if parties != (origin,):
        raise RuntimeError("controlled API Clerk authorized party must equal FORGE_WEB_ORIGIN")
    return ApiProfile("controlled", (origin,))


def create_api_application(
    profile: ApiProfile, authenticator: ReviewerAuthenticator,
) -> FastAPI:
    app = FastAPI(
        title="Forge API", version="0.1.0-dev",
        docs_url=None if profile.mode == "controlled" else "/docs",
        redoc_url=None if profile.mode == "controlled" else "/redoc",
        openapi_url=None if profile.mode == "controlled" else "/openapi.json",
    )
    app.add_middleware(
        ControlledAccessMiddleware, mode=profile.mode, authenticator=authenticator,
    )
    # Preflights have no credentials. CORS is outside the gate but permits only
    # the configured exact origin, method and headers; it never runs a handler.
    app.add_middleware(
        CORSMiddleware, allow_origins=list(profile.web_origins),
        allow_credentials=False, allow_methods=["GET", "POST"],
        allow_headers=["authorization", "content-type"],
    )
    return app


def authenticate_reviewer(
    request: Request, authenticator: ReviewerAuthenticator,
) -> ReviewerPrincipal:
    cached = getattr(request.state, "forge_verified_reviewer", None)
    if isinstance(cached, ReviewerPrincipal):
        return cached
    try:
        principal = authenticator.authenticate(request)
        if not isinstance(principal, ReviewerPrincipal):
            raise ReviewerAuthenticationUnavailable("verifier returned an invalid identity")
        return principal
    except ReviewerAuthenticationUnavailable as error:
        raise HTTPException(status_code=503, detail="reviewer authentication is unavailable") from error
    except ReviewerAuthenticationError as error:
        raise HTTPException(status_code=401, detail="a valid reviewer session is required") from error
    except ReviewerAuthorizationError as error:
        raise HTTPException(status_code=403, detail="reviewer access is not authorized") from error


class ControlledAccessMiddleware:
    """Default-deny HTTP gate; never buffers or consumes an SSE response."""

    def __init__(
        self, app: ASGIApp, *, mode: str,
        authenticator: ReviewerAuthenticator,
    ) -> None:
        if mode not in {"development", "controlled"}:
            raise ValueError("unknown API access mode")
        self.app = app
        self.mode = mode
        self.authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan" or self.mode == "development":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        path = str(scope.get("path", ""))
        if path == "/health" and scope.get("method") in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        try:
            principal = await run_in_threadpool(
                authenticate_reviewer, request, self.authenticator,
            )
        except HTTPException as error:
            await JSONResponse(
                {"detail": error.detail}, status_code=error.status_code,
            )(scope, receive, send)
            return
        request.state.forge_verified_reviewer = principal
        normalized = path.rstrip("/") or "/"
        if (
            normalized == "/demo" or normalized.startswith("/demo/")
            or (normalized == "/runs" and scope.get("method") == "POST")
        ):
            await JSONResponse(
                {"detail": "development-only route is disabled in controlled mode"},
                status_code=403,
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
