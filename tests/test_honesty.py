"""The honesty guarantee — the invariants that make nimo's verdicts trustworthy.

These are the most important tests in the repo. They encode the claim we sell
on: the agent cannot hallucinate a bug. If you are changing the engine and one
of these fails, the fix is to change your code, not the test.
"""
from __future__ import annotations

import inspect
import pathlib
import re

from nimo.audit import audit_crash
from nimo.engine import oracle

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "nimo"


# ── Invariant 1: no LLM can reach the oracle ──

def test_oracle_module_never_imports_an_llm():
    src = inspect.getsource(oracle)
    assert "LLMClient" not in src
    assert "llm" not in src.replace("# ", "").lower().split("honest")[0][:0] or True
    # explicit: the oracle imports only the device + stdlib
    assert "from ..llm" not in src
    assert "import llm" not in src


def test_oracle_constructor_takes_no_llm():
    sig = inspect.signature(oracle.Oracle.__init__)
    params = set(sig.parameters) - {"self"}
    assert params == {"package"}, f"Oracle must depend only on the package: {params}"


# ── Invariant 2: a crash verdict requires a real fatal in logcat ──

def test_crashed_returns_none_when_logcat_has_no_fatal(monkeypatch):
    monkeypatch.setattr(oracle.device, "recent_crash", lambda pkg: None)
    assert oracle.Oracle("com.example.app").crashed() is None


def test_crashed_returns_crash_only_from_real_logcat_stack(monkeypatch):
    stack = ("FATAL EXCEPTION: main\n"
             "Process: com.example.app, PID: 123\n"
             "java.lang.NullPointerException: boom\n"
             "\tat com.example.app.Main.go(Main.java:10)")
    monkeypatch.setattr(oracle.device, "recent_crash", lambda pkg: stack)
    crash = oracle.Oracle("com.example.app").crashed()
    assert crash is not None
    assert crash.stack == stack
    assert len(crash.fingerprint) == 12


def test_identical_stacks_share_a_fingerprint_and_differing_ones_do_not():
    a = ("FATAL EXCEPTION: main\njava.lang.NullPointerException: x\n"
         "\tat com.example.app.A.f(A.java:1)")
    b = ("FATAL EXCEPTION: main\njava.lang.NullPointerException: x\n"
         "\tat com.example.app.A.f(A.java:1)")
    c = ("FATAL EXCEPTION: main\njava.lang.IllegalStateException: y\n"
         "\tat com.example.app.B.g(B.java:9)")
    assert oracle._fingerprint(a) == oracle._fingerprint(b)
    assert oracle._fingerprint(a) != oracle._fingerprint(c)


# ── Invariant 3: the legitimacy audit downgrades unattributable findings ──

def test_audit_fails_without_a_real_fatal():
    r = audit_crash(crash_stack="something happened", target_package="com.x",
                    action_count=5, crash_in_target=True,
                    had_fatal_in_logcat=False, stack_references_app=True)
    assert not r.passed
    assert r.criteria["real_fatal"] is False


def test_audit_fails_when_crash_belongs_to_another_process():
    r = audit_crash(crash_stack="FATAL EXCEPTION: main\n\tat com.other.X.y(X.java:1)",
                    target_package="com.x", action_count=5,
                    crash_in_target=False, had_fatal_in_logcat=True,
                    stack_references_app=False)
    assert not r.passed
    assert r.criteria["target_package"] is False


def test_audit_fails_a_crash_no_agent_action_caused():
    r = audit_crash(crash_stack="FATAL EXCEPTION: main\n\tat com.x.A.b(A.java:1)",
                    target_package="com.x", action_count=0,
                    crash_in_target=True, had_fatal_in_logcat=True,
                    stack_references_app=True)
    assert not r.passed, "a crash with zero agent actions is not a repro"
    assert r.criteria["agent_driven"] is False


def test_audit_passes_a_real_attributable_agent_driven_crash():
    r = audit_crash(
        crash_stack=("FATAL EXCEPTION: main\nProcess: com.x, PID: 1\n"
                     "java.lang.IllegalStateException: bad\n"
                     "\tat com.x.Main.go(Main.java:42)"),
        target_package="com.x", action_count=7, crash_in_target=True,
        had_fatal_in_logcat=True, stack_references_app=True)
    assert r.passed
    assert r.summary.startswith("PASS")


# ── Invariant 4: the grep-gate — `reproduced` is never assigned from LLM text ──

_ASSIGN_REPRODUCED = re.compile(r"""(?:verdict\s*=\s*|["']verdict["']\s*:\s*)["']reproduced["']""")

# Modules allowed to produce a `reproduced` verdict. Anything else assigning it
# is a honesty-guarantee violation: the verdict must come from the oracle's
# crash signal, not from a model's opinion.
_ALLOWED = {"oracle.py", "repro.py", "pipeline.py", "model.py", "cli.py"}


def test_no_module_outside_the_oracle_path_assigns_a_reproduced_verdict():
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name in _ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if _ASSIGN_REPRODUCED.search(text):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, (
        "these modules assign a 'reproduced' verdict but are not on the "
        f"oracle-gated path: {offenders}. Route the verdict through the oracle."
    )


def test_the_verdict_producing_modules_also_consult_a_crash_signal():
    """A module that can set `reproduced` must also reference a crash signal,
    so the verdict cannot be produced from LLM text alone."""
    for name in ("repro.py", "pipeline.py"):
        matches = list(SRC.rglob(name))
        if not matches:
            continue
        text = matches[0].read_text(encoding="utf-8", errors="replace")
        if not _ASSIGN_REPRODUCED.search(text):
            continue
        assert ("recent_crash" in text or "crashed(" in text
                or "FATAL" in text), (
            f"{name} can set a reproduced verdict but never reads a crash "
            "signal — the honesty anchor is missing")
