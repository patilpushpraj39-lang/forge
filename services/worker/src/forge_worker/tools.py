from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from forge_agent_core.model_runtime import ToolSpec
from forge_agent_core.tool_policy import ToolPolicyError
from forge_sandbox_controller import SandboxController


class CommandPolicyError(ToolPolicyError):
    pass


@dataclass(frozen=True)
class CommandPolicy:
    allowed_executables: frozenset[str] = frozenset(
        {"node", "npm", "pnpm", "python", "python3"}
    )
    denied_subcommands: frozenset[str] = frozenset(
        {"add", "dlx", "exec", "install", "publish"}
    )
    max_arguments: int = 32
    max_argument_characters: int = 1024
    max_timeout_seconds: float = 120
    max_output_bytes: int = 64 * 1024

    def validate(self, command: Sequence[str], timeout_seconds: float) -> None:
        if not command:
            raise CommandPolicyError("command cannot be empty")
        executable = command[0].casefold()
        if PurePath(executable).name != executable:
            raise CommandPolicyError("command executable must be a simple name")
        if executable not in self.allowed_executables:
            raise CommandPolicyError(
                f"command executable is not allowed: {executable}"
            )
        if len(command) > self.max_arguments:
            raise CommandPolicyError("command has too many arguments")
        if any(len(argument) > self.max_argument_characters for argument in command):
            raise CommandPolicyError("command argument is too long")
        if len(command) > 1 and command[1].casefold() in self.denied_subcommands:
            raise CommandPolicyError(
                f"command subcommand is not allowed: {command[1]}"
            )
        if timeout_seconds <= 0 or timeout_seconds > self.max_timeout_seconds:
            raise CommandPolicyError("command timeout exceeds policy")


def forge_tool_specs(version: str = "forge-tools-v1") -> tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            "repository_manifest",
            "Return the immutable repository brief.",
            _object_schema({}),
            version,
        ),
        ToolSpec(
            "search_repository",
            "Search repository paths, exact text, and symbols with provenance.",
            _object_schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "modes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["path", "text", "symbol"],
                        },
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                }
            ),
            version,
        ),
        ToolSpec(
            "read_file",
            "Read a bounded UTF-8 file range with snapshot and line provenance.",
            _object_schema(
                {
                    "path": {"type": "string", "minLength": 1, "maxLength": 500},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": ["integer", "null"], "minimum": 1},
                }
            ),
            version,
        ),
        ToolSpec(
            "dependency_neighborhood",
            "Return bounded incoming and outgoing repository dependencies.",
            _object_schema(
                {
                    "path": {"type": "string", "minLength": 1, "maxLength": 500},
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 3},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                }
            ),
            version,
        ),
        ToolSpec(
            "execute_command",
            "Run one allowlisted command inside the bounded sandbox.",
            _object_schema(
                {
                    "command": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {"type": "string", "maxLength": 1024},
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 0.1,
                        "maximum": 120,
                    },
                }
            ),
            version,
        ),
        ToolSpec(
            "apply_patch",
            "Apply one checksum-bound UTF-8 unified patch.",
            _object_schema(
                {
                    "patch": {"type": "string", "minLength": 1, "maxLength": 256000},
                    "expected_sha256": {
                        "type": ["string", "null"],
                        "pattern": "[0-9a-f]{64}",
                    },
                }
            ),
            version,
        ),
        ToolSpec(
            "get_diff",
            "Create a content-addressed patch artifact for current changes.",
            _object_schema({}),
            version,
        ),
    )


class ForgeToolExecutor:
    def __init__(
        self,
        controller: SandboxController,
        sandbox_id: str,
        *,
        command_policy: CommandPolicy | None = None,
        should_cancel: Callable[[], bool] = lambda: False,
        heartbeat: Callable[[], None] = lambda: None,
    ) -> None:
        self.controller = controller
        self.sandbox_id = sandbox_id
        self.command_policy = command_policy or CommandPolicy()
        self.should_cancel = should_cancel
        self.heartbeat = heartbeat

    def execute(
        self, name: str, arguments: dict[str, object], idempotency_key: str
    ) -> dict[str, object]:
        if name == "repository_manifest":
            return {"brief": self.controller.index_repository(self.sandbox_id).brief()}
        if name == "search_repository":
            results = self.controller.search_repository(
                self.sandbox_id,
                str(arguments["query"]),
                modes=tuple(str(item) for item in arguments["modes"]),
                limit=int(arguments["limit"]),
            )
            return {"results": [result.to_dict() for result in results]}
        if name == "read_file":
            end = arguments["end_line"]
            result = self.controller.read_file(
                self.sandbox_id,
                str(arguments["path"]),
                start_line=int(arguments["start_line"]),
                end_line=None if end is None else int(end),
            )
            return {"result": result.to_dict()}
        if name == "dependency_neighborhood":
            results = self.controller.dependency_neighborhood(
                self.sandbox_id,
                str(arguments["path"]),
                max_depth=int(arguments["max_depth"]),
                limit=int(arguments["limit"]),
            )
            return {"results": [result.to_dict() for result in results]}
        if name == "execute_command":
            command = tuple(str(item) for item in arguments["command"])
            timeout_seconds = float(arguments["timeout_seconds"])
            self.command_policy.validate(command, timeout_seconds)
            result = self.controller.execute(
                self.sandbox_id,
                command,
                timeout_seconds,
                self.should_cancel,
                self.heartbeat,
                min(1.0, timeout_seconds / 3),
                self.command_policy.max_output_bytes,
            )
            return {
                "status": result.status,
                "exit_code": result.exit_code,
                "output": result.output,
                "output_truncated": result.output_truncated,
            }
        if name == "apply_patch":
            patch = str(arguments["patch"]).encode("utf-8")
            expected = arguments["expected_sha256"]
            applied = self.controller.apply_patch(
                self.sandbox_id,
                patch,
                None if expected is None else str(expected),
            )
            return {
                "patch_hash": applied.patch_hash,
                "changed_paths": list(applied.changed_paths),
            }
        if name == "get_diff":
            artifact = self.controller.diff(self.sandbox_id)
            return {
                "diff_hash": artifact.diff_hash,
                "artifact": artifact.artifact.to_dict(),
                "changed_paths": list(artifact.changed_paths),
            }
        raise ValueError(f"unknown Forge tool: {name}")


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
