from __future__ import annotations

import json
import os
from typing import Any, Protocol, runtime_checkable

import anthropic
import openai


class RateLimitError(Exception):
    """Raised when an LLM provider returns a rate-limit response."""
    pass


class AuthError(Exception):
    """Raised when an LLM provider rejects the API key."""
    pass


class LLMError(Exception):
    """Raised for any other LLM client failure (network, parse, etc.)."""
    pass


@runtime_checkable
class CompletionClient(Protocol):
    async def complete(self, prompt: str, system: str | None = None, **kwargs) -> str: ...

    async def complete_with_structured_output(
        self, prompt: str, output_schema: type, system: str | None = None, **kwargs
    ) -> Any: ...


def _build_schema_dict(output_schema: type) -> dict[str, Any]:
    """Build an OpenAI-compatible JSON schema dict from a Pydantic model class."""
    schema_name = output_schema.__name__
    schema_dict: dict[str, Any] = {"name": schema_name}
    if hasattr(output_schema, "model_json_schema"):
        schema_dict["schema"] = output_schema.model_json_schema()
    elif hasattr(output_schema, "schema"):
        schema_dict["schema"] = output_schema.schema()
    return schema_dict


def _strip_code_fence(content: str) -> str:
    """Strip markdown code fences and <think> blocks from LLM responses."""
    import re
    content = content.strip()
    # Remove thinking tags that some models output (e.g., MiniMax deep thinking)
    content = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
    if content.startswith("```"):
        parts = content.split("```", 2)
        if len(parts) >= 3:
            content = parts[1]
            content = content.lstrip("json").strip()
    return content


class OpenAIClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        _api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not _api_key:
            raise ValueError("OPENAI_API_KEY is not set or is empty")
        _base_url = base_url or os.environ.get("OPENAI_BASE_URL") or None
        if _base_url is not None:
            _base_url = _base_url.rstrip("/")
        self._client = openai.AsyncOpenAI(api_key=_api_key, base_url=_base_url)
        self._model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")

    async def complete(self, prompt: str, system: str | None = None, **kwargs) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                **kwargs,
            )
            if not response.choices:
                raise LLMError("OpenAI response has no choices")
            content = response.choices[0].message.content or ""
            return content
        except openai.RateLimitError:
            raise RateLimitError("OpenAI rate limit exceeded")
        except openai.AuthenticationError:
            raise AuthError("OpenAI authentication failed")
        except Exception as exc:
            raise LLMError(f"OpenAI error: {exc}") from exc

    async def complete_with_structured_output(
        self, prompt: str, output_schema: type, system: str | None = None, **kwargs
    ) -> Any:
        raw_content: str = ""
        schema_dict = _build_schema_dict(output_schema)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Try json_object mode first (simpler, more widely supported)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
                **kwargs,
            )
            if not response.choices:
                raise LLMError("OpenAI response has no choices")
            raw_content = response.choices[0].message.content or ""
            content = _strip_code_fence(raw_content)
            result = json.loads(content)
            # Validate against schema
            output_schema(**result)
            return result
        except Exception as exc:
            pass  # Fall through to fallback

        # Fallback: use schema in prompt, no response_format constraint
        return await self._complete_with_fallback(prompt, output_schema, system, **kwargs)

    async def _complete_with_fallback(
        self, prompt: str, output_schema: type, system: str | None, **kwargs
    ) -> Any:
        schema_dict = _build_schema_dict(output_schema)
        schema_json = json.dumps(schema_dict, indent=2)
        full_prompt = f"""{prompt}

Respond with valid JSON matching this schema:
{schema_json}"""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": full_prompt})
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                **kwargs,
            )
        except openai.RateLimitError:
            raise RateLimitError("OpenAI rate limit exceeded in fallback")
        except openai.AuthenticationError:
            raise AuthError("OpenAI authentication failed in fallback")
        except openai.BadRequestError as exc:
            raise LLMError(f"OpenAI BadRequestError in fallback (no more fallback): {exc}") from exc
        if not response.choices:
            raise LLMError("OpenAI response has no choices in fallback")
        raw_content = response.choices[0].message.content or ""
        content = _strip_code_fence(raw_content)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"OpenAI fallback JSON decode error: {exc}, raw response: {raw_content[:500]}") from exc


class AnthropicClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        _api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not _api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set or is empty")
        self._client = anthropic.AsyncAnthropic(api_key=_api_key, base_url=base_url or os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/"))
        self._model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    async def complete(self, prompt: str, system: str | None = None, **kwargs) -> str:
        max_tokens = kwargs.pop("max_tokens", 4096)
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            if not response.content:
                raise LLMError("Anthropic response has no content")
            return response.content[0].text
        except anthropic.RateLimitError:
            raise RateLimitError("Anthropic rate limit exceeded")
        except anthropic.AuthenticationError:
            raise AuthError("Anthropic authentication failed")
        except Exception as exc:
            raise LLMError(f"Anthropic error: {exc}") from exc

    async def complete_with_structured_output(
        self, prompt: str, output_schema: type, system: str | None = None, **kwargs
    ) -> Any:
        schema_dict = _build_schema_dict(output_schema)
        schema_json = json.dumps(schema_dict, indent=2)
        full_prompt = f"""{prompt}

Respond with valid JSON matching this schema:
{schema_json}"""
        text = await self.complete(full_prompt, system=system, **kwargs)
        content = _strip_code_fence(text)
        return json.loads(content)


def get_client() -> CompletionClient:
    """Return the first available LLM client (Anthropic preferred, then OpenAI).

    Raises RuntimeError if neither API key is configured.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if anthropic_key:
        return AnthropicClient()
    if openai_key:
        return OpenAIClient()
    raise RuntimeError("No LLM provider configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.")