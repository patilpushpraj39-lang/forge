from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from starlette.requests import Request

import forge_api.main as api_main
from forge_api.auth import (
    ClerkReviewerAuthenticator,
    DisabledReviewerAuthenticator,
    ReviewerAuthenticationError,
    ReviewerAuthenticationUnavailable,
    ReviewerAuthorizationError,
    create_reviewer_authenticator,
)


def request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/auth/me",
            "raw_path": b"/auth/me",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer fixture-token")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
    )


class ReviewerAuthenticationTests(unittest.TestCase):
    def authenticator(self, state):
        def verifier(received_request, options):
            self.assertEqual(received_request.url.path, "/auth/me")
            self.assertEqual(options.authorized_parties, ["http://localhost:3000"])
            self.assertEqual(options.accepts_token, ["session_token"])
            return state

        return ClerkReviewerAuthenticator(
            "-----BEGIN PUBLIC KEY-----\nfixture\n-----END PUBLIC KEY-----",
            ("http://localhost:3000",),
            frozenset({"user_allowed"}),
            verifier=verifier,
        )

    def test_clerk_identity_is_verified_and_canonicalized(self) -> None:
        principal = self.authenticator(
            SimpleNamespace(
                is_signed_in=True,
                payload={"sub": "user_allowed", "azp": "http://localhost:3000"},
            )
        ).authenticate(request())

        self.assertEqual(principal.actor_id, "clerk:user_allowed")
        self.assertEqual(principal.subject, "user_allowed")
        self.assertEqual(principal.provider, "clerk")

    def test_real_clerk_verifier_checks_signature_and_authorized_party(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        now = int(datetime.now(UTC).timestamp())

        def signed_request(authorized_party: str) -> Request:
            token = jwt.encode(
                {
                    "sub": "user_allowed",
                    "sid": "sess_fixture",
                    "iat": now,
                    "nbf": now - 1,
                    "exp": now + 60,
                    "azp": authorized_party,
                },
                private_key,
                algorithm="RS256",
            )
            authenticated = request()
            authenticated.scope["headers"] = [
                (b"authorization", f"Bearer {token}".encode("ascii"))
            ]
            return authenticated

        authenticator = ClerkReviewerAuthenticator(
            public_key,
            ("http://localhost:3000",),
            frozenset({"user_allowed"}),
        )

        principal = authenticator.authenticate(
            signed_request("http://localhost:3000")
        )
        self.assertEqual(principal.actor_id, "clerk:user_allowed")
        with self.assertRaises(ReviewerAuthenticationError):
            authenticator.authenticate(signed_request("https://attacker.example"))

    def test_missing_pending_and_unapproved_sessions_are_denied(self) -> None:
        cases = (
            (
                SimpleNamespace(is_signed_in=False, payload=None),
                ReviewerAuthenticationError,
            ),
            (
                SimpleNamespace(
                    is_signed_in=True,
                    payload={"sub": "user_allowed", "sts": "pending"},
                ),
                ReviewerAuthenticationError,
            ),
            (
                SimpleNamespace(
                    is_signed_in=True,
                    payload={"sub": "user_not_allowed"},
                ),
                ReviewerAuthorizationError,
            ),
        )
        for state, expected_error in cases:
            with self.subTest(expected_error=expected_error.__name__):
                with self.assertRaises(expected_error):
                    self.authenticator(state).authenticate(request())

    def test_authentication_is_fail_closed_until_configured(self) -> None:
        with self.assertRaises(ReviewerAuthenticationUnavailable):
            DisabledReviewerAuthenticator().authenticate(request())

        with patch.dict(os.environ, {"FORGE_AUTH_MODE": "disabled"}, clear=True):
            configured = create_reviewer_authenticator()
        self.assertIsInstance(configured, DisabledReviewerAuthenticator)

    def test_clerk_configuration_requires_origin_key_and_reviewer_allowlist(self) -> None:
        environment = {
            "FORGE_AUTH_MODE": "clerk",
            "CLERK_JWT_KEY": (
                "-----BEGIN PUBLIC KEY-----\\nfixture\\n-----END PUBLIC KEY-----"
            ),
            "CLERK_AUTHORIZED_PARTIES": "http://localhost:3000",
            "FORGE_REVIEWER_IDS": "user_allowed",
        }
        with patch.dict(os.environ, environment, clear=True):
            configured = create_reviewer_authenticator()
        self.assertIsInstance(configured, ClerkReviewerAuthenticator)
        self.assertIn("\nfixture\n", configured.options.jwt_key)

        for missing in (
            "CLERK_JWT_KEY",
            "CLERK_AUTHORIZED_PARTIES",
            "FORGE_REVIEWER_IDS",
        ):
            invalid = dict(environment)
            del invalid[missing]
            with self.subTest(missing=missing):
                with patch.dict(os.environ, invalid, clear=True):
                    with self.assertRaises(RuntimeError):
                        create_reviewer_authenticator()

        for name, value in (
            ("CLERK_AUTHORIZED_PARTIES", "http://attacker.example"),
            ("CLERK_AUTHORIZED_PARTIES", "https://forge.example/path"),
            ("FORGE_REVIEWER_IDS", "not a valid subject"),
        ):
            invalid = dict(environment)
            invalid[name] = value
            with self.subTest(invalid=name, value=value):
                with patch.dict(os.environ, invalid, clear=True):
                    with self.assertRaises(RuntimeError):
                        create_reviewer_authenticator()

    def test_api_maps_authentication_failures_without_trusting_the_browser(self) -> None:
        class RejectingAuthenticator:
            def __init__(self, error):
                self.error = error

            def authenticate(self, received_request):
                del received_request
                raise self.error

        original = api_main.reviewer_authenticator
        client = TestClient(api_main.app)
        try:
            for error, expected_status in (
                (ReviewerAuthenticationUnavailable("not configured"), 503),
                (ReviewerAuthenticationError("invalid session"), 401),
                (ReviewerAuthorizationError("not a reviewer"), 403),
            ):
                with self.subTest(expected_status=expected_status):
                    api_main.reviewer_authenticator = RejectingAuthenticator(error)
                    response = client.get(
                        "/auth/me",
                        headers={"authorization": "Bearer browser-claim"},
                    )
                    self.assertEqual(response.status_code, expected_status)
        finally:
            api_main.reviewer_authenticator = original


if __name__ == "__main__":
    unittest.main()
