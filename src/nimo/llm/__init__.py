"""LLM client + swappable backends.

`LLMClient` (from client.py) is the OpenAI-compatible client nimo already
used; it defaults to Pollinations.ai (free, no key) and is the default
backend for the free tier and demos.

`get_backend(name)` returns a configured client for a named provider. The
`null` backend runs the engine with NO network LLM — deterministic, used for
CI smoke tests and offline demos (the VALOR deterministic-fallback path).
"""
from __future__ import annotations

import os

from .client import LLMClient, LLMUnavailable

__all__ = ["LLMClient", "LLMUnavailable", "get_backend", "NullBackend",
           "ResilientBackend", "degradation_occurred", "degradation_reason"]

# Process-wide degradation record.
#
# The engine builds more than one backend per run -- `pipeline.main` makes
# one and `repro.reproduce` makes its own -- so an outage hit inside repro is
# invisible to the pipeline's instance. The final report has to state whether
# AI guidance was available at any point, so the fact is recorded here rather
# than on one object that the reporting code may not be holding.
_DEGRADED: dict = {"hit": False, "reason": ""}


def degradation_occurred() -> bool:
    """True if any backend in this process fell back to deterministic."""
    return bool(_DEGRADED["hit"])


def degradation_reason() -> str:
    """Why the first degradation happened (empty if none)."""
    return str(_DEGRADED["reason"])


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


class ResilientBackend:
    """A primary backend that degrades to deterministic rather than dying.

    Why this exists, concretely. Measured against the free Pollinations tier
    on 2026-10-05, inside a single CI run: a preflight probe succeeded, and
    the pipeline's very first real call seconds later returned HTTP 402 and
    stayed 402 through the whole retry budget. The tier flaps on a timescale
    shorter than one run, so no up-front check can establish that a run will
    be able to finish, and retries alone cannot ride out a sustained window.

    A repro run needs tens of successful calls. Aborting on the first
    unrecoverable failure throws away the emulator boot -- the slow and
    expensive part -- and returns nothing at all, which is strictly worse
    than finishing without guidance.

    So after the primary gives up, every later call is served deterministically
    and ``degraded`` is set. This CANNOT manufacture a finding: a
    ``reproduced`` verdict comes only from the oracle observing a real
    ``FATAL EXCEPTION``, which never consults an LLM. What is lost is
    intelligent navigation, so the flag is surfaced in the report, in
    summary.json and on the results page -- an unlabelled degraded run would
    be a quieter version of the false green.

    Set ``NIMO_NO_FALLBACK=1`` to opt out and have an outage fail the run.
    """

    def __init__(self, primary: "LLMClient") -> None:
        self._primary = primary
        self._null = NullBackend()
        self.degraded = False
        self.degraded_reason = ""

    @property
    def base_url(self) -> str:
        return self._null.base_url if self.degraded else self._primary.base_url

    @property
    def model(self) -> str:
        return self._null.model if self.degraded else self._primary.model

    @property
    def api_key(self) -> str:
        """The primary's key, so the wrapper stays inspectable.

        Reported even while degraded: it describes how this run is
        CONFIGURED, which does not change when the provider goes down.
        """
        return self._primary.api_key

    def chat(self, messages, temperature: float = 0.2,
             response_format: dict | None = None) -> str:
        if self.degraded:
            return self._null.chat(messages, temperature, response_format)
        try:
            return self._primary.chat(messages, temperature, response_format)
        except LLMUnavailable as exc:
            self.degraded = True
            self.degraded_reason = str(exc)
            if not _DEGRADED["hit"]:
                _DEGRADED["hit"] = True
                _DEGRADED["reason"] = str(exc)
            # Printed once, not per call: this runs inside a device loop whose
            # log the operator actually reads.
            print(f"[nimo] LLM backend lost mid-run: {exc}", flush=True)
            print("[nimo] continuing with deterministic exploration "
                  "(no AI guidance). Crash detection is unaffected: "
                  "the oracle needs no LLM.", flush=True)
            return self._null.chat(messages, temperature, response_format)


def get_backend(name: str | None = None, **kwargs) -> "LLMClient | NullBackend | ResilientBackend":
    """Resolve a backend by name.

    name:
      None / "pollinations" / "openai" -> OpenAI-compatible LLMClient,
                                           wrapped so a mid-run outage
                                           degrades instead of aborting
      "null"                           -> deterministic NullBackend
    Any other name is treated as an OpenAI-compatible endpoint selected via
    the standard NIMO_BASE_URL / NIMO_MODEL env vars.

    With no explicit name, NIMO_BACKEND is consulted, so a device-only run
    needs no code change: `NIMO_BACKEND=null nimo repro ...` drives the whole
    device / oracle / report path with no network and no key.

    NIMO_NO_FALLBACK=1 disables the degradation wrapper, so a backend outage
    fails the run outright. Use it when a run without AI guidance is worth
    nothing to you and you would rather retry.
    """
    if name is None:
        name = os.environ.get("NIMO_BACKEND") or None
    if name == "null":
        return NullBackend()
    client = LLMClient(**kwargs)
    if os.environ.get("NIMO_NO_FALLBACK") == "1":
        return client
    return ResilientBackend(client)
