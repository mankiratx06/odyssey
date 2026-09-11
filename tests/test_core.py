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

    body = library.tool().handler(name="deploy")
    assert "Run tests, then push." in body


def test_quota_exceeded_is_raised():
    budget = MessageBudget(max_messages=1)
    import asyncio

    asyncio.run(budget.reserve("m", 10))
    with pytest.raises(QuotaExceeded):
        asyncio.run(budget.reserve("m", 10))
