"""The agent loop.

This module does no I/O of its own. It takes a message and yields Events.
That single property is why the same core drives the terminal today and will
drive a desktop app and a web backend without a fork.

Streaming is handled here rather than in a frontend because assembling tool
calls out of partial chunks is fiddly and you only want it written once.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import litellm

from ..config import Config, CredentialSource, EnvKeychainCredentials, provider_of
from .events import Done, Event, Failed, TextDelta, ToolEnd, ToolStart, Usage
from .limits import Limiter, QuotaExceeded, Unlimited
from .tools.base import Tool, ToolRegistry

SYSTEM_PROMPT = """You are Odyssey, a capable assistant running on the user's own machine.

You can hold an ordinary conversation and you can do real work with tools.
Read before you edit. Prefer small, verifiable steps over one large change.
When a tool fails, read the error and adapt rather than retrying identically.
Say plainly when you are unsure instead of guessing."""

Approver = Callable[[Tool, dict[str, Any]], Awaitable[bool]]


async def approve_all(tool: Tool, arguments: dict[str, Any]) -> bool:
    return True


@dataclass
class Agent:
    config: Config
    registry: ToolRegistry
    credentials: CredentialSource = field(default_factory=EnvKeychainCredentials)
    limiter: Limiter = field(default_factory=Unlimited)
    approver: Approver = approve_all
    extra_system: str = ""
    role: str = "chat"
    messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages.append({"role": "system", "content": self._system_prompt()})

    def _system_prompt(self) -> str:
        prompt = SYSTEM_PROMPT
        if self.extra_system:
            prompt += "\n\n" + self.extra_system
        return prompt

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self._system_prompt()}]

    async def send(self, message: str) -> AsyncIterator[Event]:
        """Run one turn to completion, yielding events as they happen."""
        model = self.config.model_for(self.role)
        api_key = self.credentials.get(provider_of(model))
        if not api_key:
            provider = provider_of(model)
            yield Failed(
                f"No credential for {provider!r}. Set the environment variable, or run:\n"
                f"  odyssey keys set {provider}",
                kind="auth",
            )
            return

        self.messages.append({"role": "user", "content": message})
        final_text: list[str] = []

        for _ in range(self.config.limits["max_steps"]):
            try:
                reservation = await self.limiter.reserve(model, self.config.limits["max_tokens"])
            except QuotaExceeded as exc:
                yield Done(text="".join(final_text), reason="limit")
                yield Failed(str(exc), kind="quota")
                return

            out: dict[str, Any] = {}
            try:
                async for event in self._stream_model(model, api_key, out):
                    yield event
            except Exception as exc:
                yield Failed(f"{type(exc).__name__}: {exc}", kind="provider")
                return

            usage: Usage | None = out.get("usage")
            if usage:
                await self.limiter.settle(reservation, usage.input_tokens, usage.output_tokens)
                yield usage

            self.messages.append(out["assistant"])
            final_text.extend(out["chunks"])
            calls = out["calls"]

            if not calls:
                yield Done(text="".join(final_text))
                return

            for call in calls:
                async for event in self._run_tool(call):
                    yield event

        yield Done(text="".join(final_text), reason="max_steps")

    async def _stream_model(
        self, model: str, api_key: str, out: dict[str, Any]
    ) -> AsyncIterator[Event]:
        """One streamed completion.

        Yields TextDelta as tokens arrive; writes the assembled assistant
        message, text chunks, tool calls and usage into `out`.
        """
        stream = await litellm.acompletion(
            model=model,
            messages=self.messages,
            tools=self.registry.specs() or None,
            max_tokens=self.config.limits["max_tokens"],
            api_key=api_key,
            stream=True,
            stream_options={"include_usage": True},
        )

        chunks: list[str] = []
        partial: dict[int, dict[str, Any]] = {}
        usage: Usage | None = None

        async for chunk in stream:
            if raw := getattr(chunk, "usage", None):
                usage = Usage(
                    model=model,
                    input_tokens=getattr(raw, "prompt_tokens", 0) or 0,
                    output_tokens=getattr(raw, "completion_tokens", 0) or 0,
                )
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                chunks.append(delta.content)
                yield TextDelta(delta.content)
            for piece in getattr(delta, "tool_calls", None) or []:
                slot = partial.setdefault(
                    piece.index, {"id": "", "name": "", "arguments": ""}
                )
                if piece.id:
                    slot["id"] = piece.id
                if piece.function and piece.function.name:
                    slot["name"] = piece.function.name
                if piece.function and piece.function.arguments:
                    slot["arguments"] += piece.function.arguments

        calls = [
            {
                "id": slot["id"] or f"call_{index}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
            }
            for index, slot in sorted(partial.items())
            if slot["name"]
        ]

        assistant: dict[str, Any] = {"role": "assistant", "content": "".join(chunks) or None}
        if calls:
            assistant["tool_calls"] = calls

        out["assistant"] = assistant
        out["chunks"] = chunks
        out["calls"] = calls
        out["usage"] = usage

    async def _run_tool(self, call: dict[str, Any]) -> AsyncIterator[Event]:
        name = call["function"]["name"]
        try:
            arguments = json.loads(call["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = {}

        yield ToolStart(call_id=call["id"], name=name, arguments=arguments)
        result = await self.registry.call(name, arguments, approve=self.approver)
        ok = not result.startswith("Error")
        yield ToolEnd(call_id=call["id"], name=name, result=result, ok=ok)

        self.messages.append(
            {"role": "tool", "tool_call_id": call["id"], "name": name, "content": result}
        )

    async def collect(self, message: str) -> str:
        """Convenience for non-streaming callers: run a turn, return the text."""
        text: list[str] = []
        async for event in self.send(message):
            if isinstance(event, TextDelta):
                text.append(event.text)
            elif isinstance(event, Failed):
                raise RuntimeError(event.message)
        return "".join(text)
