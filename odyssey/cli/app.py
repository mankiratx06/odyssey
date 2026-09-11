"""Terminal frontend.

One consumer of the event stream among several. Note how little it knows:
it never touches litellm, tools, or message history.
"""

from __future__ import annotations

import asyncio
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.panel import Panel

from ..config import CONFIG_DIR
from ..core.agent import Agent
from ..core.events import Done, Failed, TextDelta, ToolEnd, ToolStart, Usage
from ..core.tools.base import Tool

console = Console()

BANNER = r"""
  ___     _
 / _ \ __| |_   _ ___ ___  ___ _   _
| | | / _` | | | / __/ __|/ _ \ | | |
| |_| \__,_| |_| \__ \__ \  __/ |_| |
 \___/\__,_|\__, |___/___/\___|\__, |
            |___/              |___/
"""


def make_approver(auto: bool = False):
    async def approve(tool: Tool, arguments: dict[str, Any]) -> bool:
        if auto:
            return True
        body = "\n".join(f"{k}: {_short(v, 400)}" for k, v in arguments.items())
        console.print(Panel(body, title=f"Run {tool.name}?", border_style="yellow"))
        answer = await asyncio.to_thread(input, "  [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    return approve


def _short(value: Any, limit: int = 70) -> str:
    text = str(value).replace("\n", "⏎")
    return text if len(text) <= limit else text[:limit] + "…"


async def render(agent: Agent, message: str, show_usage: bool = False) -> None:
    """Consume one turn's events and paint them."""
    streaming = False
    async for event in agent.send(message):
        if isinstance(event, TextDelta):
            console.print(event.text, end="", markup=False, highlight=False)
            streaming = True
        elif isinstance(event, ToolStart):
            if streaming:
                console.print()
                streaming = False
            preview = ", ".join(f"{k}={_short(v)}" for k, v in event.arguments.items())
            console.print(f"[dim]  → {event.name}({preview})[/dim]")
        elif isinstance(event, ToolEnd):
            first = event.result.splitlines()[0] if event.result else ""
            colour = "dim" if event.ok else "red"
            console.print(f"[{colour}]  ← {_short(first, 100)}[/{colour}]")
        elif isinstance(event, Usage) and show_usage:
            console.print(
                f"[dim]  {event.input_tokens} in / {event.output_tokens} out[/dim]"
            )
        elif isinstance(event, Done):
            if streaming:
                console.print()
            if event.reason == "max_steps":
                console.print("[yellow]  (stopped at the step limit)[/yellow]")
        elif isinstance(event, Failed):
            if streaming:
                console.print()
            console.print(f"[red]  {event.message}[/red]")
    console.print()


async def chat_loop(agent: Agent, show_usage: bool = False) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    session: PromptSession = PromptSession(history=FileHistory(str(CONFIG_DIR / "history")))

    console.print(f"[bold cyan]{BANNER}[/bold cyan]")
    console.print(
        f"  model [bold]{agent.config.model_for(agent.role)}[/bold] · "
        f"{len(agent.registry.names())} tools · workspace {agent.config.workspace}"
    )
    console.print("  [dim]/model <role>  /tools  /reset  /exit[/dim]\n")

    while True:
        try:
            with patch_stdout():
                line = (await session.prompt_async("› ")).strip()
        except (EOFError, KeyboardInterrupt):
            return

        if not line:
            continue
        if line in {"/exit", "/quit"}:
            return
        if line == "/reset":
            agent.reset()
            console.print("[dim]  context cleared[/dim]\n")
            continue
        if line == "/tools":
            console.print("\n".join(f"  {n}" for n in agent.registry.names()) + "\n")
            continue
        if line.startswith("/model"):
            _, _, role = line.partition(" ")
            agent.role = role.strip() or "chat"
            console.print(f"[dim]  now using {agent.config.model_for(agent.role)}[/dim]\n")
            continue

        await render(agent, line, show_usage=show_usage)
