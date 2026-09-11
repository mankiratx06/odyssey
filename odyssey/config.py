"""Configuration and credentials.

Credential resolution is deliberately pluggable. In the CLI, keys come from the
environment or the OS keychain. In a hosted deployment they will come from a
per-user vault instead — implement `CredentialSource` and nothing else changes.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

APP_NAME = "odyssey"
CONFIG_DIR = Path(os.environ.get("ODYSSEY_CONFIG_DIR", Path.home() / ".config" / APP_NAME))
CONFIG_PATH = CONFIG_DIR / "config.toml"

# LiteLLM model strings: "provider/model". Swap freely, no code changes.
DEFAULTS: dict[str, Any] = {
    "models": {
        "chat": "anthropic/claude-sonnet-5",
        "code": "anthropic/claude-opus-5",
        "cheap": "anthropic/claude-haiku-4-5-20251001",
        "free": "openrouter/openrouter/free",
    },
    "limits": {"max_steps": 25, "max_tokens": 8192},
    "workspace": ".",
    "skills": {"paths": ["~/.config/odyssey/skills", "./.odyssey/skills"]},
    "mcp": {"servers": {}},
}

PROVIDER_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


def provider_of(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else "anthropic"


class CredentialSource(Protocol):
    """Returns the API key for a provider, or None if unavailable."""

    def get(self, provider: str) -> str | None: ...


class EnvKeychainCredentials:
    """Local-first: environment variable, then the OS keychain."""

    def get(self, provider: str) -> str | None:
        env_var = PROVIDER_ENV.get(provider)
        if not env_var:
            return None
        if value := os.environ.get(env_var):
            return value
        try:
            import keyring

            return keyring.get_password(APP_NAME, env_var)
        except Exception:  # no keychain backend available
            return None


class StaticCredentials:
    """For hosted use: keys supplied per request. Never persisted here."""

    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = keys

    def get(self, provider: str) -> str | None:
        return self._keys.get(provider)


@dataclass
class Config:
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULTS["models"]))
    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULTS["limits"]))
    workspace: Path = field(default_factory=Path.cwd)
    skill_paths: list[Path] = field(default_factory=list)
    mcp_servers: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        raw: dict[str, Any] = _deep_copy(DEFAULTS)
        path = path or CONFIG_PATH
        if path.exists():
            with path.open("rb") as fh:
                _deep_merge(raw, tomllib.load(fh))
        return cls(
            models=raw["models"],
            limits=raw["limits"],
            workspace=Path(raw["workspace"]).expanduser().resolve(),
            skill_paths=[Path(p).expanduser() for p in raw["skills"]["paths"]],
            mcp_servers=raw["mcp"].get("servers", {}),
        )

    def model_for(self, role: str) -> str:
        return self.models.get(role, self.models["chat"])


def _deep_copy(source: dict) -> dict:
    return {k: _deep_copy(v) if isinstance(v, dict) else v for k, v in source.items()}


def _deep_merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
