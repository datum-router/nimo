"""Crash triage: fingerprint stack traces so 50 crashes of the same bug
collapse into one entry."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

FATAL_RE = re.compile(r"FATAL EXCEPTION[^\n]*\n((?:.*\n){1,40}?)(?=\n\S|\Z)")


def extract_stack(logcat: str) -> str:
    """Pull the first FATAL EXCEPTION stack out of a logcat chunk."""
    m = FATAL_RE.search(logcat)
    return m.group(0).strip() if m else logcat.strip()[:2000]


def fingerprint(package: str, stack: str) -> str:
    """Stable id: exception type + top app frames (framework frames ignored)."""
    exc = "Unknown"
    m = re.search(r"FATAL EXCEPTION.*?\n.*?((?:[\w.$]+)?(?:Error|Exception)[\w.$]*)",
                  stack)
    if m:
        exc = m.group(1).split(":")[0].strip()
    frames = []
    for line in stack.splitlines():
        line = line.strip()
        if line.startswith("at ") and package.split(".")[0] in line:
            frames.append(re.sub(r"\(.*", "", line[3:]))
        if len(frames) >= 5:
            break
    key = exc + "|" + "|".join(frames)
    return hashlib.sha1(key.encode()).hexdigest()[:12]


@dataclass
class Bug:
    fingerprint: str
    exception: str
    first_seen_activity: str | None
    occurrences: int = 1
    trail: list[str] = field(default_factory=list)   # action history at first hit
    stack: str = ""
    screenshot: str = ""


def dedupe(raw: list[dict]) -> list[Bug]:
    """raw: [{package, activity, trail, stack, screenshot}] -> unique Bugs."""
    bugs: dict[str, Bug] = {}
    for r in raw:
        stack = extract_stack(r.get("stack", ""))
        fp = fingerprint(r.get("package", ""), stack)
        exc_m = re.search(r"((?:[\w.$]+)?(?:Error|Exception)[\w.$]*)", stack)
        if fp in bugs:
            bugs[fp].occurrences += 1
        else:
            bugs[fp] = Bug(
                fingerprint=fp,
                exception=exc_m.group(1) if exc_m else "Unknown",
                first_seen_activity=r.get("activity"),
                trail=r.get("trail", []),
                stack=stack,
                screenshot=r.get("screenshot", ""),
            )
    return list(bugs.values())
