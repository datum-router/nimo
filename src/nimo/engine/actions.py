"""Structured action feed for the live frontend.

Every tap/type/back/swipe/launch the crawler performs is appended as one
JSON line to ``<out_dir>/actions.jsonl``::

    {"t": 1727..., "kind": "tap", "fx": 0.512, "fy": 0.301,
     "label": "New note", "activity": "com.bander.notepad.NoteList"}

Coordinates are stored as fractions of the screen size so the web UI can
place click markers on the screenshot at any display size. The live
progress pusher (scripts/run_pipeline.sh) tails this file — no log grepping.
"""

from __future__ import annotations

import json
import os
import time

KINDS = ("tap", "type", "back", "swipe", "launch", "deep_link", "crash")


class ActionFeed:
    def __init__(self, out_dir: str):
        os.makedirs(out_dir, exist_ok=True)
        self.path = os.path.join(out_dir, "actions.jsonl")
        self._w: int | None = None
        self._h: int | None = None

    def _size(self) -> tuple[int, int]:
        if self._w is None:
            try:
                from . import device
                self._w, self._h = device.screen_size()
            except Exception:
                self._w, self._h = 1080, 2400
        return self._w or 1080, self._h or 2400

    def record(self, kind: str, x: int | None = None, y: int | None = None,
               label: str = "", activity: str | None = None) -> None:
        w, h = self._size()
        rec: dict = {"t": time.time(), "kind": kind,
                     "label": (label or "")[:60],
                     "activity": activity or ""}
        if x is not None and y is not None and w and h:
            rec["fx"] = round(x / w, 4)
            rec["fy"] = round(y / h, 4)
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass  # live feed is best-effort; the crawl must never die for it

    @staticmethod
    def short_activity(activity: str | None) -> str:
        if not activity:
            return ""
        return activity.split("/")[-1].split(".")[-1]
