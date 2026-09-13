"""Odyssey CLI.

    odyssey                       interactive chat
    odyssey "fix the test"        one-shot
    odyssey --role code           route to the coding model
    odyssey serve                 HTTP + SSE backend for desktop/web clients
    odyssey keys set anthropic    store a key in the OS keychain
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from .cli.app import chat_loop, console, make_approver, render
from .config import APP_NAME, PROVIDER_ENV, Config
from .core.agent import Agent
from .core.skills import SkillLibrary
from .core.tools import builtin
from .core.tools.base import ToolRegistry
from .core.tools.mcp import MCPManager


async def _chat(args: argparse.Namespace) -> int:
    config = Config.load()
    registry = ToolRegistry()
    registry.extend(builtin.build(config.workspace))

    # Registered even with no skills on disk: write_skill is how the first one
    # gets there.
    skills = SkillLibrary(config.skill_paths)
    registry.extend(skills.tools())

    async with MCPManager(config.mcp_servers) as mcp:
        registry.extend(await mcp.connect_all())

        agent = Agent(
            config=config,
            registry=registry,
            approver=make_approver(auto=args.yes),
            extra_system=skills.catalogue(),
            role=args.role,
        )

        if args.prompt:
            await render(agent, " ".join(args.prompt), show_usage=args.usage)
        else:
            await chat_loop(agent, show_usage=args.usage)
    return 0


def _keys(args: argparse.Namespace) -> int:
    env_var = PROVIDER_ENV.get(args.provider)
    if not env_var:
        console.print(
            f"[red]Unknown provider {args.provider!r}. "
            f"Known: {', '.join(PROVIDER_ENV)}[/red]"
        )
        return 1
    try:
        import keyring
    except ImportError:
        console.print("[red]keyring is not installed; set the env var instead.[/red]")
        return 1

    secret = getpass.getpass(f"{env_var}: ").strip()
    if not secret:
        console.print("[yellow]nothing entered, aborted[/yellow]")
        return 1
    keyring.set_password(APP_NAME, env_var, secret)
    console.print(f"[green]stored {env_var} in the system keychain[/green]")
    return 0


COMMANDS = {"serve", "keys"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Dispatch on the first token rather than using subparsers, so that a bare
    # prompt ("odyssey fix the tests") doesn't get read as a subcommand.
    command = argv[0] if argv and argv[0] in COMMANDS else None
    if command:
        argv = argv[1:]

    if command == "serve":
        parser = argparse.ArgumentParser(prog="odyssey serve")
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8765)
        args = parser.parse_args(argv)

        from .server.app import serve

        console.print(f"[cyan]Odyssey serving on http://{args.host}:{args.port}[/cyan]")
        serve(host=args.host, port=args.port)
        return 0

    if command == "keys":
        parser = argparse.ArgumentParser(prog="odyssey keys")
        parser.add_argument("action", choices=["set"])
        parser.add_argument("provider")
        return _keys(parser.parse_args(argv))

    parser = argparse.ArgumentParser(
        prog="odyssey",
        description="Odyssey AI — an agent you run yourself",
        epilog="commands: serve (HTTP backend), keys set <provider>",
    )
    parser.add_argument("prompt", nargs="*", help="one-shot prompt; omit for interactive chat")
    parser.add_argument("--role", default="chat", help="model role: chat, code, cheap, free")
    parser.add_argument("-y", "--yes", action="store_true", help="skip tool approval prompts")
    parser.add_argument("--usage", action="store_true", help="show token counts per call")
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_chat(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
