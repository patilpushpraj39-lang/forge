from __future__ import annotations

import json
import unittest

from forge_agent_core.model_runtime import (
    BudgetRemaining,
    ModelStepRequest,
    StopReason,
    ToolOutput,
    ToolSpec,
    TransientModelError,
)
from forge_agent_core.openai_runtime import (
    ModelPricing,
    OpenAIResponsesRuntime,
)


TOOL = ToolSpec(
    "read_file",
    "Read a repository file.",
    {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    "tools-v1",
)
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
    "additionalProperties": False,
}


class _Responses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Client:
    def __init__(self, outcomes):
        self.responses = _Responses(outcomes)


class OpenAIResponsesRuntimeTests(unittest.TestCase):
    def request(self, **changes) -> ModelStepRequest:
        values = {
            "run_id": "run-1",
            "step_id": "step-1",
            "objective": "Inspect the repository.",
            "context_refs": (),
            "allowed_tools": (TOOL,),
            "output_schema": OUTPUT_SCHEMA,
            "budget": BudgetRemaining(10_000, 100_000, 20, 4, 4, 2),
            "prompt_version": "prompt-v1",
            "tool_version": "tools-v1",
            "idempotency_key": "a" * 64,
            "previous_response_id": None,
            "tool_outputs": (),
            **changes,
        }
        return ModelStepRequest(**values)

    def runtime(self, outcomes, *, reasoning_effort=None, service_tier=None):
        client = _Client(outcomes)
        runtime = OpenAIResponsesRuntime(
            "model-snapshot",
            ModelPricing(1_000_000, 500_000, 2_000_000),
            client=client,
            reasoning_effort=reasoning_effort,
            service_tier=service_tier,
        )
        return runtime, client

    def test_function_call_translation_uses_strict_serial_policy(self) -> None:
        response = {
            "id": "resp-1",
            "status": "completed",
            "model": "model-snapshot",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "read_file",
                    "arguments": json.dumps({"path": "app.py"}),
                }
            ],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "input_tokens_details": {"cached_tokens": 200},
                "output_tokens_details": {"reasoning_tokens": 20},
            },
        }
        runtime, client = self.runtime(
            [response], reasoning_effort="medium", service_tier="default"
        )

        result = runtime.run_step(self.request())
        sent = client.responses.requests[0]

        self.assertEqual(result.stop_reason, StopReason.TOOL_REQUESTED)
        self.assertEqual(result.tool_calls[0].arguments, {"path": "app.py"})
        self.assertEqual(result.cost_microusd, 1100)
        self.assertEqual(result.usage.cached_input_tokens, 200)
        self.assertTrue(sent["tools"][0]["strict"])
        self.assertFalse(sent["parallel_tool_calls"])
        self.assertTrue(sent["store"])
        self.assertEqual(
            sent["extra_headers"]["Idempotency-Key"], "a" * 64
        )
        self.assertEqual(sent["text"]["format"]["type"], "json_schema")
        self.assertEqual(sent["reasoning"], {"effort": "medium"})
        self.assertEqual(sent["service_tier"], "default")

    def test_reasoning_effort_is_explicitly_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported reasoning effort"):
            self.runtime([], reasoning_effort="extreme")
        with self.assertRaisesRegex(ValueError, "unsupported service tier"):
            self.runtime([], service_tier="slow")

    def test_tool_output_continuation_references_call_and_response(self) -> None:
        response = {
            "id": "resp-2",
            "status": "completed",
            "model": "model-snapshot",
            "output": [],
            "output_text": '{"status":"completed","summary":"done"}',
            "usage": {"input_tokens": 2, "output_tokens": 2},
        }
        runtime, client = self.runtime([response])
        result = runtime.run_step(
            self.request(
                previous_response_id="resp-1",
                tool_outputs=(ToolOutput("call-1", {"content": "ok"}),),
            )
        )
        sent = client.responses.requests[0]

        self.assertEqual(result.stop_reason, StopReason.COMPLETED)
        self.assertEqual(result.structured_output["summary"], "done")
        self.assertEqual(sent["previous_response_id"], "resp-1")
        self.assertEqual(sent["input"][0]["type"], "function_call_output")
        self.assertEqual(sent["input"][0]["call_id"], "call-1")

    def test_provider_rate_limit_is_classified_as_transient(self) -> None:
        class RateLimitError(Exception):
            pass

        runtime, _ = self.runtime([RateLimitError("slow down")])
        with self.assertRaises(TransientModelError):
            runtime.run_step(self.request())


if __name__ == "__main__":
    unittest.main()
