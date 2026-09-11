"""MCP client — the extension seam.

Every future capability (image generation, video, browser control, a database)
arrives as an MCP server entry in config.toml. Nothing in the core changes:

    [mcp.servers.images]
    command = "npx"
    args = ["-y", "some-image-mcp-server"]

Tools are namespaced as `server__tool` so two servers can both expose `search`.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

from .base import Tool


class MCPManager:
    """Owns the lifetime of every configured MCP server connection."""

    def __init__(self, servers: dict[str, dict[str, Any]]) -> None:
        self.servers = servers
        self._stack = AsyncExitStack()
        self._sessions: dict[str, Any] = {}

    async def __aenter__(self) -> MCPManager:
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()
        self._sessions.clear()

    async def connect_all(self) -> list[Tool]:
        """Start every configured server and return their tools, flattened."""
        if not self.servers:
            return []

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            print("[mcp] the `mcp` package is not installed; skipping configured servers")
            return []

        tools: list[Tool] = []
        for name, cfg in self.servers.items():
            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env={**os.environ, **cfg.get("env", {})},
            )
            try:
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
            except Exception as exc:
                # One bad server should not take down the agent.
                print(f"[mcp] skipping {name!r}: {type(exc).__name__}: {exc}")
                continue

            self._sessions[name] = session
            listed = await session.list_tools()
            for remote in listed.tools:
                tools.append(self._wrap(name, session, remote))
        return tools

    def _wrap(self, server: str, session: Any, remote: Any) -> Tool:
        async def handler(**arguments: Any) -> str:
            result = await session.call_tool(remote.name, arguments)
            parts = []
            for block in result.content:
                parts.append(getattr(block, "text", None) or f"[{block.type} content]")
            return "\n".join(parts) or "(no content returned)"

        return Tool(
            name=f"{server}__{remote.name}",
            description=(remote.description or remote.name).strip(),
            parameters=remote.inputSchema or {"type": "object", "properties": {}},
            handler=handler,
            # Remote code doing unknown things — approve by default, relax per server.
            requires_approval=True,
        )
