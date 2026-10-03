"""Authenticated preview service; deliberately never imports an AI executor."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from uuid import UUID

from forge_agent_core.readonly_review import EFFECTS, EVENT_TYPE, SCOPE, reviewer_identity
from forge_sandbox_controller import ArtifactRef


def _load_preparer():
    # This repo-local pilot shares the CLI's exact canonical request and checks.
    path = Path(__file__).resolve().parents[4] / "scripts" / "prepare-readonly-pilot.py"
    spec = importlib.util.spec_from_file_location("forge_readonly_preview_preparer", path)
    if spec is None or spec.loader is None:
        raise ValueError("Preview builder unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_preview(store, controller, run_id: str, repository: str) -> dict:
    if str(UUID(run_id)) != run_id:
        raise ValueError("Canonical source run ID required.")
    run = store.get_run(run_id)
    preparer = _load_preparer()
    preparer.validate_source_reference(run, repository)
    size = run.get("source_snapshot_size_bytes")
    if type(size) is not int or not 0 < size <= preparer.MAX_ARCHIVE_BYTES:
        raise ValueError("Bounded source snapshot required.")
    checksum = run.get("source_snapshot_sha256")
    if (not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or run.get("source_snapshot_media_type") != "application/vnd.forge.snapshot+tar"):
        raise ValueError("Valid captured snapshot required.")
    ref = ArtifactRef(run.get("source_snapshot_sha256"), size, run.get("source_snapshot_media_type"))
    content = controller.read_artifact(ref, max_bytes=preparer.MAX_ARCHIVE_BYTES)
    plan = preparer.prepare_plan(run, Path(), repository, snapshot_content=content)
    # Display as plain text, never rendered Markdown or executable instructions.
    readme = json.loads(plan["request"]["input"][0]["content"].split("\n", 1)[1])
    decisions = [event for event in store.list_events(run_id)
                 if event["event_type"] == EVENT_TYPE
                 and event["payload"].get("plan_sha256") == plan["plan_sha256"]]
    return {"scope": SCOPE, "plan": plan, "readme": readme, **EFFECTS,
            "decision": receipt(decisions[-1]) if decisions else None}


def receipt(event: dict) -> dict:
    payload = event["payload"]
    return {"scope": SCOPE, "decision": payload["decision"],
            "plan_sha256": payload["plan_sha256"], "actor": reviewer_identity(event),
            "created_at": event["occurred_at"], "event_id": event["event_id"], **EFFECTS}
