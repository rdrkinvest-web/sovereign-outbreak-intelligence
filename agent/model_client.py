"""Thin model client over Flower's OpenAI-compatible Responses endpoint.

Wrapped behind a Protocol so tests can inject a scripted fake. Flower injects
FLWR_RUNTIME_BASE_URL / FLWR_RUNTIME_API_KEY into every AgentApp process, on
SuperGrid and on SuperNodes alike.
"""

from __future__ import annotations

import os
from typing import Any, Protocol


# A healthy call takes seconds; a failing provider was seen taking ~80 s to
# return 502. Cap each call so an outage falls back well inside SuperGrid's
# 5-minute task limit.
MODEL_TIMEOUT_SECONDS = 45.0


class ModelClient(Protocol):
    def respond(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> list[dict[str, Any]]:
        """Return the response's output items as plain dicts."""
        ...


class FlowerModelClient:
    """Connects on first use, so paths that never need a model never touch one."""

    def __init__(
        self, model: str, max_output_tokens: int = 1200, timeout: float = MODEL_TIMEOUT_SECONDS
    ) -> None:
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout
        self._client: Any = None

    def _connect(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
                api_key=os.environ["FLWR_RUNTIME_API_KEY"],
                max_retries=0,  # each request creates a Flower model task
                timeout=self._timeout,  # a failing provider falls back fast
            )
        return self._client

    def respond(
        self,
        *,
        instructions: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> list[dict[str, Any]]:
        response = self._connect().responses.create(
            model=self._model,
            instructions=instructions,
            input=input_items,
            tools=tools,
            tool_choice=tool_choice,
            max_output_tokens=self._max_output_tokens,
        )
        return [item.to_dict() for item in response.output]
