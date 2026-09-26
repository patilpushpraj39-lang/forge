from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_Z_95 = 1.959963984540054
_CATEGORIES = frozenset({"frontend", "backend", "api", "data", "test"})


class BenchmarkVerdict(StrEnum):
    SOLVED = "solved"
    UNSOLVED = "unsolved"
    BUDGET_EXHAUSTED = "budget_exhausted"
    POLICY_VIOLATION = "policy_violation"
    INFRA_INVALID = "infra_invalid"
    TASK_INVALID = "task_invalid"


class BenchmarkSplit(StrEnum):
    DEVELOPMENT = "development"
    HOLDOUT = "holdout"
    PUBLIC = "public"


@dataclass(frozen=True)
class BenchmarkRecord:
    schema_version: int
    benchmark_version: str
    experiment_id: str
    configuration_digest: str
    code_revision: str
    task_id: str
    task_version: str
    task_split: BenchmarkSplit
    category: str
    language: str
    run_id: str
    task_manifest_digest: str
    evaluation_manifest_digest: str | None
    verdict: BenchmarkVerdict
    failure_code: str | None
    regression_free: bool
    patch_attempts: int
    duration_ms: int
    cost_microusd: int
    tool_calls: int

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported benchmark record schema")
        for name in ("benchmark_version", "task_version", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"{name} must contain 1 to 128 characters")
        for name in ("experiment_id", "task_id", "category", "language"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} must be a lowercase identifier")
        if self.category not in _CATEGORIES:
            raise ValueError("category is not part of the version 1 benchmark taxonomy")
        if not isinstance(self.task_split, BenchmarkSplit):
            raise ValueError("task_split must use the benchmark split taxonomy")
        if not isinstance(self.verdict, BenchmarkVerdict):
            raise ValueError("verdict must use the benchmark verdict taxonomy")
        if not isinstance(self.configuration_digest, str) or not _SHA256.fullmatch(
            self.configuration_digest
        ):
            raise ValueError("configuration_digest must be a SHA-256 digest")
        if not isinstance(self.code_revision, str) or not _SHA1.fullmatch(
            self.code_revision
        ):
            raise ValueError("code_revision must be a full 40-character Git SHA")
        if not isinstance(self.task_manifest_digest, str) or not _SHA256.fullmatch(
            self.task_manifest_digest
        ):
            raise ValueError("task_manifest_digest must be a SHA-256 digest")
        if self.evaluation_manifest_digest is not None and (
            not isinstance(self.evaluation_manifest_digest, str)
            or not _SHA256.fullmatch(self.evaluation_manifest_digest)
        ):
            raise ValueError(
                "evaluation_manifest_digest must be null or a SHA-256 digest"
            )
        for name in ("patch_attempts", "duration_ms", "cost_microusd", "tool_calls"):
            value = getattr(self, name)
            minimum = 0
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
        if not isinstance(self.regression_free, bool):
            raise ValueError("regression_free must be a boolean")
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str)
            or not _IDENTIFIER.fullmatch(self.failure_code)
        ):
            raise ValueError("failure_code must be a lowercase identifier")
        if self.verdict == BenchmarkVerdict.SOLVED:
            if self.failure_code is not None:
                raise ValueError("solved records cannot contain a failure code")
            if not self.regression_free:
                raise ValueError("a solved record must be regression-free")
            if self.evaluation_manifest_digest is None:
                raise ValueError("a solved record requires an evaluation manifest")
        elif self.failure_code is None:
            raise ValueError("non-solved records require a failure code")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BenchmarkRecord:
        expected = {
            "schema_version",
            "benchmark_version",
            "experiment_id",
            "configuration_digest",
            "code_revision",
            "task_id",
            "task_version",
            "task_split",
            "category",
            "language",
            "run_id",
            "task_manifest_digest",
            "evaluation_manifest_digest",
            "verdict",
            "failure_code",
            "regression_free",
            "patch_attempts",
            "duration_ms",
            "cost_microusd",
            "tool_calls",
        }
        unknown = set(value) - expected
        missing = expected - set(value)
        if unknown or missing:
            raise ValueError(
                f"benchmark record fields differ: missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}"
            )
        try:
            return cls(
                **{
                    **dict(value),
                    "task_split": BenchmarkSplit(value["task_split"]),
                    "verdict": BenchmarkVerdict(value["verdict"]),
                }
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid benchmark record: {error}") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_version": self.benchmark_version,
            "experiment_id": self.experiment_id,
            "configuration_digest": self.configuration_digest,
            "code_revision": self.code_revision,
            "task_id": self.task_id,
            "task_version": self.task_version,
            "task_split": self.task_split,
            "category": self.category,
            "language": self.language,
            "run_id": self.run_id,
            "task_manifest_digest": self.task_manifest_digest,
            "evaluation_manifest_digest": self.evaluation_manifest_digest,
            "verdict": self.verdict,
            "failure_code": self.failure_code,
            "regression_free": self.regression_free,
            "patch_attempts": self.patch_attempts,
            "duration_ms": self.duration_ms,
            "cost_microusd": self.cost_microusd,
            "tool_calls": self.tool_calls,
        }


@dataclass(frozen=True)
class BenchmarkSummary:
    value: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.value

    @property
    def digest(self) -> str:
        return _canonical_sha256(self.value)

    def to_markdown(self) -> str:
        counts = self.value["counts"]
        metrics = self.value["metrics"]
        lines = [
            "# Forge Benchmark Card",
            "",
            f"- Benchmark: `{self.value['benchmark_version']}`",
            f"- Experiment: `{self.value['experiment_id']}`",
            f"- Code revision: `{self.value['code_revision']}`",
            f"- Scored tasks: {counts['scored']} of {counts['records']} records",
            f"- Verified solve rate: {_format_rate(metrics['verified_solve_rate'])}",
            f"- Regression-free rate: {_format_rate(metrics['regression_free_rate'])}",
            "- Infrastructure failure rate: "
            f"{_format_rate(metrics['infrastructure_failure_rate'])}",
            f"- Median / p95 duration: {_format_milliseconds(metrics['duration_ms']['median'])} / "
            f"{_format_milliseconds(metrics['duration_ms']['p95'])}",
            f"- Median / p95 cost: {_format_cost(metrics['cost_microusd']['median'])} / "
            f"{_format_cost(metrics['cost_microusd']['p95'])}",
            "- Cost per verified solve: "
            f"{_format_cost(metrics['cost_per_verified_solve_microusd'])}",
            "",
            "## Composition",
            "",
            "| Category | Records | Solved | Solve rate |",
            "|---|---:|---:|---:|",
        ]
        for category, item in self.value["category_breakdown"].items():
            lines.append(
                f"| {category} | {item['records']} | {item['solved']} | "
                f"{_format_rate(item['verified_solve_rate'])} |"
            )
        lines.extend(["", "## Failure distribution", ""])
        if self.value["failure_distribution"]:
            lines.extend(
                f"- `{code}`: {count}"
                for code, count in self.value["failure_distribution"].items()
            )
        else:
            lines.append("- No failures recorded.")
        lines.extend(["", "## Release gates", ""])
        lines.extend(
            f"- {'PASS' if gate['passed'] else 'OPEN'} — {gate['description']}"
            for gate in self.value["release_gates"]
        )
        lines.extend(
            [
                "",
                f"Record-set digest: `{self.value['record_set_digest']}`",
                f"Task-set digest: `{self.value['task_set_digest']}`",
                f"Summary digest: `{self.digest}`",
                "",
            ]
        )
        return "\n".join(lines)


def summarize_benchmark(records: Iterable[BenchmarkRecord]) -> BenchmarkSummary:
    ordered = tuple(sorted(records, key=lambda item: item.task_id))
    if not ordered:
        raise ValueError("at least one benchmark record is required")
    _require_single_configuration(ordered)
    if len({item.task_id for item in ordered}) != len(ordered):
        raise ValueError("a benchmark summary may contain only one record per task")

    task_invalid = tuple(
        item for item in ordered if item.verdict == BenchmarkVerdict.TASK_INVALID
    )
    valid = tuple(item for item in ordered if item not in task_invalid)
    infrastructure_invalid = tuple(
        item for item in valid if item.verdict == BenchmarkVerdict.INFRA_INVALID
    )
    scored = tuple(item for item in valid if item not in infrastructure_invalid)
    solved = tuple(item for item in scored if item.verdict == BenchmarkVerdict.SOLVED)
    categories = sorted({item.category for item in ordered})

    category_breakdown: dict[str, dict[str, Any]] = {}
    for category in categories:
        category_records = tuple(item for item in scored if item.category == category)
        category_solved = sum(
            item.verdict == BenchmarkVerdict.SOLVED for item in category_records
        )
        category_breakdown[category] = {
            "records": len(category_records),
            "solved": category_solved,
            "verified_solve_rate": _rate(category_solved, len(category_records)),
        }

    total_valid_cost = sum(item.cost_microusd for item in valid)
    failure_distribution: dict[str, int] = {}
    for item in ordered:
        if item.failure_code is not None:
            failure_distribution[item.failure_code] = (
                failure_distribution.get(item.failure_code, 0) + 1
            )

    required_categories = _CATEGORIES
    valid_category_counts = {
        category: sum(item.category == category for item in valid)
        for category in required_categories
    }
    release_gates = [
        {
            "gate": "minimum-task-count",
            "passed": len(valid) >= 20,
            "description": "at least 20 valid versioned tasks",
        },
        {
            "gate": "required-category-coverage",
            "passed": required_categories.issubset({item.category for item in valid}),
            "description": "frontend, backend, API, data, and test categories present",
        },
        {
            "gate": "balanced-category-composition",
            "passed": bool(valid_category_counts)
            and max(valid_category_counts.values())
            - min(valid_category_counts.values())
            <= 1,
            "description": "planned category counts differ by no more than one task",
        },
        {
            "gate": "holdout-coverage",
            "passed": sum(
                item.task_split == BenchmarkSplit.HOLDOUT for item in valid
            )
            >= 10,
            "description": "at least 10 previously unseen holdout tasks",
        },
        {
            "gate": "no-invalid-tasks",
            "passed": not task_invalid,
            "description": "no task-invalid records in the released set",
        },
        {
            "gate": "no-infrastructure-invalidations",
            "passed": not infrastructure_invalid,
            "description": "no infrastructure-invalid records in the released set",
        },
        {
            "gate": "scored-results-present",
            "passed": bool(scored),
            "description": "at least one result remains after invalidation accounting",
        },
    ]

    first = ordered[0]
    value = {
        "schema_version": 1,
        "benchmark_version": first.benchmark_version,
        "experiment_id": first.experiment_id,
        "configuration_digest": first.configuration_digest,
        "code_revision": first.code_revision,
        "task_set_digest": _canonical_sha256(
            [
                {
                    "task_id": item.task_id,
                    "task_version": item.task_version,
                    "task_manifest_digest": item.task_manifest_digest,
                    "split": item.task_split,
                    "category": item.category,
                    "language": item.language,
                }
                for item in ordered
            ]
        ),
        "record_set_digest": _canonical_sha256(
            [item.to_dict() for item in ordered]
        ),
        "counts": {
            "records": len(ordered),
            "valid": len(valid),
            "scored": len(scored),
            "solved": len(solved),
            "infrastructure_invalid": len(infrastructure_invalid),
            "task_invalid": len(task_invalid),
        },
        "metrics": {
            "verified_solve_rate": _rate(len(solved), len(scored)),
            "first_attempt_solve_rate": _rate(
                sum(item.patch_attempts == 1 for item in solved), len(scored)
            ),
            "regression_free_rate": _rate(
                sum(item.regression_free for item in scored), len(scored)
            ),
            "infrastructure_failure_rate": _rate(
                len(infrastructure_invalid), len(valid)
            ),
            "duration_ms": _distribution(item.duration_ms for item in valid),
            "cost_microusd": _distribution(item.cost_microusd for item in valid),
            "tool_calls": _distribution(item.tool_calls for item in valid),
            "cost_per_attempted_task_microusd": _mean(total_valid_cost, len(valid)),
            "cost_per_verified_solve_microusd": _mean(
                total_valid_cost, len(solved)
            ),
        },
        "category_breakdown": category_breakdown,
        "language_distribution": _counts(item.language for item in ordered),
        "split_distribution": _counts(str(item.task_split) for item in ordered),
        "failure_distribution": dict(sorted(failure_distribution.items())),
        "release_gates": release_gates,
        "release_ready": all(item["passed"] for item in release_gates),
    }
    return BenchmarkSummary(value)


def load_benchmark_jsonl(lines: Iterable[str]) -> tuple[BenchmarkRecord, ...]:
    records: list[BenchmarkRecord] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number} is not valid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number} must contain a JSON object")
        try:
            records.append(BenchmarkRecord.from_dict(value))
        except ValueError as error:
            raise ValueError(f"line {line_number}: {error}") from error
    return tuple(records)


def _require_single_configuration(records: Sequence[BenchmarkRecord]) -> None:
    for name in (
        "benchmark_version",
        "experiment_id",
        "configuration_digest",
        "code_revision",
    ):
        values = {getattr(item, name) for item in records}
        if len(values) != 1:
            raise ValueError(f"benchmark records mix multiple {name} values")


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "rate": None, "wilson_95": [None, None]}
    proportion = numerator / denominator
    z_squared = _Z_95**2
    center = (proportion + z_squared / (2 * denominator)) / (
        1 + z_squared / denominator
    )
    margin = _Z_95 * math.sqrt(
        (proportion * (1 - proportion) + z_squared / (4 * denominator))
        / denominator
    ) / (1 + z_squared / denominator)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(proportion, 6),
        "wilson_95": [round(max(0.0, center - margin), 6), round(min(1.0, center + margin), 6)],
    }


def _distribution(values: Iterable[int]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    if not ordered:
        return {"median": None, "p95": None}
    median = statistics.median(ordered)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {"median": median, "p95": p95}


def _mean(total: int, count: int) -> float | None:
    return None if count == 0 else round(total / count, 3)


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _format_rate(value: Mapping[str, Any]) -> str:
    rate = value["rate"]
    if rate is None:
        return "n/a"
    lower, upper = value["wilson_95"]
    return f"{rate * 100:.1f}% (95% CI {lower * 100:.1f}–{upper * 100:.1f}%)"


def _format_milliseconds(value: int | float | None) -> str:
    return "n/a" if value is None else f"{value / 1000:.2f}s"


def _format_cost(value: int | float | None) -> str:
    return "n/a" if value is None else f"${value / 1_000_000:.4f}"
