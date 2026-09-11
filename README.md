# Odyssey AI

An open-source AI agent you run yourself. Bring your own keys, use any provider,
chat and write code in the same place.

Terminal today. Desktop and web next — all three share one core.

```bash
pip install -e .
odyssey keys set anthropic
odyssey
```

## Why another one

Most AI tools pick a model for you and rent it back. Odyssey doesn't hold your
keys, doesn't mark up your tokens, and doesn't care which provider you use.
Point it at Claude, GPT, Gemini, an OpenRouter free model, or something running
on your own hardware — it's a line of config either way.

## Status

Early. The core works and is tested; expect the API to move. Not yet suitable
for unattended use — see [Security](#security).

## Install

```bash
git clone https://github.com/mankiratx06/odyssey
cd odyssey
pip install -e ".[server,dev]"
```

Then give it a key. Either store one in your OS keychain:

```bash
odyssey keys set anthropic
```

Or export it, which is what CI and containers want:

```bash
export ANTHROPIC_API_KEY=sk-...
```

## Use

```bash
odyssey                            # interactive chat
odyssey "why is this test failing"  # one-shot
odyssey --role code                # route to the stronger coding model
odyssey --role free                # route to a free OpenRouter model
odyssey --usage                    # print token counts per call
odyssey serve                      # HTTP + SSE backend on :8765
```

In chat: `/model <role>`, `/tools`, `/reset`, `/exit`.

## Config

`~/.config/odyssey/config.toml`. Everything is optional; see `config.example.toml`.

```toml
workspace = "."

[models]
chat  = "anthropic/claude-sonnet-5"
code  = "anthropic/claude-opus-5"
cheap = "anthropic/claude-haiku-4-5-20251001"
free  = "openrouter/openrouter/free"

[mcp.servers.images]
command = "npx"
args = ["-y", "some-image-mcp-server"]
```

Model strings are [LiteLLM](https://docs.litellm.ai/)'s, so `openai/…`,
`gemini/…`, `openrouter/…`, `groq/…` and `ollama/…` work unchanged.

## Architecture

```
odyssey/
  config.py              config, model routing, credential sources
  core/
    agent.py             the loop — yields events, does no I/O
    events.py            the contract every frontend consumes
    limits.py            quota seam for hosted tiers later
    skills.py            SKILL.md loader, progressive disclosure
    tools/base.py        Tool + registry; everything normalises here
    tools/builtin.py     read / write / edit / list / shell
    tools/mcp.py         mounts MCP servers as tools
  cli/app.py             terminal frontend
  server/app.py          HTTP + SSE backend for desktop and web
```

Four decisions carry the design:

**LiteLLM owns providers.** No hand-written adapters, ever. Swapping models is
config.

**The core yields events, it doesn't print.** `Agent.send()` returns an async
stream of typed events. The terminal renders them; the server serialises them to
SSE; a desktop client will deserialise them back. Adding a frontend never
touches the core.

**MCP is the extension boundary.** Image generation, video, browser control, a
database — each arrives as a server in config, not as core code. You inherit
the whole MCP ecosystem instead of building integrations.

**Seams before features.** `Limiter` and `CredentialSource` are no-ops today.
They exist so hosted tiers and per-user keys land as new implementations rather
than as surgery on the loop.

## Skills

A skill is a directory with a `SKILL.md`:

```markdown
---
name: deploy-staging
description: How to deploy this repo to staging. Use when asked to ship.
---

1. Run the test suite
2. ...
```

Drop it in `~/.config/odyssey/skills/` or `./.odyssey/skills/`. Only names and
descriptions go into the system prompt; the body loads on demand, so a hundred
skills cost you almost no context.

## Security

`run_shell`, `write_file` and `edit_file` ask before running. Workspace
confinement keeps the filesystem tools inside one directory.

That is a guardrail, **not a sandbox**. `-y` disables the prompts, and the HTTP
server runs tools unattended by design. Before pointing this at anything you
care about, or exposing `serve` beyond localhost, put execution in a container.
An agent with shell access that also reads web content is a prompt-injection
target — treat retrieved text as untrusted input, not instructions.

## Development

```bash
pip install -e ".[server,dev]"
pytest          # no network, no keys needed
ruff check .
```

## Roadmap

- [ ] `write_skill` — the agent records what it learns
- [ ] persistent sessions and conversation search
- [ ] context compaction when history outgrows the window
- [ ] desktop shell over the HTTP backend
- [ ] web client
- [ ] hosted tier: auth, per-user key vault, real quota backend

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE) and the note in
[CONTRIBUTING.md](CONTRIBUTING.md) about why.
