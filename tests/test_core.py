"""Tests for the core. No network, no API keys — everything is stubbed."""

from __future__ import annotations

import json
import types

import pytest

from odyssey.config import Config, StaticCredentials
from odyssey.core import agent as agent_module
from odyssey.core.agent import Agent
from odyssey.core.events import Done, Failed, TextDelta, ToolEnd, ToolStart, Usage
from odyssey.core.limits import MessageBudget, QuotaExceeded
from odyssey.core.skills import SkillLibrary
from odyssey.core.tools import builtin
from odyssey.core.tools.base import ToolRegistry


def chunk(content=None, tool_calls=None, usage=None, has_choice=True):
    delta = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    choices = [types.SimpleNamespace(delta=delta)] if has_choice else []
    return types.SimpleNamespace(choices=choices, usage=usage)


def tool_piece(index, call_id=None, name=None, arguments=None):
    return types.SimpleNamespace(
        index=index,
        id=call_id,
        function=types.SimpleNamespace(name=name, arguments=arguments),
    )


def fake_stream(turns):
    """Replaces litellm.acompletion; each call plays back the next turn."""
    remaining = list(turns)

    async def acompletion(**kwargs):
        this_turn = remaining.pop(0)

        async def gen():
            for item in this_turn:
                yield item

        return gen()

    return acompletion


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


@pytest.fixture
def registry(workspace):
    reg = ToolRegistry()
    reg.extend(builtin.build(workspace))
    return reg


@pytest.fixture
def config(workspace):
    cfg = Config.load(path=workspace / "nonexistent.toml")
    cfg.workspace = workspace
    return cfg


def build_agent(config, registry, **kwargs):
    return Agent(
        config=config,
        registry=registry,
        credentials=StaticCredentials({"anthropic": "test-key"}),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_plain_text_turn_streams_deltas(config, registry, monkeypatch):
    monkeypatch.setattr(
        agent_module.litellm,
        "acompletion",
        fake_stream([[chunk("Hello "), chunk("there.")]]),
    )
    agent = build_agent(config, registry)
    events = [e async for e in agent.send("hi")]

    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    assert deltas == ["Hello ", "there."]
    assert isinstance(events[-1], Done)
    assert events[-1].text == "Hello there."


@pytest.mark.asyncio
async def test_tool_call_assembled_from_split_chunks(config, registry, workspace, monkeypatch):
    # Arguments arrive across several chunks — the accumulator must stitch them.
    args = json.dumps({"path": "note.txt", "content": "saved"})
    monkeypatch.setattr(
        agent_module.litellm,
        "acompletion",
        fake_stream(
            [
                [
                    chunk(tool_calls=[tool_piece(0, "c1", "write_file", args[:12])]),
                    chunk(tool_calls=[tool_piece(0, None, None, args[12:])]),
                ],
                [chunk("Saved it.")],
            ]
        ),
    )
    agent = build_agent(config, registry)
    events = [e async for e in agent.send("write note.txt")]

    starts = [e for e in events if isinstance(e, ToolStart)]
    ends = [e for e in events if isinstance(e, ToolEnd)]
    assert starts[0].name == "write_file"
    assert starts[0].arguments["content"] == "saved"
    assert ends[0].ok
    assert (workspace / "note.txt").read_text() == "saved"


@pytest.mark.asyncio
async def test_declined_tool_is_reported_not_run(config, registry, workspace, monkeypatch):
    args = json.dumps({"path": "nope.txt", "content": "x"})
    monkeypatch.setattr(
        agent_module.litellm,
        "acompletion",
        fake_stream(
            [
                [chunk(tool_calls=[tool_piece(0, "c1", "write_file", args)])],
                [chunk("Understood.")],
            ]
        ),
    )

    async def deny(tool, arguments):
        return False

    agent = build_agent(config, registry, approver=deny)
    events = [e async for e in agent.send("write it")]

    end = next(e for e in events if isinstance(e, ToolEnd))
    assert "declined" in end.result
    assert not (workspace / "nope.txt").exists()


@pytest.mark.asyncio
async def test_usage_is_emitted(config, registry, monkeypatch):
    usage = types.SimpleNamespace(prompt_tokens=11, completion_tokens=7)
    monkeypatch.setattr(
        agent_module.litellm,
        "acompletion",
        fake_stream([[chunk("hi"), chunk(usage=usage, has_choice=False)]]),
    )
    agent = build_agent(config, registry)
    events = [e async for e in agent.send("hi")]

    reported = next(e for e in events if isinstance(e, Usage))
    assert (reported.input_tokens, reported.output_tokens) == (11, 7)


@pytest.mark.asyncio
async def test_missing_credential_fails_cleanly(config, registry):
    agent = Agent(config=config, registry=registry, credentials=StaticCredentials({}))
    events = [e async for e in agent.send("hi")]
    assert isinstance(events[0], Failed)
    assert events[0].kind == "auth"


@pytest.mark.asyncio
async def test_quota_stops_the_turn(config, registry, monkeypatch):
    monkeypatch.setattr(agent_module.litellm, "acompletion", fake_stream([[chunk("hi")]]))
    agent = build_agent(config, registry, limiter=MessageBudget(max_messages=0))
    events = [e async for e in agent.send("hi")]
    assert any(isinstance(e, Failed) and e.kind == "quota" for e in events)


@pytest.mark.asyncio
async def test_workspace_confinement(registry):
    result = await registry.call("read_file", {"path": "../../etc/passwd"})
    assert "escapes workspace" in result


@pytest.mark.asyncio
async def test_unknown_tool_is_a_message_not_a_crash(registry):
    result = await registry.call("does_not_exist", {})
    assert "no tool named" in result


@pytest.mark.asyncio
async def test_edit_file_requires_a_unique_match(registry, workspace):
    (workspace / "dup.txt").write_text("a\na\n")
    approve = lambda tool, arguments: _true()  # noqa: E731
    result = await registry.call(
        "edit_file", {"path": "dup.txt", "old": "a", "new": "b"}, approve=approve
    )
    assert "matches 2 times" in result


async def _true():
    return True


def test_skill_library_progressive_disclosure(tmp_path):
    skill_dir = tmp_path / "deploy"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Ship to staging.\n---\nRun tests, then push.\n"
    )
    library = SkillLibrary([tmp_path])

    catalogue = library.catalogue()
    assert "deploy: Ship to staging." in catalogue
    assert "Run tests" not in catalogue  # body stays out of the system prompt

    body = skill_tool(library, "load_skill").handler(name="deploy")
    assert "Run tests, then push." in body


def test_quota_exceeded_is_raised():
    budget = MessageBudget(max_messages=1)
    import asyncio

    asyncio.run(budget.reserve("m", 10))
    with pytest.raises(QuotaExceeded):
        asyncio.run(budget.reserve("m", 10))


def skill_tool(library, name):
    return next(t for t in library.tools() if t.name == name)


@pytest.fixture
def library(tmp_path):
    user = tmp_path / "user"
    project = tmp_path / "project"
    user.mkdir()
    project.mkdir()
    return SkillLibrary([user, project])


def test_written_skill_is_readable_by_a_later_session(library, tmp_path):
    skill_tool(library, "write_skill").handler(
        name="run-tests",
        description="How this repo runs its tests.",
        body="Use `pytest -q` from the root.",
    )

    # A fresh library over the same paths: proves it reached disk, not just memory.
    reloaded = SkillLibrary([tmp_path / "user", tmp_path / "project"])
    assert "run-tests: How this repo runs its tests." in reloaded.catalogue()
    assert "pytest -q" in skill_tool(reloaded, "load_skill").handler(name="run-tests")


def test_scope_chooses_which_path_is_written(library, tmp_path):
    write = skill_tool(library, "write_skill").handler
    write(name="travels-with-me", description="d", body="b")
    write(name="this-repo-only", description="d", body="b", scope="project")

    assert (tmp_path / "user" / "travels-with-me" / "SKILL.md").exists()
    assert (tmp_path / "project" / "this-repo-only" / "SKILL.md").exists()


def test_skill_names_cannot_escape_their_directory(library, tmp_path):
    for bad in ["../escape", "/absolute", "has space", "Upper", ""]:
        with pytest.raises(ValueError, match="invalid skill name"):
            library.write(bad, "d", "b")
    assert not (tmp_path / "escape").exists()


def test_existing_skill_is_not_clobbered_silently(library):
    library.write("thing", "first", "one")
    with pytest.raises(ValueError, match="already exists"):
        library.write("thing", "second", "two")

    library.write("thing", "second", "two", overwrite=True)
    assert library.skills["thing"].body == "two"


def test_overwrite_replaces_in_place_rather_than_shadowing(library, tmp_path):
    library.write("thing", "d", "original", scope="project")
    library.write("thing", "d", "revised", scope="user", overwrite=True)

    # Written to the project path it already lived at, so one copy exists, not two.
    assert not (tmp_path / "user" / "thing").exists()
    assert SkillLibrary([tmp_path / "user", tmp_path / "project"]).skills["thing"].body == "revised"


def test_awkward_description_survives_the_round_trip(library, tmp_path):
    # A colon would break hand-rolled frontmatter; a newline would break the
    # one-line-per-skill catalogue.
    library.write("tricky", "Deploy: staging,\nthen prod", "body")

    reloaded = SkillLibrary([tmp_path / "user"])
    assert reloaded.skills["tricky"].description == "Deploy: staging, then prod"
    assert len(reloaded.catalogue().splitlines()) == 2  # header + one skill


@pytest.mark.asyncio
async def test_bad_skill_name_is_a_message_not_a_crash(library):
    registry = ToolRegistry()
    registry.extend(library.tools())
    result = await registry.call(
        "write_skill",
        {"name": "../evil", "description": "d", "body": "b"},
        approve=lambda tool, arguments: _true(),
    )
    assert "invalid skill name" in result


@pytest.mark.asyncio
async def test_writing_a_skill_asks_first(library):
    registry = ToolRegistry()
    registry.extend(library.tools())

    async def deny(tool, arguments):
        return False

    result = await registry.call(
        "write_skill", {"name": "sneaky", "description": "d", "body": "b"}, approve=deny
    )
    assert "declined" in result
    assert "sneaky" not in library.skills
