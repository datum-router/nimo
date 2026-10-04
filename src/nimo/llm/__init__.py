"""LLM client + swappable backends.

`LLMClient` (from client.py) is the OpenAI-compatible client nimo already
used; it defaults to Pollinations.ai (free, no key) and is the default
backend for the free tier and demos.

`get_backend(name)` returns a configured client for a named provider. The
`null` backend runs the engine with NO network LLM — deterministic, used for
CI smoke tests and offline demos (the VALOR deterministic-fallback path).
"""
from __future__ import annotations

from .client import LLMClient

__all__ = ["LLMClient", "get_backend", "NullBackend"]


class NullBackend:
    """Deterministic no-LLM backend. Returns a fixed 'do nothing / back'
    action so the loop runs end-to-end with no network and no key. The
    engine's deterministic crawler (VALOR path) does the real exploration;
    this only satisfies the `LLMClient.chat` interface in CI."""

    base_url = "null://deterministic"
    model = "null"

    def chat(self, messages, temperature: float = 0.0,
             response_format: dict | None = None) -> str:
        # A valid, inert action the executor understands. The deterministic
        # crawler never actually consults this unless forced; when it does,
        # 'back' is the safe no-progress action.
        return '{"action": "back"}'


def get_backend(name: str | None = None, **kwargs) -> "LLMClient | NullBackend":
    """Resolve a backend by name.

    name:
      None / "pollinations" / "openai" -> OpenAI-compatible LLMClient
                                           (env vars pick the real provider)
      "null"                           -> deterministic NullBackend
    Any other name is treated as an OpenAI-compatible endpoint selected via
    the standard NIMO_BASE_URL / NIMO_MODEL env vars.
    """
    if name == "null":
        return NullBackend()
    return LLMClient(**kwargs)
