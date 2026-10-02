from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field


SCOPE_LABEL = "forge.cleanup.scope"
DEADLINE_LABEL = "forge.cleanup.deadline"
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
CONTAINER_NAME = re.compile(r"/forge-exec-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},'
    '"labels":{{json .Config.Labels}}}'
)


def validate_cleanup_scope(scope: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", scope):
        raise ValueError("cleanup scope must be 1-64 letters, digits, dots, dashes or underscores")
    return scope


@dataclass
class CleanupReport:
    scope: str
    dry_run: bool
    eligible: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


class DockerSandboxReaper:
    """Remove only scoped, expired Forge command containers; never host files."""

    def __init__(self, scope: str, *, docker_binary: str = "docker", max_candidates: int = 100):
        self.scope = validate_cleanup_scope(scope)
        self.docker_binary = docker_binary
        if not 1 <= max_candidates <= 1000:
            raise ValueError("candidate bound must be between 1 and 1000")
        self.max_candidates = max_candidates

    def reap(self, *, dry_run: bool = True) -> CleanupReport:
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be explicitly true or false")
        report = CleanupReport(self.scope, dry_run)
        checked_at = time.time()
        if not math.isfinite(checked_at) or checked_at <= 0:
            raise RuntimeError("cannot establish a valid cleanup time")
        scan_deadline = time.monotonic() + 60

        def invoke(arguments: list[str]) -> subprocess.CompletedProcess[str]:
            remaining = scan_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("sandbox cleanup scan exceeded its time bound")
            return subprocess.run(
                [self.docker_binary, *arguments],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=min(10, remaining),
            )

        listed = invoke([
            "container", "ls", "--all", "--quiet", "--no-trunc",
            "--filter", "label=forge.sandbox=true",
            "--filter", f"label={SCOPE_LABEL}={self.scope}",
        ])
        if listed.returncode != 0:
            raise RuntimeError("cannot list scoped sandbox containers")
        ids = list(dict.fromkeys(listed.stdout.split()))
        # Validate the entire list before performing any destructive action.
        if len(ids) > self.max_candidates:
            raise RuntimeError("sandbox cleanup candidate bound exceeded")
        if any(not CONTAINER_ID.fullmatch(value) for value in ids):
            raise RuntimeError("Docker returned an invalid full container ID")

        for container_id in ids:
            try:
                inspected = invoke([
                    "container", "inspect", "--format", INSPECT_FORMAT, container_id,
                ])
            except (OSError, TimeoutError, subprocess.TimeoutExpired):
                report.failures[container_id] = "inspection_interrupted"
                break
            if inspected.returncode != 0:
                # It may have auto-removed between listing and inspection.
                report.skipped[container_id] = "inspection_unavailable"
                continue
            try:
                metadata = json.loads(inspected.stdout)
                labels = metadata["labels"]
                deadline_text = labels.get(DEADLINE_LABEL, "")
                if (
                    metadata["id"] != container_id
                    or not CONTAINER_NAME.fullmatch(metadata["name"])
                    or labels.get("forge.sandbox") != "true"
                    or labels.get(SCOPE_LABEL) != self.scope
                    or not isinstance(deadline_text, str)
                    or not re.fullmatch(r"[1-9][0-9]{0,19}", deadline_text)
                ):
                    report.skipped[container_id] = "ownership_or_deadline_invalid"
                    continue
                expired = int(deadline_text) <= checked_at
            except (ValueError, KeyError, TypeError, AttributeError):
                report.skipped[container_id] = "metadata_invalid"
                continue
            if not expired:
                report.skipped[container_id] = "deadline_not_expired"
                continue
            report.eligible.append(container_id)
            if dry_run:
                continue
            # Use the inspected immutable full ID, never a reusable name or prune.
            try:
                removed = invoke(["container", "rm", "--force", container_id])
            except (OSError, TimeoutError, subprocess.TimeoutExpired):
                report.failures[container_id] = "removal_outcome_unknown"
                break
            if removed.returncode != 0:
                report.failures[container_id] = "container_removal_failed"
            else:
                report.removed.append(container_id)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or reap expired Forge containers.")
    parser.add_argument("--scope", required=True, help="Exact deployment cleanup scope")
    parser.add_argument("--execute", action="store_true", help="Remove inspected expired containers")
    args = parser.parse_args()
    try:
        report = DockerSandboxReaper(args.scope).reap(dry_run=not args.execute)
    except (ValueError, RuntimeError, TimeoutError, OSError, subprocess.TimeoutExpired) as error:
        # Do not expose inspect metadata, host environment, or daemon stderr.
        print(json.dumps({"status": "cleanup_failed", "error_type": type(error).__name__}))
        return 1
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
