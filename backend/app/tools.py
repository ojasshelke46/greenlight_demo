import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedToolCall:
    tool_call_id: str
    server: str | None
    name: str | None
    arguments: Any


def _parse_arguments(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def resolve_tool_call(call: dict[str, Any]) -> ResolvedToolCall:
    """The MCP tool a TrueForge tool call really runs.

    Agents without preloaded tools reach MCP servers through the call_tool system tool, whose
    arguments are {"mcp_server", "tool_name", "input"}. Preloaded tools carry an mcp tool_info.
    """
    function = call.get("function") or {}
    info = call.get("tool_info") or {}
    arguments = _parse_arguments(function.get("arguments"))

    if info.get("type") == "truefoundry-system" and info.get("name") == "call_tool" and isinstance(arguments, dict):
        return ResolvedToolCall(
            tool_call_id=call.get("id", ""),
            server=arguments.get("mcp_server"),
            name=arguments.get("tool_name"),
            arguments=arguments.get("input"),
        )
    if info.get("type") == "mcp":
        return ResolvedToolCall(
            tool_call_id=call.get("id", ""), server=info.get("server_name"), name=info.get("name"), arguments=arguments
        )
    return ResolvedToolCall(tool_call_id=call.get("id", ""), server=None, name=function.get("name"), arguments=arguments)


def merge_tool_call_deltas(calls: list[dict[str, Any]], deltas: list[dict[str, Any]]) -> None:
    """Merge model.message.delta tool call fragments by index, like the SDK's mergeEventDelta."""
    for delta in deltas:
        index = delta.get("index", len(calls))
        function = delta.get("function") or {}
        if index < len(calls):
            existing = calls[index]
            if delta.get("id"):
                existing["id"] = delta["id"]
            if delta.get("tool_info") is not None:
                existing["tool_info"] = delta["tool_info"]
            if function.get("name"):
                existing["function"]["name"] = function["name"]
            if function.get("arguments"):
                existing["function"]["arguments"] += function["arguments"]
        elif index == len(calls):
            calls.append(
                {
                    "id": delta.get("id"),
                    "type": "function",
                    "function": {"name": function.get("name") or "", "arguments": function.get("arguments") or ""},
                    "tool_info": delta.get("tool_info"),
                }
            )
