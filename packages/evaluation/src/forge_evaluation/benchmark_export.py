from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from .benchmark import BenchmarkRecord, BenchmarkSplit, BenchmarkVerdict


_TERMINAL_EXPORT_STATES = {
    "AWAITING_APPROVAL",
    "PUBLISHING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
}
_BUDGET_REASONS = {
    "token_budget_exhausted",
    "cost_budget_exhausted",
    "wall_time_exhausted",
    "model_step_budget_exhausted",
    "tool_call_budget_exhausted",
    "patch_attempt_budget_exhausted",
}
_POLICY_FAILURES = {
    "patch_empty",
    "patch_invalid",
    "patch_too_large",
    "too_many_files",
    "too_many_additions",
    "too_many_deletions",
    "forbidden_path",
    "protected_test_changed",
    "policy_denied",
}
_BUDGET_FAILURES = {
    "check_timed_out",
    "check_output_limit",
    "check_resource_limit",
}
_TASK_INVALID_FAILURES = {"hidden_test_missing", "hidden_test_invalid"}
_INFRA_FAILURES = {"manifest_mismatch", "internal_error"}


class RunEvidenceStore(Protocol):
    def get_run(self, run_id: str) -> dict[str, Any]: ...

    def list_events(
        self, run_id: str, after_sequence: int = 0
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class BenchmarkTask:
    benchmark_version: str
    task_id: str
    task_version: str
    task_manifest_digest: str
    split: BenchmarkSplit
    category: str
    language: str


@dataclass(frozen=True)
class BenchmarkExperiment:
    experiment_id: str
    configuration_digest: str
    code_revision: str


def export_benchmark_record(
    store: RunEvidenceStore,
    run_id: str,
    task: BenchmarkTask,
    experiment: BenchmarkExperiment,
) -> BenchmarkRecord:
    """Build one benchmark record exclusively from durable run evidence."""

    run = store.get_run(run_id)
    if str(run.get("run_id")) != run_id:
        raise ValueError("run store returned evidence for a different run")
    state = str(run.get("state"))
    if state not in _TERMINAL_EXPORT_STATES:
        raise ValueError("benchmark evidence can be exported only after a verdict")

    events = tuple(store.list_events(run_id))
    _validate_event_stream(events, run_id)
    created = _first_event(events, "run_created")
    agent = _last_event(events, "agent_stopped")
    evaluation_started = _last_event(events, "evaluation_started")
    evaluation_completed = _last_event(events, "evaluation_completed")

    if state in {"AWAITING_APPROVAL", "PUBLISHING"} and evaluation_completed is None:
        raise ValueError("approval states require completed evaluation evidence")
    if state == "COMPLETED" and agent is None:
        raise ValueError("completed smoke-command runs are not benchmark agent runs")

    evaluation_manifest_digest = _evaluation_manifest_digest(
        evaluation_started, evaluation_completed
    )
    verdict, failure_code, regression_free = _classify(
        state, agent, evaluation_completed, events
    )
    cost_microusd, tool_calls, patch_attempts = _agent_metrics(agent)
    end = evaluation_completed or _last_terminal_transition(events) or agent
    if end is None:
        end = events[-1]

    return BenchmarkRecord(
        schema_version=1,
        benchmark_version=task.benchmark_version,
        experiment_id=experiment.experiment_id,
        configuration_digest=experiment.configuration_digest,
        code_revision=experiment.code_revision,
        task_id=task.task_id,
        task_version=task.task_version,
        task_split=task.split,
        category=task.category,
        language=task.language,
        run_id=run_id,
        task_manifest_digest=task.task_manifest_digest,
        evaluation_manifest_digest=evaluation_manifest_digest,
        verdict=verdict,
        failure_code=failure_code,
        regression_free=regression_free,
        patch_attempts=patch_attempts,
        duration_ms=_duration_ms(created, end),
        cost_microusd=cost_microusd,
        tool_calls=tool_calls,
    )


def _validate_event_stream(
    events: tuple[dict[str, Any], ...], run_id: str
) -> None:
    if not events:
        raise ValueError("run has no durable event evidence")
    sequences: list[int] = []
    for event in events:
        if str(event.get("run_id")) != run_id:
            raise ValueError("event stream contains a different run ID")
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
            raise ValueError("event sequence must be a positive integer")
        if not isinstance(event.get("event_type"), str):
            raise ValueError("event type is missing")
        if not isinstance(event.get("payload"), dict):
            raise ValueError("event payload must be an object")
        _event_time(event)
        sequences.append(sequence)
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise ValueError("event sequence must be unique and increasing")
    if events[0]["event_type"] != "run_created":
        raise ValueError("event stream must begin with run_created")


def _classify(
    state: str,
    agent: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    events: tuple[dict[str, Any], ...],
) -> tuple[BenchmarkVerdict, str | None, bool]:
    if evaluation is not None:
        payload = _payload(evaluation)
        evaluation_verdict = str(payload.get("verdict", ""))
        failures = _failure_codes(payload.get("failure_codes"))
        if evaluation_verdict == "passed":
            if failures:
                raise ValueError("passed evaluation contains failure codes")
            return BenchmarkVerdict.SOLVED, None, True
        if not failures:
            raise ValueError("failed evaluation must contain failure codes")
        primary = next(
            (item for item in failures if item != "rubric_failed"), failures[0]
        )
        if any(item in _TASK_INVALID_FAILURES for item in failures):
            verdict = BenchmarkVerdict.TASK_INVALID
        elif evaluation_verdict == "error" or any(
            item in _INFRA_FAILURES for item in failures
        ):
            verdict = BenchmarkVerdict.INFRA_INVALID
        elif any(item in _POLICY_FAILURES for item in failures):
            verdict = BenchmarkVerdict.POLICY_VIOLATION
        elif any(item in _BUDGET_FAILURES for item in failures):
            verdict = BenchmarkVerdict.BUDGET_EXHAUSTED
        else:
            verdict = BenchmarkVerdict.UNSOLVED
        return verdict, primary, _public_regression_checks_passed(payload)

    if agent is not None:
        payload = _payload(agent)
        stop_reason = _identifier(payload.get("stop_reason"), "invalid_stop_reason")
        if stop_reason in _BUDGET_REASONS:
            return BenchmarkVerdict.BUDGET_EXHAUSTED, stop_reason, False
        if stop_reason == "policy_denied":
            return BenchmarkVerdict.POLICY_VIOLATION, stop_reason, False
        if stop_reason in {"provider_error", "cancelled"}:
            return BenchmarkVerdict.INFRA_INVALID, stop_reason, False
        terminal_reason = _terminal_reason(events)
        failure = terminal_reason or (
            "no_patch" if state == "COMPLETED" else stop_reason
        )
        return BenchmarkVerdict.UNSOLVED, failure, False

    failure = _terminal_reason(events) or "missing_agent_evidence"
    return BenchmarkVerdict.INFRA_INVALID, failure, False


def _agent_metrics(agent: dict[str, Any] | None) -> tuple[int, int, int]:
    if agent is None:
        return 0, 0, 0
    payload = _payload(agent)
    values = []
    for name in ("cost_microusd", "tool_calls", "patch_attempts"):
        value = payload.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"agent_stopped.{name} is missing or invalid")
        values.append(value)
    return values[0], values[1], values[2]


def _evaluation_manifest_digest(
    started: dict[str, Any] | None,
    completed: dict[str, Any] | None,
) -> str | None:
    values: list[str] = []
    for event in (started, completed):
        if event is None:
            continue
        digest = _payload(event).get("manifest_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("evaluation manifest digest is missing or invalid")
        values.append(digest)
    if len(set(values)) > 1:
        raise ValueError("evaluation events disagree on the manifest digest")
    return values[0] if values else None


def _failure_codes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        return ()
    result: list[str] = []
    for item in value:
        result.append(_identifier(item, "invalid_failure_code"))
    return tuple(dict.fromkeys(result))


def _public_regression_checks_passed(payload: Mapping[str, Any]) -> bool:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise ValueError("evaluation check evidence is missing")
    required_public = [
        item
        for item in checks
        if isinstance(item, dict)
        and item.get("required") is True
        and str(item.get("kind")) != "hidden_test"
    ]
    return bool(required_public) and all(
        str(item.get("status")) == "passed" for item in required_public
    )


def _duration_ms(start: dict[str, Any], end: dict[str, Any]) -> int:
    seconds = (_event_time(end) - _event_time(start)).total_seconds()
    if seconds < 0:
        raise ValueError("benchmark event timestamps move backwards")
    return int(round(seconds * 1000))


def _event_time(event: Mapping[str, Any]) -> datetime:
    raw = event.get("occurred_at")
    if not isinstance(raw, str):
        raise ValueError("event timestamp is missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("event timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("event timestamp must include a timezone")
    return parsed


def _terminal_reason(events: tuple[dict[str, Any], ...]) -> str | None:
    transition = _last_terminal_transition(events)
    if transition is None:
        return None
    value = _payload(transition).get("reason")
    return None if value is None else _identifier(value, "run_failed")


def _last_terminal_transition(
    events: tuple[dict[str, Any], ...]
) -> dict[str, Any] | None:
    transitions = [
        event
        for event in events
        if event["event_type"] == "state_changed"
        and str(_payload(event).get("to_state")) in _TERMINAL_EXPORT_STATES
    ]
    return transitions[-1] if transitions else None


def _first_event(
    events: tuple[dict[str, Any], ...], event_type: str
) -> dict[str, Any]:
    event = next((item for item in events if item["event_type"] == event_type), None)
    if event is None:
        raise ValueError(f"event stream is missing {event_type}")
    return event


def _last_event(
    events: tuple[dict[str, Any], ...], event_type: str
) -> dict[str, Any] | None:
    matches = [item for item in events if item["event_type"] == event_type]
    return matches[-1] if matches else None


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("event payload must be an object")
    return payload


def _identifier(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        normalized = re.sub(r"[^a-z0-9_-]+", "_", value.casefold()).strip("_")
        if normalized and normalized[0].isalpha():
            return normalized[:64]
    return fallback
