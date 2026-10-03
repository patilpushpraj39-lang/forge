"""Record-only review contract. This is not an execution or publication grant."""
from __future__ import annotations

import re
from typing import Any

EVENT_TYPE = "readonly_preview_decided"
SCOPE = "readonly-preview-record-only-v1"
EFFECTS = {
    "execution_enabled": False,
    "authorizes_source_upload": False,
    "authorizes_paid_generation": False,
    "authorizes_file_changes": False,
    "authorizes_github_writes": False,
}


def validate_record(run: dict, actor: str, payload: dict) -> None:
    expected = {"scope", "decision", "plan_sha256", "snapshot_sha256", "base_sha",
                "repository", "decision_key_sha256", *EFFECTS}
    if set(payload) != expected or payload["scope"] != SCOPE:
        raise ValueError("Invalid record-only review scope.")
    if payload["decision"] not in ("approved", "rejected") or not actor.startswith("clerk:"):
        raise ValueError("A verified reviewer and review decision are required.")
    if any(payload[key] is not False for key in EFFECTS):
        raise ValueError("Record-only review cannot authorize execution.")
    for key in ("plan_sha256", "snapshot_sha256", "decision_key_sha256"):
        if not isinstance(payload[key], str) or not re.fullmatch(r"[0-9a-f]{64}", payload[key]):
            raise ValueError("Invalid review fingerprint.")
    if (run["state"] != "COMPLETED" or run.get("evaluated_patch_hash")
            or run.get("evaluation_verdict_hash")
            or run.get("source_snapshot_sha256") != payload["snapshot_sha256"]
            or run.get("base_sha") != payload["base_sha"]
            or f"{run.get('repository_owner')}/{run.get('repository_name')}" != payload["repository"]):
        raise ValueError("Source reference changed; reload the preview.")


def existing_decision(events: list[dict[str, Any]], actor: str, payload: dict) -> dict | None:
    for event in events:
        old = event["payload"]
        if old.get("plan_sha256") == payload["plan_sha256"]:
            if event["actor"] == actor and old == payload:
                return event
            raise ValueError("This exact preview already has a review decision.")
    return None
