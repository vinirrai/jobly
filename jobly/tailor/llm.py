"""Thin wrapper around the Claude API returning validated Pydantic objects."""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Models that accept server-side refusal fallbacks ("default" routing).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    model: str

    def structured(self, *, system: list[dict], messages: list[dict], schema: type[T]) -> T: ...


class ClaudeLLM:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high",
                 client: anthropic.Anthropic | None = None, max_tokens: int = 64000):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = client or anthropic.Anthropic()

    def structured(self, *, system: list[dict], messages: list[dict], schema: type[T]) -> T:
        output_config: dict = {"format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}}
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            output_config=output_config,
        )
        if self.model in _FALLBACK_MODELS:
            # If a safety classifier declines, the API retries on a recommended model.
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            # Streaming avoids HTTP timeouts on long, high-effort generations.
            with self.client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("Claude API key missing or invalid — set ANTHROPIC_API_KEY.") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"The API key can't use {self.model}: {e.message}") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"Unknown model {self.model!r} — check ai.model in config.yaml.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude API rate limit hit — try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Couldn't reach the Claude API — check your internet connection.") from e

        if msg.stop_reason == "refusal":
            raise LLMError("Claude declined this request.")
        if msg.stop_reason == "max_tokens":
            raise LLMError("The response was cut off (max_tokens). Try a lower effort setting.")
        usage = msg.usage
        log.info("claude %s: in=%s cached=%s out=%s", schema.__name__, usage.input_tokens,
                 getattr(usage, "cache_read_input_tokens", 0), usage.output_tokens)
        text = "".join(b.text for b in msg.content if b.type == "text")
        try:
            return schema.model_validate_json(text)
        except ValidationError as e:
            raise LLMError(f"Claude returned data that didn't match {schema.__name__}: {e}") from e
