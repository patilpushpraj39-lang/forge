from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from forge_agent_core import (
    ApprovalError,
    ApprovalEvidenceMismatchError,
    ApprovalExpiredError,
    PublicationConflictError,
    RepositoryTarget,
    RunState,
    RunStore,
)


PATCH_HASH = "a" * 64
VERDICT_HASH = "b" * 64
BASE_SHA = "c" * 40


class ApprovalPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.database = self.root / "forge.db"
        self.store = RunStore(self.database)
        self.target = RepositoryTarget("octo-org", "fixture", 42, "main", BASE_SHA)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def awaiting_run(self, *, with_target: bool = True) -> str:
        created = self.store.create_run(
            str(self.repository),
            "Fix the bounded fixture.",
            repository=self.target if with_target else None,
        )
        run_id = str(created["run_id"])
        claimed = self.store.claim_next_run("worker-one")
        self.assertIsNotNone(claimed)
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
        self.store.transition(
            run_id,
            RunState.EVALUATING,
            RunState.AWAITING_APPROVAL,
            "evaluator",
            {"diff_hash": PATCH_HASH, "verdict_hash": VERDICT_HASH},
            lease_owner="worker-one",
        )
        return run_id

    def grant(self, run_id: str, *, key: str = "approval-request-0001") -> dict:
        return self.store.grant_approval(
            run_id,
            PATCH_HASH,
            VERDICT_HASH,
            "user:reviewer-1",
            key,
        )

    def test_approval_binds_exact_evidence_target_actor_action_and_expiry(self) -> None:
        run_id = self.awaiting_run()
        approval = self.grant(run_id)
        repeated = self.grant(run_id)

        self.assertEqual(repeated["approval_id"], approval["approval_id"])
        self.assertEqual(approval["patch_hash"], PATCH_HASH)
        self.assertEqual(approval["evaluation_verdict_hash"], VERDICT_HASH)
        self.assertEqual(approval["repository_owner"], self.target.owner)
        self.assertEqual(approval["repository_name"], self.target.name)
        self.assertEqual(approval["installation_id"], self.target.installation_id)
        self.assertEqual(approval["base_ref"], self.target.base_ref)
        self.assertEqual(approval["base_sha"], self.target.base_sha)
        self.assertEqual(approval["actor_id"], "user:reviewer-1")
        self.assertEqual(approval["action"], "create_pull_request")
        self.assertGreater(
            datetime.fromisoformat(approval["expires_at"]), datetime.now(UTC)
        )
        events = self.store.list_events(run_id)
        self.assertEqual(
            sum(event["event_type"] == "approval_granted" for event in events),
            1,
        )

        with self.assertRaises(ApprovalEvidenceMismatchError):
            self.store.grant_approval(
                run_id,
                "d" * 64,
                VERDICT_HASH,
                "user:reviewer-1",
                "approval-request-0002",
            )

    def test_run_without_github_target_cannot_be_approved(self) -> None:
        run_id = self.awaiting_run(with_target=False)
        with self.assertRaises(ApprovalError):
            self.grant(run_id)

    def test_expired_or_stale_approval_cannot_publish(self) -> None:
        expired_run = self.awaiting_run()
        expired = self.grant(expired_run)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
                (
                    (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                    expired["approval_id"],
                ),
            )
            connection.commit()
        with self.assertRaises(ApprovalExpiredError):
            self.store.request_publication(
                expired_run,
                expired["approval_id"],
                PATCH_HASH,
                "Forge fix",
                "Verified by Forge.",
                "publication-request-0001",
            )

        stale_run = self.awaiting_run()
        stale = self.grant(stale_run, key="approval-request-0003")
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """
                UPDATE runs SET evaluated_patch_hash = ? WHERE run_id = ?
                """,
                ("e" * 64, stale_run),
            )
            connection.commit()
        with self.assertRaises(ApprovalEvidenceMismatchError):
            self.store.request_publication(
                stale_run,
                stale["approval_id"],
                PATCH_HASH,
                "Forge fix",
                "Verified by Forge.",
                "publication-request-0002",
            )

    def test_publication_is_durable_leased_and_exactly_once(self) -> None:
        run_id = self.awaiting_run()
        approval = self.grant(run_id)
        publication = self.store.request_publication(
            run_id,
            approval["approval_id"],
            PATCH_HASH,
            "Forge fix",
            "Verified by Forge.",
            "publication-request-0003",
        )
        repeated = self.store.request_publication(
            run_id,
            approval["approval_id"],
            PATCH_HASH,
            "Forge fix",
            "Verified by Forge.",
            "publication-request-0003",
        )
        self.assertEqual(repeated["publication_id"], publication["publication_id"])
        self.assertEqual(self.store.get_run(run_id)["state"], RunState.PUBLISHING)

        claimed = self.store.claim_publication("publisher-one", lease_seconds=1)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        completed = self.store.complete_publication(
            claimed["publication_id"],
            "publisher-one",
            17,
            "https://github.com/octo-org/fixture/pull/17",
            "f" * 40,
        )
        repeated_completion = self.store.complete_publication(
            claimed["publication_id"],
            "publisher-one",
            17,
            "https://github.com/octo-org/fixture/pull/17",
            "f" * 40,
        )

        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(
            repeated_completion["publication_id"], completed["publication_id"]
        )
        self.assertEqual(self.store.get_run(run_id)["state"], RunState.COMPLETED)
        events = self.store.list_events(run_id)
        self.assertEqual(
            sum(event["event_type"] == "pull_request_created" for event in events),
            1,
        )
        with self.assertRaises(PublicationConflictError):
            self.store.complete_publication(
                claimed["publication_id"],
                "publisher-one",
                18,
                "https://github.com/octo-org/fixture/pull/18",
                "0" * 40,
            )

    def test_expired_publication_lease_is_recovered_without_new_job(self) -> None:
        run_id = self.awaiting_run()
        approval = self.grant(run_id)
        publication = self.store.request_publication(
            run_id,
            approval["approval_id"],
            PATCH_HASH,
            "Forge fix",
            "",
            "publication-request-0004",
        )
        first = self.store.claim_publication("publisher-one", lease_seconds=0.05)
        self.assertIsNotNone(first)
        time.sleep(0.08)
        recovered = self.store.claim_publication("publisher-two", lease_seconds=1)

        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["publication_id"], publication["publication_id"])
        self.assertEqual(recovered["attempt"], 2)
        self.assertEqual(recovered["lease_owner"], "publisher-two")

    def test_transient_publication_failure_has_durable_retry_delay(self) -> None:
        run_id = self.awaiting_run()
        approval = self.grant(run_id)
        publication = self.store.request_publication(
            run_id,
            approval["approval_id"],
            PATCH_HASH,
            "Forge fix",
            "",
            "publication-request-0005",
        )
        claimed = self.store.claim_publication("publisher-one")
        self.assertIsNotNone(claimed)

        retriable = self.store.fail_publication(
            publication["publication_id"],
            "publisher-one",
            "github_unavailable",
            permanent=False,
        )

        self.assertEqual(retriable["status"], "PENDING")
        self.assertGreater(
            datetime.fromisoformat(retriable["available_at"]), datetime.now(UTC)
        )
        self.assertIsNone(self.store.claim_publication("publisher-two"))

    def test_cancellation_is_rejected_after_publication_starts(self) -> None:
        run_id = self.awaiting_run()
        approval = self.grant(run_id)
        self.store.request_publication(
            run_id,
            approval["approval_id"],
            PATCH_HASH,
            "Forge fix",
            "",
            "publication-request-0006",
        )

        with self.assertRaises(PublicationConflictError):
            self.store.request_cancellation(run_id)
        self.assertIsNone(
            self.store.get_run(run_id)["cancellation_requested_at"]
        )


if __name__ == "__main__":
    unittest.main()
