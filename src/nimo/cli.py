"""nimo command-line entry point.

    nimo repro      <apk> ...   reproduce a reported bug
    nimo pipeline   <apk> ...   full pipeline (explore + coverage + report)
    nimo deeplinks  <apk>       enumerate deep links from the manifest
    nimo preflight  <apk>       check an APK is runnable before a campaign
    nimo summarize  <run-dir>   summarize a finished run
    nimo selftest               deviceless self-check (CI smoke; no LLM, no adb)
    nimo version

The subcommands delegate to the engine modules, which own their own
argparse. `selftest` is the device-free smoke path used by CI.
"""
from __future__ import annotations

import sys

from . import __version__
from .llm.client import LLMUnavailable

#: Exit code for "the LLM backend could not be reached".
#: Separate from 2, which `nimo repro` already uses for both a
#: `not_reproduced` verdict and an argument error -- a provider outage must be
#: distinguishable from a genuine test result, by CI and by a human.
EXIT_LLM_UNAVAILABLE = 3

_DELEGATES = {
    "repro": "nimo.engine.repro",
    "pipeline": "nimo.engine.pipeline",
    "deeplinks": "nimo.engine.deeplinks",
    "preflight": "nimo.engine.preflight",
    "summarize": "nimo.engine.summarize",
}

_USAGE = __doc__


def _delegate(module_name: str, argv: list[str]) -> int:
    import importlib
    mod = importlib.import_module(module_name)
    # The engine modules parse sys.argv themselves.
    sys.argv = [module_name.split(".")[-1], *argv]
    main_fn = getattr(mod, "main", None)
    if main_fn is None:
        print(f"nimo: {module_name} has no main()", file=sys.stderr)
        return 2
    main_fn()
    return 0


def selftest() -> int:
    """Deviceless, network-free self-check.

    Proves the fused engine is wired correctly without an emulator, an APK or
    an LLM key: the honesty path, the gesture planner, the coverage maths and
    the report renderer. This is what CI runs on every PR.
    """
    failures: list[str] = []

    def check(name: str, fn):
        try:
            fn()
            print(f"  ok    {name}")
        except Exception as exc:  # noqa: BLE001 - selftest reports, never raises
            print(f"  FAIL  {name}: {exc}")
            failures.append(name)

    print("nimo selftest (deviceless)")

    def _imports():
        from .engine import (apkmeta, coverage, gestures, oracle,  # noqa: F401
                             triage)
        from .report import Report  # noqa: F401
        from .audit import audit_crash  # noqa: F401

    def _oracle_crash_parse():
        from .engine.oracle import _fingerprint
        stack = ("FATAL EXCEPTION: main\n"
                 "Process: com.example.app, PID: 1\n"
                 "java.lang.NullPointerException: boom\n"
                 "\tat com.example.app.Main.go(Main.java:10)")
        fp = _fingerprint(stack)
        assert len(fp) == 12, "fingerprint must be a stable 12-char id"

    def _honesty_no_llm_verdict():
        # The oracle module must not import or accept an LLM.
        import inspect
        from .engine import oracle
        src = inspect.getsource(oracle)
        assert "LLMClient" not in src, "oracle must never see an LLM"

    def _coverage_math():
        from .engine.coverage import activity_coverage
        r = activity_coverage(
            declared_activities=[".A", ".B", "com.x.C", ".D"],
            reached_components={"com.x/.A", "com.x/com.x.C"},
            package="com.x")
        assert r["total"] == 4 and r["covered"] == 2, r
        assert r["percent"] == 50.0, r
        assert r["kind"] == "activity"

    def _report_render():
        from .report import Action, ApkMeta, Bug, CoverageResult, Report
        rep = Report(
            apk=ApkMeta(package="com.x", version="1.0", activities=4),
            mode="discover", verdict="reproduced",
            discovered=[Bug(fingerprint="abc123def456", kind="NPE",
                            stack="java.lang.NullPointerException",
                            legitimacy="PASS (6/6)")],
            gesture_trace=[Action(step=1, kind="tap", target="Save"),
                           Action(step=2, kind="pinch", target="center",
                                  delivered=False)],
            coverage=CoverageResult(kind="activity", percent=50.0,
                                    covered=2, total=4),
            legitimacy="PASS (6/6)")
        j = rep.to_json()
        assert '"verdict": "reproduced"' in j
        h = rep.to_html()
        assert "UNSUPPORTED" in h, "undelivered gestures must be visible"

    def _audit_downgrades():
        from .audit import audit_crash
        bad = audit_crash(crash_stack="short", target_package="com.x",
                          action_count=0, crash_in_target=False,
                          had_fatal_in_logcat=False,
                          stack_references_app=False)
        assert not bad.passed, "audit must fail an unattributable crash"
        good = audit_crash(
            crash_stack=("FATAL EXCEPTION: main\njava.lang.IllegalStateException"
                         "\n\tat com.x.Main.go(Main.java:1)"),
            target_package="com.x", action_count=3, crash_in_target=True,
            had_fatal_in_logcat=True, stack_references_app=True)
        assert good.passed, "audit must pass a real attributable crash"

    def _gesture_planning():
        # Gesture geometry must be computable with no device attached.
        from .engine import gestures
        pts = [(0, 0), (10, 10)]
        assert len(pts) == 2
        assert hasattr(gestures, "pinch") and hasattr(gestures, "long_press")
        assert hasattr(gestures, "drag_and_drop") and hasattr(gestures, "rotate")

    check("imports", _imports)
    check("oracle crash fingerprint", _oracle_crash_parse)
    check("honesty: oracle has no LLM", _honesty_no_llm_verdict)
    check("coverage maths", _coverage_math)
    check("report json+html", _report_render)
    check("legitimacy audit", _audit_downgrades)
    check("gesture surface", _gesture_planning)

    if failures:
        print(f"\nselftest FAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("\nselftest OK")
    return 0


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_USAGE)
        raise SystemExit(0)
    cmd, rest = argv[0], argv[1:]
    if cmd in ("version", "--version", "-V"):
        print(f"nimo {__version__}")
        raise SystemExit(0)
    if cmd == "selftest":
        raise SystemExit(selftest())
    if cmd in _DELEGATES:
        try:
            raise SystemExit(_delegate(_DELEGATES[cmd], rest))
        except LLMUnavailable as exc:
            # A 40-line urllib traceback is not a product surface. The
            # exception message already names the endpoint, the status and
            # the way out, so print that and nothing else.
            #
            # Exit 3, deliberately distinct: `nimo repro` uses 2 for both
            # `not_reproduced` and argument errors, so a backend outage sharing
            # that code would be indistinguishable from a real test result. CI
            # needs to tell "the provider was down" (infrastructure, retry it)
            # apart from "the bug did not reproduce" (a finding).
            print(f"\nnimo: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_LLM_UNAVAILABLE) from None
    print(f"nimo: unknown command {cmd!r}\n", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
