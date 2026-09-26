from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

import forge_api.main as api_main
from forge_api.auth import ReviewerPrincipal
from forge_agent_core import RunState, RunStore
from forge_publisher import (
    InstallationSummary,
    MaterializedRepository,
    RepositorySummary,
)
from forge_sandbox_controller import LocalArtifactStore, LocalSandboxController


class ApiRunContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.original_store = api_main.store
        self.original_controller = api_main.controller
        self.original_github_catalog = api_main.github_catalog
        api_main.store = RunStore(self.root / "forge.db")
        api_main.controller = LocalSandboxController(
            LocalArtifactStore(self.root / "artifacts")
        )
        api_main.github_catalog = None

    def tearDown(self) -> None:
        api_main.store = self.original_store
        api_main.controller = self.original_controller
        api_main.github_catalog = self.original_github_catalog
        self.temporary_directory.cleanup()

    def test_api_persists_normalized_objective_and_default_budgets(self) -> None:
        created = api_main.create_run(
            api_main.CreateRunRequest(
                repository_path=str(self.repository),
                objective="  Fix the parser and add a regression test.  ",
            )
        )

        self.assertEqual(
            created["objective"],
            "Fix the parser and add a regression test.",
        )
        self.assertEqual(created["budget_total_tokens"], 50_000)
        self.assertEqual(created["budget_cost_microusd"], 1_000_000)
        self.assertEqual(created["budget_patch_attempts"], 5)

    def test_api_rejects_empty_objective_and_unsafe_budget(self) -> None:
        with self.assertRaises(ValidationError):
            api_main.CreateRunRequest(
                repository_path=str(self.repository), objective="   "
            )
        with self.assertRaises(ValidationError):
            api_main.CreateRunRequest(
                repository_path=str(self.repository),
                objective="Inspect the repository.",
                budgets={"cost_microusd": 100_000_001},
            )

    def test_api_records_repository_approval_and_publication_contract(self) -> None:
        reviewer = ReviewerPrincipal(
            "clerk:user_reviewer_1", "user_reviewer_1", "clerk"
        )
        created = api_main.create_run(
            api_main.CreateRunRequest(
                repository_path=str(self.repository),
                objective="Fix the parser.",
                github_repository={
                    "owner": "octo-org",
                    "name": "fixture",
                    "installation_id": 42,
                    "base_ref": "main",
                    "base_sha": "c" * 40,
                },
            )
        )
        run_id = str(created["run_id"])
        self.assertEqual(created["repository_owner"], "octo-org")
        self.assertEqual(created["base_sha"], "c" * 40)
        api_main.store.claim_next_run("api-test-worker")
        api_main.store.transition(
            run_id,
            RunState.SNAPSHOTTING,
            RunState.EXECUTING,
            "worker",
            lease_owner="api-test-worker",
        )
        api_main.store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.EVALUATING,
            "evaluator",
            lease_owner="api-test-worker",
        )
        api_main.store.transition(
            run_id,
            RunState.EVALUATING,
            RunState.AWAITING_APPROVAL,
            "evaluator",
            {"diff_hash": "a" * 64, "verdict_hash": "b" * 64},
            lease_owner="api-test-worker",
        )

        approval = api_main.grant_approval(
            run_id,
            api_main.GrantApprovalRequest(
                patch_hash="a" * 64,
                evaluation_verdict_hash="b" * 64,
                approval_key="approval-api-test-0001",
            ),
            reviewer,
        )
        with self.assertRaises(api_main.HTTPException) as denied:
            api_main.publish_run(
                run_id,
                api_main.PublishRunRequest(
                    approval_id=str(approval["approval_id"]),
                    patch_hash="a" * 64,
                    title="Forge fix",
                    body="Verified by Forge.",
                    idempotency_key="publication-api-denied-0001",
                ),
                ReviewerPrincipal("clerk:user_other", "user_other", "clerk"),
            )
        self.assertEqual(denied.exception.status_code, 403)
        publication = api_main.publish_run(
            run_id,
            api_main.PublishRunRequest(
                approval_id=str(approval["approval_id"]),
                patch_hash="a" * 64,
                title="Forge fix",
                body="Verified by Forge.",
                idempotency_key="publication-api-test-0001",
            ),
            reviewer,
        )

        self.assertEqual(approval["actor_id"], "clerk:user_reviewer_1")
        self.assertEqual(publication["status"], "PENDING")
        self.assertEqual(api_main.store.get_run(run_id)["state"], "PUBLISHING")

        with self.assertRaises(ValidationError):
            api_main.GrantApprovalRequest(
                patch_hash="a" * 64,
                evaluation_verdict_hash="b" * 64,
                approval_key="approval-forged-actor-0001",
                actor_id="attacker:chosen-identity",
            )

    def test_review_returns_only_integrity_checked_run_evidence(self) -> None:
        created = api_main.create_run(
            api_main.CreateRunRequest(
                repository_path=str(self.repository),
                objective="Update the readme.",
                github_repository={
                    "owner": "octo-org",
                    "name": "fixture",
                    "installation_id": 42,
                    "base_ref": "main",
                    "base_sha": "c" * 40,
                },
            )
        )
        run_id = str(created["run_id"])
        patch = b"diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-before\n+after\n"
        patch_artifact = api_main.controller.store_artifact(
            patch, "text/x-diff; charset=utf-8"
        )
        api_main.store.claim_next_run("api-test-worker")
        api_main.store.transition(
            run_id,
            RunState.SNAPSHOTTING,
            RunState.EXECUTING,
            "worker",
            lease_owner="api-test-worker",
        )
        api_main.store.transition(
            run_id,
            RunState.EXECUTING,
            RunState.EVALUATING,
            "evaluator",
            lease_owner="api-test-worker",
        )
        api_main.store.append_event(
            run_id,
            "evaluation_started",
            "evaluator",
            {"patch_artifact": patch_artifact.to_dict()},
        )
        api_main.store.append_event(
            run_id,
            "evaluation_completed",
            "evaluator",
            {
                "verdict": "passed",
                "verdict_hash": "b" * 64,
                "checks": [
                    {
                        "check_id": "tests",
                        "kind": "command",
                        "status": "passed",
                        "required": True,
                    }
                ],
                "rubric": {"passed": True, "scores": {"correctness": 4}},
                "failure_codes": [],
            },
        )
        api_main.store.transition(
            run_id,
            RunState.EVALUATING,
            RunState.AWAITING_APPROVAL,
            "evaluator",
            {
                "diff_hash": patch_artifact.sha256,
                "verdict_hash": "b" * 64,
                "changed_paths": ["README.md"],
            },
            lease_owner="api-test-worker",
        )

        review = api_main.get_run_review(run_id)

        self.assertEqual(review["patch"], patch.decode())
        self.assertEqual(review["patch_hash"], patch_artifact.sha256)
        self.assertEqual(review["changed_paths"], ["README.md"])
        self.assertEqual(review["repository"]["base_sha"], "c" * 40)

    def test_github_catalog_creates_run_from_immutable_snapshot(self) -> None:
        class FakeCatalog:
            def list_installations(self):
                return (
                    InstallationSummary(42, "octo-org", "Organization", "selected"),
                )

            def list_repositories(self, installation_id):
                self.installation_id = installation_id
                return (
                    RepositorySummary(
                        "octo-org", "fixture", "octo-org/fixture", "main", True
                    ),
                )

            def materialize_selected_repository(
                self, installation_id, owner, name, base_ref, destination
            ):
                destination.mkdir()
                (destination / "README.md").write_text(
                    "immutable source\n", encoding="utf-8"
                )
                return MaterializedRepository(
                    installation_id,
                    owner,
                    name,
                    base_ref,
                    "a" * 40,
                    "b" * 40,
                    1,
                    17,
                    destination,
                )

        api_main.github_catalog = FakeCatalog()
        reviewer = ReviewerPrincipal(
            "clerk:user_reviewer_1", "user_reviewer_1", "clerk"
        )

        installations = api_main.list_github_installations(reviewer)
        repositories = api_main.list_github_repositories(42, reviewer)
        created = api_main.create_github_run(
            api_main.CreateGitHubRunRequest(
                installation_id=42,
                owner="octo-org",
                name="fixture",
                base_ref="main",
                objective="Inspect the immutable source.",
            ),
            reviewer,
        )

        self.assertEqual(installations[0]["account_login"], "octo-org")
        self.assertEqual(repositories[0]["full_name"], "octo-org/fixture")
        self.assertEqual(created["base_sha"], "a" * 40)
        self.assertEqual(len(created["source_snapshot_sha256"]), 64)
        self.assertEqual(
            created["repository_path"], "github://octo-org/fixture@" + "a" * 40
        )
        events = api_main.store.list_events(str(created["run_id"]))
        self.assertEqual(events[-1]["event_type"], "github_source_ingested")


if __name__ == "__main__":
    unittest.main()
