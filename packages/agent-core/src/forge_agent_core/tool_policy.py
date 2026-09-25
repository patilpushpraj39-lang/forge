from __future__ import annotations

import re
from typing import Any

from .model_runtime import ToolCall, ToolSpec


class ToolPolicyError(ValueError):
    pass


def validate_tool_specs(tools: tuple[ToolSpec, ...]) -> None:
    names: set[str] = set()
    for tool in tools:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", tool.name):
            raise ToolPolicyError(f"invalid tool name: {tool.name}")
        if tool.name in names:
            raise ToolPolicyError(f"duplicate tool name: {tool.name}")
        names.add(tool.name)
        validate_strict_object_schema(tool.parameters, path=tool.name)


def validate_strict_object_schema(
    schema: dict[str, Any], path: str = "schema"
) -> None:
    _validate_strict_object_schema(schema, path)


def validate_json_value(
    value: Any, schema: dict[str, Any], path: str = "value"
) -> None:
    _validate_value(value, schema, path)


def validate_tool_call(
    tools: tuple[ToolSpec, ...], call: ToolCall
) -> ToolSpec:
    validate_tool_specs(tools)
    tool = next((item for item in tools if item.name == call.name), None)
    if tool is None:
        raise ToolPolicyError(f"tool is not allowed: {call.name}")
    _validate_value(call.arguments, tool.parameters, path=call.name)
    return tool


def _validate_strict_object_schema(schema: dict[str, Any], path: str) -> None:
    if schema.get("type") != "object":
        raise ToolPolicyError(f"{path} parameters must be an object schema")
    if schema.get("additionalProperties") is not False:
        raise ToolPolicyError(
            f"{path} must set additionalProperties to false"
        )
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ToolPolicyError(f"{path} must define properties and required")
    if set(properties) != set(required):
        raise ToolPolicyError(
            f"{path} strict schema must require every property"
        )
    for name, child in properties.items():
        if not isinstance(child, dict):
            raise ToolPolicyError(f"{path}.{name} must be a schema")
        child_types = child.get("type")
        if child_types == "object":
            _validate_strict_object_schema(child, f"{path}.{name}")
        elif isinstance(child_types, list) and "object" in child_types:
            _validate_strict_object_schema(child, f"{path}.{name}")


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if value is None and "null" in expected_types:
        return
    allowed = [item for item in expected_types if item != "null"]
    if not any(_matches_type(value, item) for item in allowed):
        raise ToolPolicyError(f"{path} has the wrong type")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolPolicyError(f"{path} is not an allowed enum value")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = required - set(value)
        extras = set(value) - set(properties)
        if missing:
            raise ToolPolicyError(
                f"{path} is missing required fields: {sorted(missing)}"
            )
        if extras and schema.get("additionalProperties") is False:
            raise ToolPolicyError(
                f"{path} contains extra fields: {sorted(extras)}"
            )
        for name, child_value in value.items():
            child_schema = properties.get(name)
            if child_schema is not None:
                _validate_value(child_value, child_schema, f"{path}.{name}")
    elif isinstance(value, list) and "items" in schema:
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ToolPolicyError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ToolPolicyError(f"{path} has too many items")
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], f"{path}[{index}]")
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ToolPolicyError(f"{path} is shorter than allowed")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ToolPolicyError(f"{path} is longer than allowed")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ToolPolicyError(f"{path} does not match its pattern")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolPolicyError(f"{path} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolPolicyError(f"{path} is above its maximum")


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)
