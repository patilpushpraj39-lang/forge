from __future__ import annotations

import hashlib
import json
import base64
import tempfile
import unittest
from pathlib import Path
from datetime import UTC, datetime
from typing import Any

from forge_agent_core import RepositoryTarget, RunState, RunStore
from forge_publisher import (
    GitHubChange,
    GitHubAppInstallationTokenProvider,
    GitHubPullRequestPublisher,
    GitHubResponse,
    PublisherStaleBaseError,
    PublisherTransientError,
    PullRequestResult,
    StaticInstallationTokenProvider,
    run_once,
)
from forge_sandbox_controller import LocalArtifactStore, LocalSandboxController


BASE_SHA = "c" * 40
HEAD_SHA = "d" * 40


class InMemoryGitHubTransport:
    def __init__(self) -> None:
        self.base_sha = BASE_SHA
        self.branches: dict[str, str] = {}
        self.pulls: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.lose_first_pull_response = False
        self.pull_posts = 0

    def request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> GitHubResponse:
        self.requests.append((method, path, body))
        if path == "/repos/octo-org/fixture" and method == "GET":
            return response(200, {"permissions": {"push": True}})
        if path.endswith("/git/ref/heads/main") and method == "GET":
            return response(200, {"object": {"sha": self.base_sha}})
        if path.endswith(f"/git/commits/{BASE_SHA}") and method == "GET":
            return response(200, {"tree": {"sha": "1" * 40}})
        if path.endswith("/git/blobs") and method == "POST":
            return response(201, {"sha": stable_sha(body)})
        if path.endswith("/git/trees") and method == "POST":
            return response(201, {"sha": stable_sha(body)})
        if path.endswith("/git/commits") and method == "POST":
            return response(201, {"sha": stable_sha(body)})
        if "/git/ref/heads/forge%2F" in path and method == "GET":
            branch = path.rsplit("/", 1)[-1].replace("%2F", "/")
            if branch not in self.branches:
                return response(404, {"message": "Not Found"})
            return response(200, {"object": {"sha": self.branches[branch]}})
        if path.endswith("/git/refs") and method == "POST":
            assert body is not None
            branch = str(body["ref"]).removeprefix("refs/heads/")
            self.branches[branch] = str(body["sha"])
            return response(201, {"ref": body["ref"], "object": {"sha": body["sha"]}})
        if path.endswith("/pulls") and method == "GET":
            head = "" if query is None else query.get("head", "")
            branch = head.split(":", 1)[-1]
            return response(
                200,
                [item for item in self.pulls if item["head"]["ref"] == branch],
            )
        if path.endswith("/pulls") and method == "POST":
            assert body is not None
            self.pull_posts += 1
            branch = str(body["head"])
            item = {
                "number": 17,
                "html_url": "https://github.com/octo-org/fixture/pull/17",
                "head": {"ref": branch, "sha": self.branches[branch]},
            }
            self.pulls.append(item)
            if self.lose_first_pull_response:
                self.lose_first_pull_response = False
                raise PublisherTransientError("simulated lost response")
            return response(201, item)
        return response(500, {"message": f"unhandled {method} {path}"})


class FakePublisher:
    def __init__(self, *, stale: bool = False, transient: bool = False) -> None:
        self.stale = stale
        self.transient = transient
        self.changes: tuple[GitHubChange, ...] = ()

    def prepare_branch(self, publication, changes, heartbeat):
        heartbeat()
        self.changes = changes
        if self.stale:
            raise PublisherStaleBaseError("base changed")
        if self.transient:
            raise PublisherTransientError("network unavailable")
        return HEAD_SHA

    def ensure_pull_request(self, publication, head_sha, heartbeat):
        heartbeat()
        return PullRequestResult(
            17,
            "https://github.com/octo-org/fixture/pull/17",
            head_sha,
        )


class TokenTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, dict[str, Any] | None]] = []

    def request(self, method, path, token, *, body=None, query=None):
        self.calls.append((method, path, token, body))
        return response(
            201,
            {
                "token": "ghs_test_installation_token",
                "expires_at": "2026-09-26T01:00:00Z",
            },
        )


class PublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        (self.repository / "README.md").write_bytes(b"before\n")
        self.store = RunStore(self.root / "forge.db")
        self.controller = LocalSandboxController(
            LocalArtifactStore(self.root / "artifacts")
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def queued_publication(self, suffix: str = "0001") -> tuple[str, dict]:
        snapshot = self.controller.create(self.repository)
        try:
            assert snapshot.snapshot_artifact is not None
            patch = (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1 @@\n"
                "-before\n"
                "+after\n"
            ).encode("utf-8")
            patch_artifact = self.controller.store_artifact(
                patch, "text/x-diff; charset=utf-8"
            )
            created = self.store.create_run(
                str(self.repository),
                "Update the README.",
                repository=RepositoryTarget(
                    "octo-org", "fixture", 42, "main", BASE_SHA
                ),
            )
            run_id = str(created["run_id"])
            self.store.claim_next_run("worker-one")
            self.store.transition(
                run_id,
                RunState.SNAPSHOTTING,
                RunState.EXECUTING,
                "worker",
                lease_owner="worker-one",
            )
            self.store.transition(
                run_id,
                RunState.EXECUTING,
                RunState.EVALUATING,
                "evaluator",
                lease_owner="worker-one",
            )
            self.store.append_event(
                run_id,
                "evaluation_started",
                "evaluator",
                {
                    "snapshot_artifact": snapshot.snapshot_artifact.to_dict(),
                    "patch_artifact": patch_artifact.to_dict(),
                },
            )
            self.store.transition(
                run_id,
                RunState.EVALUATING,
                RunState.AWAITING_APPROVAL,
                "evaluator",
                {
                    "diff_hash": patch_artifact.sha256,
                    "verdict_hash": "b" * 64,
                },
                lease_owner="worker-one",
            )
            approval = self.store.grant_approval(
                run_id,
                patch_artifact.sha256,
                "b" * 64,
                "user:reviewer-1",
                f"approval-publisher-{suffix}",
            )
            publication = self.store.request_publication(
                run_id,
                str(approval["approval_id"]),
                patch_artifact.sha256,
                "Update README",
                "Verified by Forge.",
                f"publication-publisher-{suffix}",
            )
            return run_id, publication
        finally:
            self.controller.destroy(snapshot.sandbox_id)

    def test_service_reconstructs_exact_patch_and_completes(self) -> None:
        run_id, _ = self.queued_publication()
        github = FakePublisher()

        self.assertTrue(
            run_once(
                self.store,
                self.controller,
                github,
                publisher_id="publisher-one",
            )
        )

        self.assertEqual(self.store.get_run(run_id)["state"], RunState.COMPLETED)
        self.assertEqual(len(github.changes), 1)
        self.assertEqual(github.changes[0].path, "README.md")
        assert github.changes[0].content is not None
        self.assertEqual(github.changes[0].content.splitlines(), [b"after"])
        self.assertEqual(
            (self.repository / "README.md").read_text(encoding="utf-8"),
            "before\n",
        )

    def test_stale_base_is_permanent_and_transient_error_requeues(self) -> None:
        stale_run, _ = self.queued_publication("0002")
        self.assertTrue(
            run_once(
                self.store,
                self.controller,
                FakePublisher(stale=True),
                publisher_id="publisher-one",
            )
        )
        self.assertEqual(self.store.get_run(stale_run)["state"], RunState.FAILED)

        transient_run, publication = self.queued_publication("0003")
        self.assertTrue(
            run_once(
                self.store,
                self.controller,
                FakePublisher(transient=True),
                publisher_id="publisher-one",
            )
        )
        self.assertEqual(
            self.store.get_run(transient_run)["state"], RunState.PUBLISHING
        )
        recovered = self.store.get_publication(str(publication["publication_id"]))
        self.assertEqual(recovered["status"], "PENDING")

    def test_github_retry_recovers_branch_and_lost_pull_response(self) -> None:
        transport = InMemoryGitHubTransport()
        gateway = GitHubPullRequestPublisher(
            StaticInstallationTokenProvider("test-token"), transport
        )
        publication = {
            "publication_id": "publication-1",
            "installation_id": 42,
            "repository_owner": "octo-org",
            "repository_name": "fixture",
            "base_ref": "main",
            "base_sha": BASE_SHA,
            "branch_name": "forge/run-patch",
            "patch_hash": "a" * 64,
            "pull_request_title": "Update README",
            "pull_request_body": "Verified by Forge.",
            "created_at": "2026-09-26T00:00:00+00:00",
        }
        changes = (GitHubChange("README.md", b"after\n"),)

        first_head = gateway.prepare_branch(publication, changes, lambda: None)
        second_head = gateway.prepare_branch(publication, changes, lambda: None)
        self.assertEqual(first_head, second_head)
        transport.lose_first_pull_response = True
        with self.assertRaises(PublisherTransientError):
            gateway.ensure_pull_request(publication, first_head, lambda: None)
        recovered = gateway.ensure_pull_request(
            publication, first_head, lambda: None
        )

        self.assertEqual(recovered.number, 17)
        self.assertEqual(recovered.head_sha, first_head)
        self.assertEqual(transport.pull_posts, 1)

    def test_github_stale_base_stops_before_any_write(self) -> None:
        transport = InMemoryGitHubTransport()
        transport.base_sha = "e" * 40
        gateway = GitHubPullRequestPublisher(
            StaticInstallationTokenProvider("test-token"), transport
        )
        publication = {
            "publication_id": "publication-2",
            "installation_id": 42,
            "repository_owner": "octo-org",
            "repository_name": "fixture",
            "base_ref": "main",
            "base_sha": BASE_SHA,
            "branch_name": "forge/run-patch",
            "patch_hash": "a" * 64,
            "pull_request_title": "Update README",
            "pull_request_body": "",
            "created_at": "2026-09-26T00:00:00+00:00",
        }

        with self.assertRaises(PublisherStaleBaseError):
            gateway.prepare_branch(
                publication,
                (GitHubChange("README.md", b"after\n"),),
                lambda: None,
            )

        self.assertTrue(all(method == "GET" for method, _, _ in transport.requests))

    def test_github_app_token_uses_signed_jwt_and_cache(self) -> None:
        transport = TokenTransport()
        signed_inputs: list[bytes] = []

        def signer(private_key: bytes, payload: bytes) -> bytes:
            self.assertEqual(private_key, b"test-private-key")
            signed_inputs.append(payload)
            return b"test-signature"

        provider = GitHubAppInstallationTokenProvider(
            "Iv1.test-client",
            "test-private-key",
            transport,
            clock=lambda: datetime(2026, 9, 26, tzinfo=UTC),
            signer=signer,
        )

        first = provider.token_for(42)
        second = provider.token_for(42)
        read_first = provider.read_token_for(42)
        read_second = provider.read_token_for(42)

        self.assertEqual(first, "ghs_test_installation_token")
        self.assertEqual(second, first)
        self.assertEqual(read_first, "ghs_test_installation_token")
        self.assertEqual(read_second, read_first)
        self.assertEqual(len(transport.calls), 2)
        method, path, jwt, body = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/app/installations/42/access_tokens")
        self.assertEqual(body["permissions"]["contents"], "write")
        self.assertEqual(body["permissions"]["pull_requests"], "write")
        read_body = transport.calls[1][3]
        self.assertEqual(
            read_body["permissions"],
            {"contents": "read", "metadata": "read"},
        )
        header, payload, signature = jwt.split(".")
        claims = json.loads(decode_base64url(payload))
        self.assertEqual(claims["iss"], "Iv1.test-client")
        self.assertEqual(claims["iat"], int(datetime(2026, 9, 26, tzinfo=UTC).timestamp()) - 60)
        self.assertEqual(claims["exp"], int(datetime(2026, 9, 26, tzinfo=UTC).timestamp()) + 540)
        self.assertEqual(len(signed_inputs), 2)
        self.assertEqual(signed_inputs[0], f"{header}.{payload}".encode("ascii"))
        self.assertEqual(decode_base64url(signature), b"test-signature")


def response(status: int, data: Any) -> GitHubResponse:
    return GitHubResponse(status, data, {})


def stable_sha(value: Any) -> str:
    return hashlib.sha1(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


if __name__ == "__main__":
    unittest.main()
