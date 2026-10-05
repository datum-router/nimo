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

    #: Controls that navigate OUT of the app under test rather than within
    #: it. Observed in CI run #29: step 2 tapped "Notepad, Navigate home"
    #: (the ActionBar up affordance) and the run spent its remaining 23
    #: steps driving the launcher, the Gallery and the Camera -- then
    #: reported a verdict for an app it was no longer in.
    #:
    #: The loop also relaunches the app whenever a step escapes it, which is
    #: the robust guarantee; this list is the cheap half that stops the step
    #: being wasted in the first place. Matched on the whole label, so an
    #: app's own "Home" tab is not caught by the launcher's "Navigate home".
    EXIT_LABELS = frozenset({
        "navigate home", "navigate up", "home", "recent apps", "overview",
        "app info", "back", "close app", "minimise", "minimize",
    })
    #: Substrings that indicate leaving for another app entirely.
    EXIT_HINTS = ("navigate home", "switch to ", "open in ", "app info")

    def __init__(self) -> None:
        self._elements: list[dict] = []
        #: How many times each control has been tapped, by identity. A COUNT
        #: rather than a visited-set: a burn list runs out, and when it did
        #: the explorer had nothing left but Back. See `chat`.
        self._taps: dict[str, int] = {}
        self._scrolls: dict[str, int] = {}
        self._consecutive_backs = 0

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

    def relaunched(self) -> None:
        """The loop put the app back after a step navigated out of it.

        The app's state is fresh, so screens previously judged exhausted are
        worth another look. Without this the scroll budget stayed spent and
        the explorer went straight back to Back -- which is what livelocked
        CI run #31 into 19 Back presses and 18 relaunches.
        """
        self._scrolls.clear()
        self._consecutive_backs = 0

    # -- internals --------------------------------------------------------
    @classmethod
    def _is_exit(cls, label: str) -> bool:
        """True if tapping this would leave the app under test."""
        low = label.strip().lower()
        if low in cls.EXIT_LABELS:
            return True
        return any(h in low for h in cls.EXIT_HINTS)

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
            label = str(el.get("label") or "").strip()
            if not label:
                continue  # unlabelled: the executor could not resolve it
            if self._is_exit(label):
                continue  # would leave the app; the loop would relaunch
            out.append(el)
        return out

    # -- decision ---------------------------------------------------------
    def chat(self, messages, temperature: float = 0.0,
             response_format: dict | None = None) -> str:
        """Pick the next move: least-visited control, else scroll, else back.

        The ordering matters and was got wrong once. The first version burned
        each control after one tap and fell through to Back when a screen had
        nothing new. On a small app that livelocked: CI run #31 pressed Back
        from the root activity, which EXITS to the launcher, the loop
        relaunched, the root screen's controls were all burned, so it pressed
        Back again -- 19 times, with 18 relaunches, for 2 useful taps.

        Choosing the LEAST-VISITED control instead of an unvisited one is
        what removes the dead end: the frontier never empties, so re-tapping
        a known control to re-enter a sub-screen is always available and is
        real exploration rather than a stall. Back is now reserved for a
        screen with no reachable controls at all, and is capped so a
        pathological screen cannot spend the whole budget on it.
        """
        screen = self._screen()
        targets = self._targets()

        if targets:
            # Fewest taps first; ties broken by position for determinism, so
            # the same app yields the same walk on every run.
            best = min(targets, key=lambda el: (
                self._taps.get(self._identity(el), 0),
                el.get("y", 0), el.get("x", 0)))
            ident = self._identity(best)
            seen = self._taps.get(ident, 0)
            # Only scroll for MORE controls while this screen still has
            # untouched ones; otherwise scrolling is just a slower Back.
            if seen > 0 and self._scrolls.get(screen, 0) < self.SCROLLS_PER_SCREEN:
                self._scrolls[screen] = self._scrolls.get(screen, 0) + 1
                self._consecutive_backs = 0
                return json.dumps({
                    "action": "swipe_up",
                    "why": "deterministic frontier: reveal controls below the fold",
                })
            self._taps[ident] = seen + 1
            self._consecutive_backs = 0
            return json.dumps({
                "action": "tap",
                "label": best.get("label"),
                "why": ("deterministic frontier: first visit to this control"
                        if seen == 0 else
                        f"deterministic frontier: revisiting to go deeper "
                        f"(visit {seen + 1})"),
            })

        # Nothing clickable here at all.
        if self._scrolls.get(screen, 0) < self.SCROLLS_PER_SCREEN:
            self._scrolls[screen] = self._scrolls.get(screen, 0) + 1
            return json.dumps({
                "action": "swipe_up",
                "why": "deterministic frontier: no controls visible, scrolling",
            })

        # Back is the last resort, and bounded: from a root activity it exits
        # the app, and an unbounded run of them is the livelock above.
        if self._consecutive_backs >= 2:
            self._consecutive_backs = 0
            return json.dumps({
                "action": "swipe_down",
                "why": ("deterministic frontier: two backs changed nothing, "
                        "scrolling up instead of exiting the app again"),
            })
        self._consecutive_backs += 1
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

    def relaunched(self) -> None:
        """Forward a relaunch notice to the fallback."""
        self._null.relaunched()

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
