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
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://text.pollinations.ai/openai"
DEFAULT_MODEL = "openai"


class LLMUnavailable(RuntimeError):
    """The LLM backend could not be reached, or refused the request.

    Kept separate from a bad-response error so a caller can distinguish "the
    provider is down or unconfigured" (the operator must act) from "the model
    replied with something unusable" (the prompt must act).
    """


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 120,
        retries: int = 3,
        backoff: float = 2.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("NIMO_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        # An unset key must stay EMPTY, never a placeholder. Sending
        # `Authorization: Bearer not-needed` made Pollinations classify the
        # call as authenticated and refuse it outright (their legacy text API
        # is deprecated for authenticated users).
        self.api_key = api_key or os.environ.get("NIMO_API_KEY") or ""
        self.model = model or os.environ.get("NIMO_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    def chat(self, messages: list[dict], temperature: float = 0.2,
             response_format: dict | None = None) -> str:
        """Send a chat request, return the assistant message text.

        Transient upstream failures (429, 5xx) are retried with exponential
        backoff: one hiccup from the provider must not discard a 20-minute
        emulator run. A terminal failure raises LLMUnavailable, whose message
        names the endpoint, the status and the way out — rather than surfacing
        a bare urllib traceback.
        """
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode(),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode()[:200]
                except Exception:
                    pass
                detail = f"HTTP {exc.code} {exc.reason}{(': ' + body) if body else ''}"
                # Anything 4xx except 429 is a configuration problem; retrying
                # a 401/402/404 only burns the device's remaining budget.
                if not (exc.code == 429 or exc.code >= 500) or attempt == self.retries:
                    raise LLMUnavailable(self._advice(detail)) from exc
            except urllib.error.URLError as exc:
                if attempt == self.retries:
                    raise LLMUnavailable(
                        self._advice(f"network error: {exc.reason}")) from exc
            time.sleep(self.backoff * (2 ** attempt))

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {data!r}") from exc

    def _advice(self, detail: str) -> str:
        return (
            f"LLM backend unavailable: {self.base_url} (model {self.model}) — {detail}. "
            "Point nimo at a backend you control via NIMO_BASE_URL / NIMO_API_KEY / "
            "NIMO_MODEL, or set NIMO_BACKEND=null for a deterministic device-only run."
        )
