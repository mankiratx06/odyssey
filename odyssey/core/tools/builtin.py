"""Built-in tools: the minimum set that makes Odyssey useful for code.

Filesystem access is confined to a workspace root. This is a guardrail against
the model wandering, not a security sandbox — see README for the difference.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from .base import Tool, schema


def build(workspace: Path) -> list[Tool]:
    workspace = workspace.resolve()

    def resolve(relative: str) -> Path:
        target = (workspace / relative).resolve()
        if target != workspace and workspace not in target.parents:
            raise ValueError(f"path escapes workspace: {relative}")
        return target

    def read_file(path: str, max_lines: int = 2000) -> str:
        target = resolve(path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        body = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(lines[:max_lines], 1))
        if len(lines) > max_lines:
            body += f"\n... [{len(lines) - max_lines} more lines]"
        return body or "(empty file)"

    def write_file(path: str, content: str) -> str:
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        target.write_text(content, encoding="utf-8")
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {path} ({len(content)} bytes)"

    def edit_file(path: str, old: str, new: str) -> str:
        target = resolve(path)
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return "Error: `old` text not found in file."
        if count > 1:
            return f"Error: `old` matches {count} times; include more context to disambiguate."
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path}"

    def list_dir(path: str = ".") -> str:
        target = resolve(path)
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        if not entries:
            return "(empty directory)"
        return "\n".join(
            f"{'dir ' if e.is_dir() else 'file'}  {e.name}"
            for e in entries
            if not e.name.startswith(".")
        )

    async def run_shell(command: str, timeout: int = 120) -> str:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            return f"Error: command exceeded {timeout}s and was killed."
        output = stdout.decode("utf-8", errors="replace").strip()
        return f"exit={proc.returncode}\n{output or '(no output)'}"

    return [
        Tool(
            name="read_file",
            description="Read a file from the workspace, with line numbers.",
            parameters=schema(
                path={"type": "string", "description": "Path relative to the workspace root"},
                max_lines={"type": "integer", "default": 2000},
            ),
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description="Create a file or overwrite it entirely. Prefer edit_file for changes.",
            parameters=schema(
                path={"type": "string"},
                content={"type": "string"},
            ),
            handler=write_file,
            requires_approval=True,
        ),
        Tool(
            name="edit_file",
            description="Replace one unique occurrence of `old` with `new` in a file.",
            parameters=schema(
                path={"type": "string"},
                old={"type": "string", "description": "Exact text to replace; must match once"},
                new={"type": "string"},
            ),
            handler=edit_file,
            requires_approval=True,
        ),
        Tool(
            name="list_dir",
            description="List the contents of a directory in the workspace.",
            parameters=schema(path={"type": "string", "default": "."}),
            handler=list_dir,
        ),
        Tool(
            name="run_shell",
            description="Run a shell command in the workspace and return its combined output.",
            parameters=schema(
                command={"type": "string"},
                timeout={"type": "integer", "default": 120},
            ),
            handler=run_shell,
            requires_approval=True,
        ),
    ]
