from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

import forge_api.main as api_main
from forge_api.access import create_api_application, validate_api_profile
from forge_api.auth import (
    ClerkReviewerAuthenticator,
    ReviewerAuthenticationError,
    ReviewerAuthenticationUnavailable,
    ReviewerAuthorizationError,
    ReviewerPrincipal,
)


ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://forge.example"


def controlled_environment() -> dict[str, str]:
    return {
        "FORGE_API_MODE": "controlled",
        "FORGE_AUTH_MODE": "clerk",
        "FORGE_DATABASE_URL": "postgresql://fixture:secret@database.example/forge",
        "FORGE_SANDBOX_BACKEND": "docker",
        "FORGE_SANDBOX_IMAGE": "python@sha256:" + "a" * 64,
        "FORGE_SANDBOX_SCOPE": "controlled-test",
        "FORGE_ARTIFACT_PATH": str((ROOT / ".state" / "controlled-test").resolve()),
        "FORGE_WEB_ORIGIN": ORIGIN,
        "CLERK_AUTHORIZED_PARTIES": ORIGIN,
    }


class ApiProfileTests(unittest.TestCase):
    def test_development_default_preserves_both_local_origins(self) -> None:
        profile = validate_api_profile({})
        self.assertEqual(profile.mode, "development")
        self.assertEqual(set(profile.web_origins), {
            "http://localhost:3000", "http://127.0.0.1:3000",
        })

    def test_controlled_profile_accepts_explicit_safe_dependencies(self) -> None:
        profile = validate_api_profile(controlled_environment())
        self.assertEqual(profile.mode, "controlled")
        self.assertEqual(profile.web_origins, (ORIGIN,))
        s3 = dict(controlled_environment(), FORGE_ARTIFACT_BACKEND="s3",
                  FORGE_ARTIFACT_BUCKET="fixture-bucket")
        self.assertEqual(validate_api_profile(s3).mode, "controlled")

    def test_controlled_profile_rejects_unsafe_or_implicit_configuration(self) -> None:
        for field, value in (
            ("FORGE_API_MODE", "production-typo"),
            ("FORGE_AUTH_MODE", "disabled"),
            ("FORGE_DATABASE_URL", ""),
            ("FORGE_DATABASE_URL", "sqlite:///database.db"),
            ("FORGE_DATABASE_URL", "postgresql://secret@[broken/forge"),
            ("FORGE_SANDBOX_BACKEND", "local"),
            ("FORGE_SANDBOX_IMAGE", "python:latest"),
            ("FORGE_SANDBOX_SCOPE", ""),
            ("FORGE_SANDBOX_SCOPE", "*"),
            ("FORGE_ARTIFACT_BACKEND", "unknown"),
            ("FORGE_ARTIFACT_PATH", ".state/artifacts"),
            ("FORGE_WEB_ORIGIN", "http://localhost:3000"),
            ("FORGE_WEB_ORIGIN", "https://forge.example/path"),
            ("FORGE_WEB_ORIGIN", "https://user:secret@forge.example"),
            ("FORGE_WEB_ORIGIN", "https://forge.example:invalid"),
            ("FORGE_WEB_ORIGIN", "https://*.example"),
            ("FORGE_WEB_ORIGIN", "https://forge.example evil"),
            ("CLERK_AUTHORIZED_PARTIES", "https://attacker.example"),
            ("CLERK_AUTHORIZED_PARTIES", ORIGIN + ",http://localhost:3000"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(RuntimeError) as raised:
                    validate_api_profile(dict(controlled_environment(), **{field: value}))
                self.assertNotIn("secret", str(raised.exception))
        with self.assertRaises(RuntimeError):
            validate_api_profile(dict(controlled_environment(),
                                      FORGE_ARTIFACT_BACKEND="s3"))

    def test_unsafe_controlled_import_stops_before_database_or_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="forge-access-import-") as temporary:
            environment = {
                key: value for key, value in os.environ.items()
                if not key.upper().startswith(("FORGE_", "OPENAI_", "CLERK_", "AWS_"))
            }
            environment.update({
                "FORGE_API_MODE": "controlled",
                "FORGE_AUTH_MODE": "disabled",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(
                    str(path) for directory in (ROOT / "packages", ROOT / "services")
                    for path in directory.glob("*/src")
                ),
            })
            result = subprocess.run(
                [sys.executable, "-c", "import forge_api.main"],
                cwd=temporary, env=environment, capture_output=True, timeout=15,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"controlled API requires FORGE_AUTH_MODE=clerk", result.stderr)
            self.assertFalse((Path(temporary) / ".state").exists())


class FixtureAuthenticator:
    """Deterministic verifier for access routing, not a replacement for Clerk."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def authenticate(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if request.headers.get("authorization") != "Bearer fixture-authorized-session":
            raise ReviewerAuthenticationError("untrusted-token-must-not-be-echoed")
        return ReviewerPrincipal("clerk:user_fixture", "user_fixture", "clerk")


class ControlledAccessTests(unittest.TestCase):
    def client(self, authenticator=None, mode="controlled"):
        authenticator = authenticator or FixtureAuthenticator()
        profile = validate_api_profile(
            dict(controlled_environment(), FORGE_API_MODE=mode),
        )
        application = create_api_application(profile, authenticator)
        # Use the real product handlers without copying development-only docs.
        application.router.routes.extend(
            route for route in api_main.app.routes if isinstance(route, APIRoute)
        )
        return TestClient(application), authenticator

    def test_run_data_commands_benchmarks_and_sse_require_authentication(self) -> None:
        client, _ = self.client()
        for method, path in (
            ("GET", "/runs/private-run"),
            ("GET", "/runs/private-run/review"),
            ("GET", "/runs/private-run/events"),
            ("GET", "/runs/private-run/events/stream"),
            ("GET", "/benchmarks/latest"),
            ("GET", "/github/installations"),
            ("GET", "/demo/runs/private-run"),
            ("GET", "/docs"),
            ("POST", "/runs/private-run/cancel"),
            ("POST", "/runs/private-run/approvals"),
            ("POST", "/runs/private-run/publish"),
            ("POST", "/demo/runs"),
        ):
            with self.subTest(path=path):
                response = client.request(method, path)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("untrusted-token", response.text)
        # Merely submitting an actor or token in a URL is not authentication.
        self.assertEqual(client.get(
            "/runs/private-run?token=fixture-authorized-session&actor_id=user_fixture"
        ).status_code, 401)

    def test_only_exact_health_is_an_unauthenticated_liveness_exception(self) -> None:
        client, authenticator = self.client()
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(authenticator.calls, 0)
        self.assertEqual(client.post("/health").status_code, 401)
        self.assertEqual(client.get("/health/other").status_code, 401)

    def test_authenticated_local_run_and_demo_routes_are_blocked_before_writes(self) -> None:
        client, _ = self.client()
        headers = {"authorization": "Bearer fixture-authorized-session"}
        with patch.object(api_main.store, "create_run") as create_run:
            for path in ("/runs", "/runs/", "/demo/runs", "/demo/runs/x/decision"):
                with self.subTest(path=path):
                    response = client.post(path, headers=headers, json={})
                    self.assertEqual(response.status_code, 403)
            self.assertEqual(client.get("/demo/runs/x", headers=headers).status_code, 403)
            create_run.assert_not_called()

    def test_verified_identity_is_reused_by_existing_reviewer_dependency(self) -> None:
        client, authenticator = self.client()
        response = client.get(
            "/auth/me", headers={"authorization": "Bearer fixture-authorized-session"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["actor_id"], "clerk:user_fixture")
        self.assertEqual(authenticator.calls, 1)

    def test_real_signed_sessions_enforce_origin_expiry_and_reviewer_allowlist(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        authenticator = ClerkReviewerAuthenticator(
            public, (ORIGIN,), frozenset({"user_fixture"}),
        )
        client, _ = self.client(authenticator)
        now = int(datetime.now(UTC).timestamp())
        base = {
            "sub": "user_fixture", "sid": "sess_fixture", "azp": ORIGIN,
            "iat": now, "nbf": now - 1, "exp": now + 60,
        }
        for changed, expected in (
            ({}, 200),
            ({"sub": "user_other"}, 403),
            ({"azp": "https://attacker.example"}, 401),
            ({"exp": now - 120}, 401),
        ):
            with self.subTest(changed=changed):
                token = jwt.encode(dict(base, **changed), key, algorithm="RS256")
                response = client.get(
                    "/auth/me", headers={"authorization": "Bearer " + token},
                )
                self.assertEqual(response.status_code, expected)
                self.assertNotIn(token, response.text)

    def test_verifier_unavailability_and_forbidden_reviewers_fail_closed(self) -> None:
        for error, status in (
            (ReviewerAuthenticationUnavailable("secret-value"), 503),
            (ReviewerAuthorizationError("secret-value"), 403),
        ):
            with self.subTest(status=status):
                client, _ = self.client(FixtureAuthenticator(error))
                response = client.get("/runs/private-run")
                self.assertEqual(response.status_code, status)
                self.assertNotIn("secret-value", response.text)

    def test_invalid_identity_and_unexpected_verifier_failure_never_run_handler(self) -> None:
        class BrokenAuthenticator:
            def __init__(self, crashes):
                self.crashes = crashes

            def authenticate(self, request):
                if self.crashes:
                    raise RuntimeError("secret-verifier-detail")
                return None

        for crashes, expected in ((False, 503), (True, 500)):
            with self.subTest(crashes=crashes):
                client, _ = self.client(BrokenAuthenticator(crashes))
                safe_client = TestClient(client.app, raise_server_exceptions=False)
                with patch.object(api_main.store, "get_run") as get_run:
                    response = safe_client.get("/runs/private-run")
                    self.assertEqual(response.status_code, expected)
                    self.assertNotIn("secret-verifier-detail", response.text)
                    get_run.assert_not_called()

    def test_exact_origin_preflight_does_not_execute_a_protected_route(self) -> None:
        client, authenticator = self.client()
        headers = {
            "origin": ORIGIN, "access-control-request-method": "POST",
            "access-control-request-headers": "authorization,content-type",
        }
        response = client.options("/github/runs", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        self.assertEqual(authenticator.calls, 0)
        hostile = client.options("/github/runs", headers=dict(headers, origin="https://attacker.example"))
        self.assertEqual(hostile.status_code, 400)
        self.assertNotIn("access-control-allow-origin", hostile.headers)
        denied = client.get("/runs/private-run", headers={"origin": ORIGIN})
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.headers["access-control-allow-origin"], ORIGIN)

    def test_development_access_does_not_call_the_controlled_authenticator(self) -> None:
        client, authenticator = self.client(mode="development")
        self.assertEqual(client.get("/demo/runs/nonexistent").status_code, 404)
        self.assertEqual(authenticator.calls, 0)

    def test_controlled_application_has_no_schema_or_interactive_docs(self) -> None:
        client, _ = self.client()
        headers = {"authorization": "Bearer fixture-authorized-session"}
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path, headers=headers).status_code, 404)

    def test_authenticated_sse_chunks_are_preserved_and_websockets_denied(self) -> None:
        client, _ = self.client()

        @client.app.get("/finite-test-stream")
        def finite_stream():
            async def chunks():
                yield "data: first\n\n"
                yield "data: second\n\n"
            return StreamingResponse(chunks(), media_type="text/event-stream")

        headers = {"authorization": "Bearer fixture-authorized-session"}
        with client.stream("GET", "/finite-test-stream", headers=headers) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(list(response.iter_lines()), [
                "data: first", "", "data: second", "",
            ])
        with self.assertRaises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/not-a-supported-websocket"):
                pass
        self.assertEqual(closed.exception.code, 1008)


if __name__ == "__main__":
    unittest.main()
