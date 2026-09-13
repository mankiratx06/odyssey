"""HTTP server.

The same core, exposed over HTTP with server-sent events. A desktop shell
(Tauri, Electron) and a web client both talk to this, so neither needs to
reimplement the agent.

Not production-ready and not meant to be yet: sessions are in memory, there is
no auth, and it binds to localhost. Those are the things that turn into the
hosted tier later, and each has a seam waiting for it — `Limiter` for quota,
`CredentialSource` for per-user keys.

Run with:  odyssey serve
"""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..config import Config, EnvKeychainCredentials, StaticCredentials
from ..core.agent import Agent, approve_all
from ..core.skills import SkillLibrary
from ..core.tools import builtin
from ..core.tools.base import ToolRegistry


@dataclass
class SessionStore:
    """In-memory sessions. Swap for Redis or Postgres when this goes multi-user."""

    sessions: dict[str, Agent] = field(default_factory=dict)

    def create(self, config: Config, **overrides: Any) -> tuple[str, Agent]:
        registry = ToolRegistry()
        registry.extend(builtin.build(config.workspace))
        skills = SkillLibrary(config.skill_paths)
        registry.extend(skills.tools())

        agent = Agent(
            config=config,
            registry=registry,
            extra_system=skills.catalogue(),
            # Server-side, tools run unattended. Sandbox before exposing this
            # to anyone but yourself.
            approver=approve_all,
            **overrides,
        )
        session_id = uuid.uuid4().hex
        self.sessions[session_id] = agent
        return session_id, agent

    def get(self, session_id: str) -> Agent | None:
        return self.sessions.get(session_id)


def create_app(config: Config | None = None):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel

    config = config or Config.load()
    store = SessionStore()
    app = FastAPI(title="Odyssey AI", version="0.1.0")

    class NewSession(BaseModel):
        role: str = "chat"
        # BYOK: provider -> key, held for this session only, never written to disk.
        api_keys: dict[str, str] | None = None

    class Message(BaseModel):
        session_id: str
        message: str

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "models": config.models}

    @app.post("/sessions")
    async def new_session(body: NewSession) -> dict[str, str]:
        credentials = (
            StaticCredentials(body.api_keys) if body.api_keys else EnvKeychainCredentials()
        )
        session_id, agent = store.create(config, credentials=credentials, role=body.role)
        return {"session_id": session_id, "model": agent.config.model_for(agent.role)}

    @app.post("/chat")
    async def chat(body: Message) -> StreamingResponse:
        agent = store.get(body.session_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="unknown session")

        async def stream() -> AsyncIterator[str]:
            async for event in agent.send(body.message):
                yield f"data: {json.dumps(event.to_dict())}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)
