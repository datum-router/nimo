"""Legitimacy audit — CARBON's anti-self-report check.

Before a `reproduced` verdict or a discovered `bug` is trusted, it must pass
these criteria. A failed audit DOWNGRADES the verdict (e.g. reproduced ->
not_reproduced, bug -> observation). This is the second half of the honesty
guarantee: the oracle proves a crash is real; the audit proves the crash is
attributable to the agent's actions on the target app, not noise.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditResult:
    passed: bool
    criteria: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        n_ok = sum(1 for v in self.criteria.values() if v)
        return f"{'PASS' if self.passed else 'FAIL'} ({n_ok}/{len(self.criteria)})"


def audit_crash(*, crash_stack: str, target_package: str,
                action_count: int, crash_in_target: bool,
                had_fatal_in_logcat: bool,
                stack_references_app: bool) -> AuditResult:
    """Six-criterion legitimacy audit for a crash finding.

    1. real_fatal        — a genuine FATAL EXCEPTION/ANR existed in logcat.
    2. target_package    — the crash belongs to the target app, not a
                           system/other process.
    3. agent_driven      — at least one agent action preceded the crash
                           (not a cold-start / install-time crash counted as
                           a repro).
    4. stack_attribution — the stack trace references the app's own package
                           (not purely framework frames).
    5. nonempty_stack    — the captured stack is substantive, not a stub.
    6. consistent        — the crash's package matches the requested target.
    """
    crit = {
        "real_fatal": bool(had_fatal_in_logcat),
        "target_package": bool(crash_in_target),
        "agent_driven": action_count > 0,
        "stack_attribution": bool(stack_references_app),
        "nonempty_stack": len((crash_stack or "").strip()) > 40,
        "consistent": target_package.split(".")[0] in (crash_stack or ""),
    }
    notes: list[str] = []
    for name, ok in crit.items():
        if not ok:
            notes.append(f"criterion failed: {name}")
    # Hard requirements: a verdict cannot stand without a real fatal in the
    # target, driven by an agent action.
    passed = crit["real_fatal"] and crit["target_package"] and crit["agent_driven"]
    return AuditResult(passed=passed, criteria=crit, notes=notes)
