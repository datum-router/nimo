"""Tarpit detection + LLM escape consultation (the Jev pattern).

The deterministic crawler is the engine; the LLM is a rarely-consulted
safety net for stuck states. A tarpit verdict fires after STALL_THRESHOLD
consecutive crawl episodes with no gain — no new state fingerprint, no
new activity, no crash. On a verdict the LLM gets exactly one shot: the
current screen, the recent action trail, and a fixed menu of escape
actions. Abstention is a first-class outcome (Jev's designed refusal):
the crawler then falls back to BACK + foreground reset.

Consultations are capped per run — each one is an LLM round-trip on the
CI clock, so the detector goes quiet after MAX_CONSULTS and the crawl
continues deterministically. An LLM failure never breaks the crawl;
it degrades to abstain.
"""

from __future__ import annotations

import json

STALL_THRESHOLD = 6   # consecutive zero-gain episodes before a verdict
MAX_CONSULTS = 5      # LLM escape consultations per run, max

ESCAPE_PROMPT = """You are recovering a stuck Android app-testing crawler.
The crawler has visited several screens in a row without finding anything
new — it is going in circles. Pick ONE action most likely to unstick it.

Current activity: {activity}
App: {app_name}

Recent actions (most recent last):
{trail}

Screen elements (label | class | clickable | x,y):
{elements}

Reply with exactly one JSON object, nothing else. Valid actions:
- {{"action": "tap", "target": "<element label>", "reason": "..."}}
- {{"action": "back", "reason": "..."}}
- {{"action": "swipe_up", "reason": "..."}}
- {{"action": "type", "target": "<field label>", "text": "<text>", "reason": "..."}}
- {{"action": "relaunch", "reason": "..."}}  (force-stop and cold-start the app)
If nothing looks promising, abstain:
- {{"action": "abstain", "reason": "..."}}
Prefer the smallest action that could break the loop. Never invent
element labels — use only labels from the list above."""


class TarpitDetector:
    """Counts consecutive zero-gain episodes; fires a verdict at threshold."""

    def __init__(self, stall_threshold: int = STALL_THRESHOLD,
                 max_consults: int = MAX_CONSULTS):
        self.stall_threshold = stall_threshold
        self.max_consults = max_consults
        self.stall_episodes = 0
        self.consults_used = 0
        self.events: list[dict] = []

    def observe(self, gained: bool) -> bool:
        """Feed one reached-screen episode. Returns True when a tarpit
        verdict fires and the LLM should be consulted now."""
        if gained:
            self.stall_episodes = 0
            return False
        self.stall_episodes += 1
        if self.stall_episodes >= self.stall_threshold \
                and self.consults_used < self.max_consults:
            self.consults_used += 1
            self.stall_episodes = 0
            return True
        return False


def suggest_escape(elements: list[dict], trail: list[str], activity: str,
                   app_name: str, llm) -> dict:
    """One LLM shot at an escape action. Always returns a dict with at
    least 'action' and 'reason'; failures degrade to abstain."""
    abstain = {"action": "abstain", "reason": "escape consultation failed"}
    if not elements:
        return {"action": "abstain",
                "reason": "no screen elements to choose from"}
    el_desc = "\n".join(
        f"- {e.get('label') or '(no label)'} | {e.get('class')} | "
        f"clickable={e.get('clickable')} | {e.get('x')},{e.get('y')}"
        for e in elements[:30])
    trail_desc = "\n".join(f"- {t}" for t in trail[-12:]) or "(empty)"
    try:
        raw = llm.chat([
            {"role": "system",
             "content": "Reply with exactly one JSON object, nothing else."},
            {"role": "user", "content": ESCAPE_PROMPT.format(
                activity=activity, app_name=app_name,
                trail=trail_desc, elements=el_desc)},
        ])
        esc = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        if esc.get("action") not in ("tap", "back", "swipe_up", "type",
                                     "relaunch", "abstain"):
            return abstain
        esc.setdefault("reason", "")
        return esc
    except Exception:
        return abstain
