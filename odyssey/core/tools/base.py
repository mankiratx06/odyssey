"""Tool abstraction.

Everything the agent can do is a Tool. Built-in Python functions, MCP server
tools, and skills all normalise to this one shape, which means the agent loop
never needs to know where a capability came from.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the arguments object
    handler: Callable[..., Any | Awaitable[Any]]
    requires_approval: bool = False

    def spec(self) -> dict[str, Any]:
        """OpenAI-format function spec, which LiteLLM translates per provider."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def extend(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        approve: Callable[[Tool, dict], Awaitable[bool]] | None = None,
    ) -> str:
        tool = self.get(name)
        if tool is None:
            return f"Error: no tool named {name!r}. Available: {', '.join(self.names())}"

        if tool.requires_approval:
            if approve is None:
                return "Error: this tool requires approval but no approver is configured."
            if not await approve(tool, arguments):
                return "The user declined to run this tool."

        try:
            result = tool.handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
            return _stringify(result)
        except TypeError as exc:
            return f"Error: bad arguments for {name}: {exc}"
        except Exception as exc:  # tool failures are data, not crashes
            return f"Error running {name}: {type(exc).__name__}: {exc}"


def _stringify(result: Any, limit: int = 30_000) -> str:
    text = result if isinstance(result, str) else repr(result)
    if len(text) > limit:
        return text[:limit] + f"\n... [truncated, {len(text) - limit} chars omitted]"
    return text


def schema(**properties: dict[str, Any]) -> dict[str, Any]:
    """Small helper so tool definitions stay readable.

    Mark a property required by leaving out a "default" key.
    """
    required = [k for k, v in properties.items() if "default" not in v]
    return {"type": "object", "properties": properties, "required": required}
