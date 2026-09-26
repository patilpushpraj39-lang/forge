from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    ROOT / "packages" / "agent-core" / "src",
    ROOT / "packages" / "evaluation" / "src",
    ROOT / "packages" / "repo-intelligence" / "src",
    ROOT / "services" / "sandbox-controller" / "src",
):
    sys.path.insert(0, str(source_path))

from forge_agent_core import create_run_store
from forge_evaluation import (
    BenchmarkExperiment,
    BenchmarkSplit,
    BenchmarkTask,
    export_benchmark_record,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export one strict benchmark record from durable Forge evidence."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--benchmark-version", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-version", required=True)
    parser.add_argument("--task-manifest-digest", required=True)
    parser.add_argument(
        "--split",
        required=True,
        choices=[item.value for item in BenchmarkSplit],
    )
    parser.add_argument(
        "--category",
        required=True,
        choices=["frontend", "backend", "api", "data", "test"],
    )
    parser.add_argument("--language", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--configuration-digest", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    store = create_run_store()
    try:
        record = export_benchmark_record(
            store,
            arguments.run_id,
            BenchmarkTask(
                arguments.benchmark_version,
                arguments.task_id,
                arguments.task_version,
                arguments.task_manifest_digest,
                BenchmarkSplit(arguments.split),
                arguments.category,
                arguments.language,
            ),
            BenchmarkExperiment(
                arguments.experiment_id,
                arguments.configuration_digest,
                arguments.code_revision,
            ),
        )
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()

    rendered = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"))
    if arguments.output is None:
        print(rendered)
    else:
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
