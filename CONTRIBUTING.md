# Contributing

Thanks for looking. Odyssey is early, which means changes are cheap and
opinions are welcome.

## Getting set up

```bash
pip install -e ".[server,dev]"
pytest
ruff check .
```

Tests stub the provider entirely — no network access and no API key required to
run the suite. Please keep it that way; a test that needs a key is a test
nobody runs.

## What helps most right now

- New tools (each one is a `Tool` in `core/tools/`)
- Frontends that consume the event stream
- Provider coverage: try a model we haven't, tell us what broke
- Documentation, especially setup on Windows

## House style

- The core stays I/O-free. If your change makes `core/agent.py` print, read a
  file directly, or know what a terminal is, it belongs in a frontend instead.
- New capabilities should be MCP servers unless there's a reason they can't be.
- Tool failures return error strings; they don't raise. The model reads the
  error and adapts.
- Run `ruff check .` before opening a PR.

## On the license

Odyssey is AGPL-3.0. That's deliberate and worth understanding before you
contribute.

Permissive licenses like MIT let anyone take the project, host it as a paid
service, and contribute nothing back. The AGPL closes that loop: if you run
modified Odyssey as a network service, you have to publish your changes. It's
the standard choice for projects that intend to offer a hosted tier themselves,
because it keeps that ground from being taken by a better-funded competitor
running your own code against you.

The trade-off is real. Some companies won't touch AGPL software, and some
contributors won't work on it. If Odyssey ever wants corporate adoption, the
usual answer is dual licensing — AGPL for everyone, a commercial license for
those who need one — which requires a contributor licensing agreement so the
project can relicense. There isn't a CLA today. If one is introduced it will be
discussed in the open first, and nothing already contributed gets relicensed
without the author's agreement.

None of this is legal advice. If you're contributing on an employer's time,
check with them.

## Reporting security issues

Please don't open a public issue for anything exploitable. Email the maintainer
instead, and give us a reasonable window before disclosure.
