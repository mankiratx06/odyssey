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

`write_skill` closes the loop: the agent records what it works out, and the
next session starts knowing it. Skills are plain Markdown on purpose — what
the agent writes, a human can read, edit in place, delete, or commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .tools.base import Tool, schema

# Skill names become directory names, so they are a strict slug rather than
# anything sanitised after the fact — no traversal, no spaces, no surprises.
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")

WRITE_DESCRIPTION = """Record durable know-how as a skill for future sessions.

Use this after working something out that would save time next time: a
project's conventions, a multi-step procedure, the fix for a failure that
keeps recurring. Write the body as instructions to your future self.

Not for one-off facts, and never for secrets — skills are plain files that
persist on disk and are often committed to a repo."""


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


def _render(name: str, description: str, body: str) -> str:
    """Serialise a skill. yaml.safe_dump so a colon in the description
    cannot corrupt the frontmatter it is written into."""
    meta = yaml.safe_dump(
        {"name": name, "description": description}, sort_keys=False, allow_unicode=True
    ).strip()
    return f"---\n{meta}\n---\n\n{body.strip()}\n"


class SkillLibrary:
    def __init__(self, search_paths: list[Path]) -> None:
        self.search_paths = [Path(p).expanduser() for p in search_paths]
        self.skills: dict[str, Skill] = {}
        for root in self.search_paths:
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

    def write(
        self,
        name: str,
        description: str,
        body: str,
        scope: str = "user",
        overwrite: bool = False,
    ) -> Skill:
        """Create or replace a skill, on disk and in this library."""
        if not SLUG.fullmatch(name):
            raise ValueError(
                f"invalid skill name {name!r}: use lowercase letters, digits and hyphens, "
                "e.g. 'deploy-staging'"
            )
        description = " ".join(description.split())  # the catalogue is one line per skill
        if not description:
            raise ValueError("a skill needs a description — it is what future you matches on")
        if not body.strip():
            raise ValueError("a skill needs a body")

        existing = self.skills.get(name)
        if existing:
            if not overwrite:
                raise ValueError(
                    f"skill {name!r} already exists at {existing.path}. Read it with "
                    "load_skill first, then pass overwrite=true to replace it."
                )
            path = existing.path  # replace where it lives, so scope can't shadow it
        else:
            path = self._root_for(scope) / name / "SKILL.md"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(name, description, body), encoding="utf-8")

        skill = Skill(name=name, description=description, body=body.strip(), path=path)
        self.skills[name] = skill
        return skill

    def _root_for(self, scope: str) -> Path:
        if not self.search_paths:
            raise ValueError("no skill paths are configured")
        if scope == "user":
            return self.search_paths[0]
        if scope == "project":
            return self.search_paths[-1]
        raise ValueError(f"unknown scope {scope!r}: use 'user' or 'project'")

    def tools(self) -> list[Tool]:
        return [self._load_tool(), self._write_tool()]

    def _load_tool(self) -> Tool:
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

    def _write_tool(self) -> Tool:
        def write_skill(
            name: str,
            description: str,
            body: str,
            scope: str = "user",
            overwrite: bool = False,
        ) -> str:
            skill = self.write(name, description, body, scope=scope, overwrite=overwrite)
            return (
                f"Saved skill {skill.name!r} to {skill.path}. "
                "You can load_skill it now; it joins the catalogue in new sessions."
            )

        return Tool(
            name="write_skill",
            description=WRITE_DESCRIPTION,
            parameters=schema(
                name={
                    "type": "string",
                    "description": "Slug: lowercase letters, digits, hyphens",
                },
                description={
                    "type": "string",
                    "description": "One line saying when to use this skill. This is all "
                    "a future session sees until it loads the body.",
                },
                body={"type": "string", "description": "Markdown instructions"},
                scope={
                    "type": "string",
                    "enum": ["user", "project"],
                    "description": "'user' for know-how that travels with you, "
                    "'project' for this repo only",
                    "default": "user",
                },
                overwrite={"type": "boolean", "default": False},
            ),
            handler=write_skill,
            requires_approval=True,
        )
