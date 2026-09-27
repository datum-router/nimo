"""LLM client for nimo.

Talks to any OpenAI-compatible chat-completions endpoint. Defaults to
Pollinations.ai, which needs no API key and no signup:

    base_url = https://text.pollinations.ai/openai
    model    = openai          (alias for their default model)

Swap providers with env vars only — no code changes:

    NIMO_BASE_URL  e.g. https://generativelanguage.googleapis.com/v1beta/openai/
    NIMO_API_KEY   provider key (Pollinations: leave unset)
    NIMO_MODEL     e.g. gemini-2.5-flash

Stdlib only: no `openai` package required.
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_BASE_URL = "https://text.pollinations.ai/openai"
DEFAULT_MODEL = "openai"


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or os.environ.get("NIMO_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.environ.get("NIMO_API_KEY", "not-needed")
        self.model = model or os.environ.get("NIMO_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def chat(self, messages: list[dict], temperature: float = 0.2,
             response_format: dict | None = None) -> str:
        """Send a chat request, return the assistant message text."""
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode())
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {data!r}") from exc
