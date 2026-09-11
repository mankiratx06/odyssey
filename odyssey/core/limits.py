"""Usage limits.

Nothing here enforces anything yet — Odyssey is local and single-user today.
This exists so the hosted tiers land as a new Limiter implementation rather
than as surgery on the agent loop.

The shape matters: `reserve` runs BEFORE a model call and `settle` runs after
with the real token counts. Checking only afterwards means every user can
overrun their quota by one expensive request, which at scale is the whole
quota.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class QuotaExceeded(Exception):
    """Raised by a Limiter when a call must not proceed."""


@dataclass
class Reservation:
    """Handle returned by reserve(), passed back to settle()."""

    key: str
    estimated_tokens: int = 0


class Limiter(Protocol):
    async def reserve(self, model: str, estimated_tokens: int) -> Reservation: ...

    async def settle(
        self, reservation: Reservation, input_tokens: int, output_tokens: int
    ) -> None: ...


class Unlimited:
    """Default. Local users spend their own money and police themselves."""

    async def reserve(self, model: str, estimated_tokens: int) -> Reservation:
        return Reservation(key="local", estimated_tokens=estimated_tokens)

    async def settle(self, reservation: Reservation, input_tokens: int, output_tokens: int) -> None:
        return None


class MessageBudget:
    """In-memory message counter. A worked example, not production-ready.

    A real deployment needs shared state (Redis, Postgres) so limits survive a
    restart and hold across processes.
    """

    def __init__(self, max_messages: int) -> None:
        self.max_messages = max_messages
        self.used = 0

    async def reserve(self, model: str, estimated_tokens: int) -> Reservation:
        if self.used >= self.max_messages:
            raise QuotaExceeded(
                f"Free tier limit reached ({self.max_messages} messages). "
                "Add your own API key to keep going."
            )
        self.used += 1
        return Reservation(key="budget", estimated_tokens=estimated_tokens)

    async def settle(self, reservation: Reservation, input_tokens: int, output_tokens: int) -> None:
        return None
