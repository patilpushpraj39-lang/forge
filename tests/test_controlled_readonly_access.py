"""Controlled request policy with real local JWT verification, not live hosting."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import forge_api.main as api
from forge_agent_core import RunStore
from forge_agent_core.readonly_review import EFFECTS, EVENT_TYPE
from forge_api.access import create_api_application, validate_api_profile
from forge_api.auth import ClerkReviewerAuthenticator, ReviewerAuthenticationUnavailable
from test_api_access import ORIGIN, controlled_environment
from test_readonly_preview_api import ReadonlyPreviewFixture


class ControlledReadonlyAccessTests(ReadonlyPreviewFixture):
    def setUp(self):
        super().setUp()
        # Ephemeral signing keys/identities only; the SDK verifier is not mocked.
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        self.authenticator = Mock(wraps=ClerkReviewerAuthenticator(
            public, (ORIGIN,), frozenset({"fixture", "other_approved"}),
        ))
        self.application = create_api_application(
            validate_api_profile(controlled_environment()), self.authenticator,
        )
        self.application.router.routes.extend(
            route for route in api.app.routes if isinstance(route, APIRoute)
        )
        self.client = TestClient(self.application)
        self.addCleanup(self.client.close)
        self.headers = self.signed_headers()

    def signed_headers(self, *, key=None, **changes):
        now = int(datetime.now(UTC).timestamp())
        payload = {"sub": "fixture", "sid": "sess_fixture", "azp": ORIGIN,
                   "iat": now, "nbf": now - 1, "exp": now + 60, **changes}
        token = jwt.encode(payload, key or self.key, algorithm="RS256")
        return {"Authorization": "Bearer " + token}

    def assert_denied_before_evidence(self, headers, status, body=None, query=None):
        events = self.store.list_events(self.run_id)
        self.controller.reset_mock()
        with patch.object(self.store, "get_run", side_effect=AssertionError("Unauthorized evidence read")), \
             patch.object(self.store, "record_readonly_preview_decision", side_effect=AssertionError("Unauthorized decision")):
            for method, suffix in (("GET", ""), ("POST", "/decision")):
                response = self.client.request(method, self.path + suffix,
                    params={"repository": "octo/fixture", **(query or {})},
                    headers=headers, json=body or {})
                self.assertEqual(response.status_code, status)
                self.assertNotIn("README", response.text)
                self.assertNotIn("snapshot", response.text)
                self.assertNotIn("clerk:", response.text)
                if headers.get("Authorization"):
                    self.assertNotIn(headers["Authorization"].removeprefix("Bearer "), response.text)
        self.controller.read_artifact.assert_not_called()
        self.assertEqual(self.store.list_events(self.run_id), events)

    def test_approved_session_loads_canonical_preview_and_is_verified_once(self):
        before = self.store.get_run(self.run_id)
        self.authenticator.reset_mock()
        preview = self.preview()
        self.assertEqual(preview["plan"], self.preparer.prepare_plan(
            before, self.root, "octo/fixture", snapshot_content=self.content))
        self.assertEqual({key: preview[key] for key in EFFECTS}, EFFECTS)
        self.assertEqual(self.store.get_run(self.run_id), before)
        self.authenticator.authenticate.assert_called_once()

    def test_missing_session_and_url_or_body_credentials_cannot_bypass_gate(self):
        body = self.body()
        token = self.headers["Authorization"].removeprefix("Bearer ")
        self.assert_denied_before_evidence({}, 401,
            body={**body, "token": token, "actor_id": "clerk:fixture"},
            query={"token": token, "actor_id": "clerk:fixture"})
        self.assert_denied_before_evidence({"Authorization": "Bearer malformed-fixture"}, 401)

    def test_unapproved_expired_future_pending_wrong_origin_and_bad_signature_are_denied(self):
        now = int(datetime.now(UTC).timestamp())
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases = [
            (self.signed_headers(sub="not_approved"), 403),
            (self.signed_headers(exp=now - 120), 401),
            (self.signed_headers(nbf=now + 120), 401),
            (self.signed_headers(sts="pending"), 401),
            (self.signed_headers(azp="https://attacker.example"), 401),
            (self.signed_headers(key=other_key), 401),
        ]
        for index, (headers, status) in enumerate(cases):
            with self.subTest(case=index):
                self.assert_denied_before_evidence(headers, status)

    def test_saved_receipt_restores_after_reopen_but_is_not_visible_to_unapproved_session(self):
        before = self.store.get_run(self.run_id)
        body = self.body()
        response = self.client.post(self.path + "/decision", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200)
        receipt = response.json()
        self.assertEqual(receipt["actor"], "clerk:fixture")
        self.assertEqual({key: receipt[key] for key in EFFECTS}, EFFECTS)
        self.assert_denied_before_evidence(self.signed_headers(sub="not_approved"), 403, body=body)
        self.assert_denied_before_evidence({}, 401, body=body)
        with patch.object(api, "store", RunStore(self.database)):
            self.assertEqual(self.preview()["decision"], receipt)
        self.assertEqual(self.client.post(self.path + "/decision", headers=self.headers, json=body).json(), receipt)
        self.assertEqual(self.store.get_run(self.run_id), before)
        events = [event for event in self.store.list_events(self.run_id) if event["event_type"] == EVENT_TYPE]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], "user")
        self.assertEqual(events[0]["payload"]["actor_id"], "clerk:fixture")
        with closing(sqlite3.connect(self.database)) as connection:
            for table in ("approvals", "publication_jobs"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual({name for name, _, _ in self.controller.mock_calls}, {"read_artifact"})

    def test_browser_cannot_supply_identity_or_upgrade_review_to_execution(self):
        body = self.body()
        for changes in ({"actor": "clerk:spoof"}, {"actor_id": "clerk:spoof"},
                        {"execution_enabled": True}, {"allow_source_upload": True},
                        {"allow_one_paid_generation": True}, {"scope": "readonly-readme-openai-v1"}):
            with self.subTest(changes=changes):
                response = self.client.post(self.path + "/decision", headers=self.headers, json={**body, **changes})
                self.assertEqual(response.status_code, 422)
        self.assertFalse(any(e["event_type"] == EVENT_TYPE for e in self.store.list_events(self.run_id)))

    def test_other_approved_reviewer_cannot_replay_or_overwrite_an_existing_decision(self):
        body = self.body()
        receipt = self.client.post(self.path + "/decision", headers=self.headers, json=body).json()
        other_headers = self.signed_headers(sub="other_approved")
        # Single-tenant access: approved reviewers share read access, not identity.
        preview = self.client.get(self.path, params={"repository": "octo/fixture"}, headers=other_headers)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["decision"], receipt)
        for changes in ({}, {"decision": "rejected"}):
            self.assertEqual(self.client.post(self.path + "/decision", headers=other_headers,
                json={**body, **changes}).status_code, 409)
        self.assertEqual(self.preview()["decision"], receipt)
        self.assertEqual(sum(e["event_type"] == EVENT_TYPE for e in self.store.list_events(self.run_id)), 1)

    def test_unavailable_verifier_blocks_reads_and_decisions_before_evidence(self):
        self.authenticator.authenticate.side_effect = ReviewerAuthenticationUnavailable("private-fixture-detail")
        self.assert_denied_before_evidence(self.headers, 503)

    def test_cors_preflight_is_not_authentication_and_does_not_read_evidence(self):
        self.authenticator.reset_mock()
        headers = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "authorization,content-type"}
        response = self.client.options(self.path + "/decision", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        hostile = self.client.options(self.path + "/decision", headers={**headers, "Origin": "https://attacker.example"})
        self.assertEqual(hostile.status_code, 400)
        self.authenticator.authenticate.assert_not_called()
        self.controller.read_artifact.assert_not_called()
        self.assert_denied_before_evidence({"Origin": ORIGIN}, 401)
