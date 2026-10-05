"""nimo pipeline — every corner of the APK, tested.

Two paths, one engine:

  Path A  --bug report.md        targeted reproduction of a reported bug
  Path B  (default)              systematic discovery: static map of the APK
                                 -> install -> login handling -> intent sweep ->
                                 BFS crawl of every activity, deep link and UI
                                 element -> crash triage -> coverage report

Usage:
    nimo pipeline --apk app.apk --out out/run1/
    nimo pipeline --apk app.apk --bug report.md --auth auth.yaml --out out/run2/
    nimo pipeline --apk app.apk --coverage-apk app-jacoco.apk --out out/run3/

The --coverage-apk is a JaCoCo-instrumented build of the same app (built
with the VALOR-Droid toolchain, see docs/COVERAGE.md). When given, the
crawl runs against the instrumented APK and the pipeline pulls coverage.ec
afterwards, so the report carries real code coverage, not just screens.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import actions, apkmeta, auth, autofill, blockers, coverage, deeplinks, device, login_prologue, preflight
from .crawler import CrawlBudget, explore
from ..llm import degradation_occurred, degradation_reason, get_backend
from .repro import reproduce


def main() -> None:
    ap = argparse.ArgumentParser(description="nimo full-APK test pipeline")
    ap.add_argument("--apk", required=True, help="APK under test")
    ap.add_argument("--bug", default=None, help="bug report markdown (Path A)")
    ap.add_argument("--auth", default=None, help="auth config file (key: value)")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--coverage-apk", default=None,
                    help="JaCoCo-instrumented APK for code coverage")
    ap.add_argument("--coverage-remote-path", default=None,
                    help="remote coverage.ec path "
                         "(default /data/data/<pkg>/files/coverage.ec)")
    ap.add_argument("--max-minutes", type=int, default=30)
    ap.add_argument("--max-actions", type=int, default=400)
    ap.add_argument("--repro-only", action="store_true",
                    help="only run Path A (bug reproduction), skip discovery")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()
    started = datetime.now(timezone.utc).isoformat()

    # ---- preflight: refuse bad APKs before spending device minutes -----
    def _gate(apk: str, need_coverage: bool, pkg: str | None = None):
        r = preflight.check(apk, expected_package=pkg, need_coverage=need_coverage)
        for f in r.failures:
            print(f"[nimo] preflight FAIL: {f}")
        if not r.ok:
            print("[nimo] refused — fix the APK and retry")
            sys.exit(2)
        for w in r.warnings:
            print(f"[nimo] preflight warning: {w}")
        return r

    print("[nimo] preflight...")
    pf = _gate(args.apk, need_coverage=bool(args.coverage_apk))
    if args.coverage_apk and args.coverage_apk != args.apk:
        _gate(args.coverage_apk, need_coverage=True,
              pkg=pf.info.get("package"))

    # ---- static map -------------------------------------------------
    print("[nimo] analyzing APK...")
    meta = apkmeta.analyze(args.apk)
    print(f"[nimo] {meta.app_name or meta.package} "
          f"({meta.package} v{meta.version})")
    print(f"[nimo] map: {len(meta.activities)} activities, "
          f"{sum(len(a.deep_links) for a in meta.activities)} deep links, "
          f"{len(meta.permissions)} permissions")
    serial = device.check_connected()
    print(f"[nimo] device: {serial}")

    llm = get_backend()
    cfg = auth.AuthConfig.from_file(args.auth)
    report: dict = {
        "tool": "nimo-pipeline",
        "version": "0.2.0",
        "started": started,
        "apk": os.path.basename(args.apk),
        "package": meta.package,
        "app": meta.app_name,
        "version_name": meta.version,
        "llm": {"base_url": llm.base_url, "model": llm.model},
        "map": {
            "activities": [a.name for a in meta.activities],
            "main_activity": meta.main_activity,
            "deep_links": [l for a in meta.activities for l in a.deep_links],
        },
    }

    # ---- Path A: targeted reproduction ------------------------------
    if args.bug:
        print("[nimo] Path A: targeted bug reproduction")
        device.install(args.apk)
        repro_dir = os.path.join(args.out, "repro")
        rep = reproduce(args.apk, meta.package, args.bug, repro_dir)
        report["repro"] = {
            "verdict": rep["verdict"],
            "steps_taken": rep["steps_taken"],
            "report_dir": "repro",
        }
        print(f"[nimo] Path A verdict: {rep['verdict']}")

    # ---- Path B: systematic discovery -------------------------------
    if not args.repro_only:
        print("[nimo] Path B: systematic discovery crawl")
        crawl_apk = args.coverage_apk or args.apk
        device.install(crawl_apk)
        crawl_dir = os.path.join(args.out, "crawl")
        os.makedirs(crawl_dir, exist_ok=True)
        coverage_attempts: list[dict] = []

        # intent sweep first: fire every enumerated intent target directly.
        # Fast (~2s/target), reaches screens no tap sequence can find, and
        # labels the unreached ones precisely for the coverage report.
        print("[nimo] intent sweep: firing enumerated intent targets")
        enum = deeplinks.enumerate_targets(args.apk)
        sweep = deeplinks.fire_all(meta.package, enum["targets"])
        sweep_labels = {u["activity"]: u for u in sweep["unreachable"]}
        by_outcome: dict[str, int] = {}
        for att in sweep["attempts"]:
            by_outcome[att["outcome"]] = by_outcome.get(att["outcome"], 0) + 1
        coverage_attempts.append({
            "strategy": "intent-sweep",
            "targets": len(enum["targets"]),
            "reached": len(sweep["reached"]),
            "by_outcome": by_outcome,
        })
        print(f"[nimo] sweep done: {len(sweep['reached'])} activities "
              f"reached via intents, "
              f"{len(sweep['unreachable'])} labeled unreachable")
        # seed the live action feed with what the sweep reached, so the
        # frontend's app map shows those screens from the start
        sweep_feed = actions.ActionFeed(crawl_dir)
        for act in sweep["reached"]:
            sweep_feed.record("launch",
                              label=actions.ActionFeed.short_activity(act),
                              activity=act)

        # rung 2b of the login ladder: nimo's own AutofillService on-device.
        # OS-level credential fill for any app's login form; the crawler's
        # auth.attempt tries it before falling back to adb typing.
        af_ok, af_detail = autofill.setup(cfg, meta.package)
        print(f"[nimo] autofill: {af_detail}")
        coverage_attempts.append(
            {"strategy": "autofill-setup", "ok": af_ok, "detail": af_detail})

        # rung 4 of the login ladder: per-app Maestro prologue, if defined
        ran, ok, detail = login_prologue.run(meta.package, cfg)
        if ran:
            print(f"[nimo] login prologue: {detail}")
            coverage_attempts.append(
                {"strategy": "maestro-login-prologue",
                 "ok": ok, "detail": detail})

        budget = CrawlBudget(max_actions=args.max_actions,
                             max_minutes=args.max_minutes)
        result = explore(meta, cfg, llm, crawl_dir, budget,
                         seeds=sweep["reached"], sweep_labels=sweep_labels)
        coverage_attempts.extend(result.coverage_attempts)
        print(f"[nimo] crawl done: {len(result.visited_activities)}/"
              f"{result.total_activities} activities "
              f"({result.coverage_pct}%), {result.screens_visited} screens, "
              f"{len(result.bugs)} unique bugs, "
              f"{result.actions_taken} actions")

        coverage_info = None
        if args.coverage_apk:
            ec = coverage.collect(meta.package, crawl_dir,
                                  args.coverage_remote_path)
            if ec:
                coverage_info = coverage.report(ec, crawl_dir)

        report["discovery"] = {
            "visited_activities": result.visited_activities,
            "coverage_pct": result.coverage_pct,
            "screens_visited": result.screens_visited,
            "actions_taken": result.actions_taken,
            "deep_links_fired": result.deep_links_fired,
            "intent_sweep": {
                "targets": len(enum["targets"]),
                "reached": sweep["reached"],
                "attempts": sweep["attempts"],
            },
            "bugs": [
                {"fingerprint": b.fingerprint,
                 "exception": b.exception,
                 "occurrences": b.occurrences,
                 "first_seen_activity": b.first_seen_activity,
                 "trail": b.trail,
                 "screenshot": b.screenshot,
                 "stack": b.stack}
                for b in result.bugs
            ],
            "unreachable": result.unreachable,
            "unreachable_by_reason": blockers.summarize(result.unreachable),
            "auth_events": result.auth_events,
            "coverage_attempts": coverage_attempts,
            "coverage": coverage_info,
            "report_dir": "crawl",
        }

    report["finished"] = datetime.now(timezone.utc).isoformat()
    report["wall_seconds"] = round(time.time() - t0, 1)
    # Whether AI guidance actually survived the run. Checked at the END, not
    # at construction: the backend can be lost mid-run, and a report that
    # claims guidance it did not have would overstate the exploration's
    # quality -- "no bugs found" means far less when navigation was random.
    report["ai_guidance"] = not degradation_occurred()
    if degradation_occurred():
        report["degraded"] = True
        report["degraded_reason"] = degradation_reason()
    with open(os.path.join(args.out, "pipeline_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(f"[nimo] pipeline report: {args.out}/pipeline_report.json "
          f"({report['wall_seconds']}s)")


if __name__ == "__main__":
    main()
