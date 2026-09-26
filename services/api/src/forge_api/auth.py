from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from clerk_backend_api import AuthenticateRequestOptions, authenticate_request
from starlette.requests import Request


_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


class ReviewerAuthenticationError(RuntimeError):
    pass


class ReviewerAuthorizationError(RuntimeError):
    pass


class ReviewerAuthenticationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ReviewerPrincipal:
    actor_id: str
    subject: str
    provider: str

    def to_dict(self) -> dict[str, str]:
        return {
            "actor_id": self.actor_id,
            "subject": self.subject,
            "provider": self.provider,
        }


class ReviewerAuthenticator(Protocol):
    def authenticate(self, request: Request) -> ReviewerPrincipal: ...


class DisabledReviewerAuthenticator:
    def authenticate(self, request: Request) -> ReviewerPrincipal:
        del request
        raise ReviewerAuthenticationUnavailable(
            "reviewer authentication is not configured"
        )


class ClerkReviewerAuthenticator:
    """Verifies Clerk session JWTs and applies Forge's reviewer allowlist."""

    def __init__(
        self,
        jwt_key: str,
        authorized_parties: tuple[str, ...],
        reviewer_subjects: frozenset[str],
        *,
        secret_key: str | None = None,
        verifier: Callable[[Any, AuthenticateRequestOptions], Any] = authenticate_request,
    ) -> None:
        if "BEGIN PUBLIC KEY" not in jwt_key:
            raise ValueError("CLERK_JWT_KEY must be a PEM public key")
        if not authorized_parties:
            raise ValueError("CLERK_AUTHORIZED_PARTIES must not be empty")
        if not reviewer_subjects:
            raise ValueError("FORGE_REVIEWER_IDS must not be empty")
        for party in authorized_parties:
            parsed = urlsplit(party)
            local_http = (
                parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
            if (
                (parsed.scheme != "https" and not local_http)
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "CLERK_AUTHORIZED_PARTIES must contain exact HTTPS origins "
                    "or loopback HTTP origins"
                )
        if any(not _SUBJECT.fullmatch(subject) for subject in reviewer_subjects):
            raise ValueError("FORGE_REVIEWER_IDS contains an invalid Clerk subject")
        self.options = AuthenticateRequestOptions(
            secret_key=secret_key,
            jwt_key=jwt_key,
            authorized_parties=list(authorized_parties),
            accepts_token=["session_token"],
        )
        self.reviewer_subjects = reviewer_subjects
        self.verifier = verifier

    def authenticate(self, request: Request) -> ReviewerPrincipal:
        state = self.verifier(request, self.options)
        if not state.is_signed_in or not isinstance(state.payload, dict):
            raise ReviewerAuthenticationError("a valid reviewer session is required")
        subject = state.payload.get("sub")
        if not isinstance(subject, str) or not _SUBJECT.fullmatch(subject):
            raise ReviewerAuthenticationError("reviewer session subject is invalid")
        if state.payload.get("sts") == "pending":
            raise ReviewerAuthenticationError("reviewer session is pending")
        if subject not in self.reviewer_subjects:
            raise ReviewerAuthorizationError(
                "signed-in user is not authorized to approve Forge patches"
            )
        return ReviewerPrincipal(f"clerk:{subject}", subject, "clerk")


def _csv_environment(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if len(values) != len(set(values)):
        raise RuntimeError(f"{name} contains duplicate values")
    return values


def create_reviewer_authenticator() -> ReviewerAuthenticator:
    mode = os.environ.get("FORGE_AUTH_MODE", "disabled").strip().casefold()
    if mode == "disabled":
        return DisabledReviewerAuthenticator()
    if mode != "clerk":
        raise RuntimeError("FORGE_AUTH_MODE must be 'disabled' or 'clerk'")
    jwt_key = os.environ.get("CLERK_JWT_KEY", "").replace("\\n", "\n").strip()
    authorized_parties = _csv_environment("CLERK_AUTHORIZED_PARTIES")
    reviewer_subjects = frozenset(_csv_environment("FORGE_REVIEWER_IDS"))
    try:
        return ClerkReviewerAuthenticator(
            jwt_key,
            authorized_parties,
            reviewer_subjects,
            secret_key=os.environ.get("CLERK_SECRET_KEY") or None,
        )
    except ValueError as error:
        raise RuntimeError(str(error)) from error
