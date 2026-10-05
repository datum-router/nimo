"""LLM client + swappable backends.

`LLMClient` (from client.py) is the OpenAI-compatible client nimo already
used; it defaults to Pollinations.ai (free, no key) and is the default
backend for the free tier and demos.

`get_backend(name)` returns a configured client for a named provider. The
`null` backend runs the engine with NO network LLM — deterministic, used for
CI smoke tests and offline demos (the VALOR deterministic-fallback path).
"""
from __future__ import annotations

import json
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
    """Deterministic explorer: no network, no key, but REAL exploration.

    This used to return a fixed ``{"action": "back"}`` on every call. The
    consequence was measured in CI run #28: with the backend degraded, the
    repro loop pressed Back twenty-five times, burning 82 seconds of booted
    emulator to visit nothing. The run was honest -- it reported
    ``not_reproduced`` and labelled itself unguided -- but the evidence
    behind that verdict was worthless, because no screen past the launch
    activity was ever opened.

    So the fallback now explores. It is a frontier walk over the UI dump the
    caller hands it via :meth:`observe`:

    * tap a clickable element this run has not already tapped;
    * when a screen offers nothing new, scroll to reveal more (twice);
    * when a scrolled screen is still exhausted, go Back and try elsewhere.

    Each element is remembered by identity (resource id, label, class) rather
    than by screen position, so a list that reflows between dumps does not
    look like new ground.

    This is navigation only -- it cannot invent a finding. A ``reproduced``
    verdict comes exclusively from the oracle observing a real
    ``FATAL EXCEPTION``, which consults no LLM, so a crash found while
    walking deterministically is exactly as real as one found under AI
    guidance. What is lost is *targeting*: this walks broadly instead of
    pursuing the reported reproduction path, which is why a degraded run is
    still labelled everywhere it surfaces.
    """

    base_url = "null://deterministic"
    model = "null"

    #: How many times to scroll one screen before giving up on it.
    SCROLLS_PER_SCREEN = 2

    def __init__(self) -> None:
        self._elements: list[dict] = []
        self._tapped: set[str] = set()
        self._scrolls: dict[str, int] = {}

    # -- observation ------------------------------------------------------
    def observe(self, elements) -> None:
        """Record the current screen's UI dump.

        Called by the engine loop before each decision. Without it the
        explorer is blind and falls back to scroll/back, so a caller that
        does not observe gets the old (useless) behaviour rather than a
        crash -- which is why this is a separate, optional method instead of
        a change to the ``chat`` signature every backend shares.
        """
        self._elements = [e for e in (elements or []) if isinstance(e, dict)]

    # -- internals --------------------------------------------------------
    @staticmethod
    def _identity(el: dict) -> str:
        """Stable identity for one element, independent of its position."""
        return "|".join((
            str(el.get("resource_id") or ""),
            str(el.get("label") or ""),
            str(el.get("class") or ""),
        ))

    def _screen(self) -> str:
        """Signature of the current screen, from what it contains."""
        return "/".join(sorted(self._identity(e) for e in self._elements))

    def _targets(self) -> list[dict]:
        """Clickable, labelled elements worth tapping, untouched first."""
        out = []
        for el in self._elements:
            if not el.get("clickable"):
                continue
            if not str(el.get("label") or "").strip():
                continue  # unlabelled: the executor could not resolve it
            out.append(el)
        return out

    # -- decision ---------------------------------------------------------
    def chat(self, messages, temperature: float = 0.0,
             response_format: dict | None = None) -> str:
        screen = self._screen()
        for el in self._targets():
            ident = self._identity(el)
            if ident in self._tapped:
                continue
            self._tapped.add(ident)
            return json.dumps({
                "action": "tap",
                "label": el.get("label"),
                "why": "deterministic frontier: first visit to this control",
            })

        seen = self._scrolls.get(screen, 0)
        if seen < self.SCROLLS_PER_SCREEN:
            self._scrolls[screen] = seen + 1
            return json.dumps({
                "action": "swipe_up",
                "why": "deterministic frontier: reveal controls below the fold",
            })

        return json.dumps({
            "action": "back",
            "why": "deterministic frontier: screen exhausted, backtracking",
        })


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
        # First contact must not stall the device loop.
        #
        # LLMClient defaults to 5 retries with exponential backoff, which is
        # ~48s before it gives up -- measured as exactly that in CI run #28,
        # where the first call burned 48s of booted emulator before the
        # fallback took over. Retrying that hard is redundant HERE, because
        # this wrapper already provides the resilience: degrading is instant
        # and costs correctness nothing a long retry would have saved.
        #
        # A KEYED provider is left alone. There, degrading forfeits the real
        # verdicts the operator is paying for, so riding out a blip is worth
        # the wait; an anonymous free tier offers no such guarantee.
        if not getattr(primary, "api_key", ""):
            primary.retries = min(getattr(primary, "retries", 5), 2)

    def observe(self, elements) -> None:
        """Keep the fallback's map current even while the primary is healthy.

        Observations must flow through on EVERY step, not just after
        degrading: the explorer's value is knowing which controls were
        already visited, and a map that only starts filling at the moment of
        failure would re-walk everything the guided half of the run covered.
        """
        self._null.observe(elements)

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
