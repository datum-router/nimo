"""Blocker taxonomy: every uncovered screen gets a reason code.

The point of nimo's coverage number is that it is *believable*. An activity
that was never visited is not silently dropped — it is labeled with exactly
why, from this fixed vocabulary. New codes are added here, never invented
ad-hoc in the crawler.
"""

from __future__ import annotations

REASONS: dict[str, str] = {
    # auth / access
    "login-wall": "screen requires sign-in; the login ladder was exhausted "
                  "or not attempted for this app",
    "otp": "one-time code sent to a real phone/email — needs test numbers "
           "or SMS retrieval, not attempted automatically",
    "captcha": "CAPTCHA challenge — no automation handles this",
    "biometric": "biometric gate — cannot be passed automatically",
    "play-integrity": "Play Integrity / SafetyNet attestation fails on "
                      "emulator/redroid — app refuses to run there",
    "paywall": "blocked behind purchase or subscription",
    "server-url": "needs a server URL / self-hosted backend configured first",
    "permission-loop": "stuck in a permission or system-dialog loop",
    # reachability
    "not-exported": "activity not exported and has no intent filters; only "
                    "reachable via in-app UI, which never surfaced it",
    "no-route": "exported, but no UI path, deep link, or direct launch "
                "reached it",
    "webview-only": "screen is WebView-only; automation cannot see or fill "
                    "its fields",
    # runtime
    "launch-failed": "direct launch failed",
    "crash-on-launch": "app crashes when this screen opens",
    "budget-exhausted": "crawl budget ran out before this screen surfaced",
}


def label(activity: str, reason_code: str, detail: str = "") -> dict:
    """Build one unreachable entry. Unknown codes are rejected loudly."""
    if reason_code not in REASONS:
        raise ValueError(f"unknown blocker reason code: {reason_code!r} — "
                         f"add it to agent/blockers.py first")
    reason = REASONS[reason_code]
    if detail:
        reason = f"{reason} ({detail})"
    return {"activity": activity, "reason_code": reason_code, "reason": reason}


def summarize(unreachable: list[dict]) -> dict[str, int]:
    """Count unreachable entries per reason code, for the report."""
    counts: dict[str, int] = {}
    for u in unreachable:
        code = u.get("reason_code", "?")
        counts[code] = counts.get(code, 0) + 1
    return counts
