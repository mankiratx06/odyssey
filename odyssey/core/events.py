"""Events.

The agent emits a stream of these. The terminal renders them, the HTTP server
serialises them to SSE, and a desktop app will deserialise them back. Because
every frontend consumes the same contract, adding one never touches the core.

Keep this module dependency-free and JSON-serialisable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass
class Event:
    type: str = field(init=False, default="event")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type
        return data


@dataclass
class TextDelta(Event):
    """A chunk of assistant text, as it streams."""

    text: str

    def __post_init__(self) -> None:
        self.type = "text_delta"


@dataclass
class ToolStart(Event):
    call_id: str
    name: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        self.type = "tool_start"


@dataclass
class ToolEnd(Event):
    call_id: str
    name: str
    result: str
    ok: bool = True

    def __post_init__(self) -> None:
        self.type = "tool_end"


@dataclass
class Usage(Event):
    """Token counts for one model call. Metering and billing hang off this."""

    model: str
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        self.type = "usage"


@dataclass
class Done(Event):
    text: str
    reason: Literal["stop", "max_steps", "limit"] = "stop"

    def __post_init__(self) -> None:
        self.type = "done"


@dataclass
class Failed(Event):
    message: str
    kind: str = "error"

    def __post_init__(self) -> None:
        self.type = "failed"
