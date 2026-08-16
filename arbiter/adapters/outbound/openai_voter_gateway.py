"""OpenAI-compatible ``/v1/chat/completions`` voter gateway."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx2


@dataclass(frozen=True)
class CompletionResult:
    ok: bool
    text: str | None
    raw: dict[str, Any] | None
    latency_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None


class OpenAIVoterGateway:
    def __init__(self, *, http_client: httpx2.AsyncClient | None = None) -> None:
        self._http = http_client
        self._owns_http = http_client is None

    async def __aenter__(self) -> OpenAIVoterGateway:
        if self._http is None:
            self._http = httpx2.AsyncClient()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    async def complete(
        self,
        *,
        base_url: str,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        messages: list[dict[str, str]],
        api_key: str | None,
    ) -> CompletionResult:
        if self._http is None:
            self._http = httpx2.AsyncClient()
            self._owns_http = True
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        started = time.perf_counter()
        try:
            response = await self._http.post(
                url, headers=headers, json=body, timeout=timeout_seconds
            )
        except httpx2.TimeoutException:
            return CompletionResult(
                ok=False,
                text=None,
                raw=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=None,
                completion_tokens=None,
                error="timeout",
            )
        except httpx2.HTTPError as exc:
            return CompletionResult(
                ok=False,
                text=None,
                raw=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=None,
                completion_tokens=None,
                error=f"http_error:{type(exc).__name__}",
            )
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code == 401:
            return CompletionResult(
                ok=False,
                text=None,
                raw={"status_code": 401, "body": response.text},
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                error="http_401",
            )
        if response.status_code >= 400:
            return CompletionResult(
                ok=False,
                text=None,
                raw={"status_code": response.status_code, "body": response.text},
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                error=f"http_{response.status_code}",
            )
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return CompletionResult(
                ok=False,
                text=None,
                raw={"status_code": response.status_code, "body": response.text},
                latency_ms=latency_ms,
                prompt_tokens=None,
                completion_tokens=None,
                error="unparseable_response",
            )
        text = _extract_message_text(payload)
        usage = payload.get("usage") if isinstance(payload, dict) else None
        prompt_tokens = None
        completion_tokens = None
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            ct = usage.get("completion_tokens")
            if isinstance(pt, int):
                prompt_tokens = pt
            if isinstance(ct, int):
                completion_tokens = ct
        if text is None:
            return CompletionResult(
                ok=False,
                text=None,
                raw=payload if isinstance(payload, dict) else {"body": payload},
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error="unparseable_response",
            )
        return CompletionResult(
            ok=True,
            text=text,
            raw=payload if isinstance(payload, dict) else {"body": payload},
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=None,
        )


def _extract_message_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    text = first.get("text")
    if isinstance(text, str):
        return text
    return None
