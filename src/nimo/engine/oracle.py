"""Dual oracle — the honesty anchor of the fused engine.

A `reproduced` verdict is produced HERE and ONLY here, and only from a real
`FATAL EXCEPTION`/ANR in logcat (CARBON's dual-oracle idea on nimo's crash
parser). No LLM output can reach this module; it reads the device, not the
model. This is what lets us say the agent cannot hallucinate a bug.

Two independent signals:
  1. logcat crash signal  — a genuine FATAL EXCEPTION / ANR for the package.
  2. view-hierarchy state  — did the on-screen state actually change
                             (progress) or are we stuck / looping.

`Verdict.reproduced` requires signal 1. Signal 2 feeds progress/termination
and the discover-mode coverage accounting.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import device


@dataclass
class Crash:
    package: str
    stack: str
    fingerprint: str  # stable id for dedup (nimo triage uses the same scheme)
    kind: str = "FATAL EXCEPTION"


@dataclass
class State:
    activity: str | None
    ui_signature: str  # hash of the visible element set
    elements: list[dict] = field(default_factory=list)


def _fingerprint(stack: str) -> str:
    # First meaningful exception line + top frame — matches nimo triage.
    lines = [l.strip() for l in stack.splitlines() if l.strip()]
    key = ""
    for l in lines:
        if "Exception" in l or "Error" in l:
            key = l
            break
    top = next((l for l in lines if l.startswith("at ")), "")
    return hashlib.sha1(f"{key}|{top}".encode()).hexdigest()[:12]


class Oracle:
    """Reads device signals to produce an honest verdict. Constructed with a
    target package; never with an LLM."""

    def __init__(self, package: str) -> None:
        self.package = package

    # ── signal 1: the crash oracle (honesty anchor) ──
    def crashed(self) -> Crash | None:
        """Return a real crash for the target package, or None.
        Delegates to the device's logcat FATAL EXCEPTION parser — a signal
        the model never touches."""
        stack = device.recent_crash(self.package)
        if not stack:
            return None
        return Crash(package=self.package, stack=stack,
                     fingerprint=_fingerprint(stack))

    # ── signal 2: the state oracle (progress / stuck detection) ──
    def observe(self) -> State:
        try:
            elements = device.dump_ui()
        except device.DeviceError:
            elements = []
        sig_src = "|".join(sorted(
            f"{e.get('label','')}:{e.get('class','')}" for e in elements))
        return State(
            activity=device.current_activity(),
            ui_signature=hashlib.sha1(sig_src.encode()).hexdigest()[:12],
            elements=elements,
        )

    @staticmethod
    def progressed(before: State, after: State) -> bool:
        """Did the app actually move? Activity change OR UI signature change."""
        if before.activity != after.activity:
            return True
        return before.ui_signature != after.ui_signature
