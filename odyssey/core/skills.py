"""Skills — reusable know-how stored as plain directories on disk.

A skill is a folder containing SKILL.md with YAML frontmatter:

    ---
    name: deploy-staging
    description: How to deploy this repo to staging. Use when asked to ship.
    ---
    1. Run the test suite...

Progressive disclosure: only names and descriptions go in the system prompt.
The agent calls `load_skill` to pull the body in when it decides it needs it,
which keeps a hundred skills from eating the context window.

This is also where self-improvement lives later — give the agent a
`write_skill` tool and it can record what it learns.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .tools.base import Tool, schema


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _parse(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    _, frontmatter, body = text.split("---", 2)
    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return None
    if not meta.get("name"):
        return None
    return Skill(
        name=str(meta["name"]),
        description=str(meta.get("description", "")).strip(),
        body=body.strip(),
        path=path,
    )


class SkillLibrary:
    def __init__(self, search_paths: list[Path]) -> None:
        self.skills: dict[str, Skill] = {}
        for root in search_paths:
            root = root.expanduser()
            if not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                skill = _parse(skill_file)
                if skill:
                    self.skills[skill.name] = skill  # later paths win

    def catalogue(self) -> str:
        """The one-line-per-skill summary that goes into the system prompt."""
        if not self.skills:
            return ""
        lines = [f"- {s.name}: {s.description}" for s in self.skills.values()]
        return (
            "Available skills (call load_skill to read the full instructions "
            "before acting on one):\n" + "\n".join(lines)
        )

    def tool(self) -> Tool:
        def load_skill(name: str) -> str:
            skill = self.skills.get(name)
            if skill is None:
                return f"No skill named {name!r}. Known: {', '.join(self.skills) or 'none'}"
            return f"# Skill: {skill.name}\n(from {skill.path})\n\n{skill.body}"

        return Tool(
            name="load_skill",
            description="Load the full instructions for a named skill.",
            parameters=schema(name={"type": "string"}),
            handler=load_skill,
        )
