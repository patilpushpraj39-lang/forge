from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    ROOT / "packages" / "evaluation" / "src",
    ROOT / "packages" / "repo-intelligence" / "src",
    ROOT / "services" / "sandbox-controller" / "src",
):
    sys.path.insert(0, str(source_path))

from forge_evaluation import load_benchmark_jsonl, summarize_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a reproducible Forge benchmark summary and card."
    )
    parser.add_argument("input", type=Path, help="Versioned benchmark JSONL records")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    arguments = parser.parse_args()

    with arguments.input.open("r", encoding="utf-8") as source:
        summary = summarize_benchmark(load_benchmark_jsonl(source))
    artifact = {**summary.to_dict(), "summary_digest": summary.digest}
    rendered_json = json.dumps(artifact, indent=2, sort_keys=True) + "\n"

    if arguments.json_output is None and arguments.markdown_output is None:
        print(rendered_json, end="")
        return 0
    if arguments.json_output is not None:
        arguments.json_output.write_text(rendered_json, encoding="utf-8")
    if arguments.markdown_output is not None:
        arguments.markdown_output.write_text(summary.to_markdown(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
