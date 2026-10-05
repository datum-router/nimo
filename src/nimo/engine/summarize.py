"""Customer-facing summary for a pipeline run.

Reads out/pipeline_report.json (+ device info) and writes summary.json:
  - verdict: bugs_found | clean | reproduced | not_reproduced | failed
  - coverage diagram data, bug list, unreachable list
  - customer_note: plain-language summary for the customer
"""

from __future__ import annotations

import argparse
import json
import os


def _metrics(rep: dict, disc: dict | None, sweep: dict,
             visited: int, total: int, coverage: float,
             unreachable: list) -> dict:
    """The detailed report, as structured data rather than prose.

    Every figure carries its own ``measured`` flag. That is the whole point:
    a run that never instrumented the APK has no line coverage, and emitting
    0% for it would be a false statement about the app rather than a missing
    number. The frontend renders "not measured" from these flags instead of
    guessing from a zero.
    """
    repro = rep.get("repro") or {}
    cov = (disc or {}).get("coverage") or {}

    # Activity coverage: declared activities actually reached.
    # `declared` comes from the manifest and is known even in repro mode,
    # where no crawl runs. Reporting it separately lets the UI say "7
    # activities, coverage not measured in this mode" instead of either
    # hiding the app's size or implying 0% of 7 were reached.
    declared = len((rep.get("map") or {}).get("activities") or [])
    activity = {
        "measured": total > 0,
        "visited": visited,
        "total": total,
        "declared": declared,
        "pct": coverage if total else None,
        "unreachable": len(unreachable or []),
    }

    # Line coverage: only exists with an instrumented build AND the JaCoCo
    # CLI. `converted` false means an .ec was collected but not turned into
    # numbers -- which is a different state from "no coverage run at all",
    # and the note explains which.
    line = {
        # "converted" alone was not enough: a conversion can succeed and
        # still yield no readable counters, which would show a measured tile
        # with no number in it.
        "measured": bool(cov.get("converted")) and cov.get("pct") is not None,
        "ec_collected": bool(cov),
        "ec_class_records": cov.get("ec_class_records"),
        "xml": cov.get("xml"),
        "pct": cov.get("pct"),
        "covered": cov.get("covered"),
        "total": cov.get("total"),
        "basis": cov.get("basis"),
        "counters": cov.get("counters"),
        "note": cov.get("note") or (
            "no instrumented build was supplied, so line coverage was not "
            "measured; activity coverage below is measured on the release APK"
        ),
    }

    # Screens actually seen, which is NOT the same as activities declared:
    # a screen can be reached that the manifest never named, and a declared
    # activity can redirect elsewhere and never really open.
    seen = (disc or {}).get("visited_activities") or []
    screens = {
        "measured": bool(disc),
        "reached": len(seen),
        "names": sorted(seen)[:50],
    }

    # Both halves of the engine do work worth counting, and they count
    # DIFFERENT things: the repro loop walks a fixed step budget, the crawler
    # takes as many actions as its budget allows. Reporting only repro steps
    # left this unmeasured for every discover-mode run even though the crawl
    # had driven the device -- verified on live run #35, which reported
    # 0 steps after a real 358s crawl.
    off = repro.get("off_app_steps")
    if repro:
        steps = {
            "measured": True,
            "basis": "repro steps",
            "taken": repro.get("steps_taken") or repro.get("steps") or 0,
            "in_app": repro.get("steps_in_app"),
            "off_app": len(off) if isinstance(off, list) else off,
        }
    elif disc:
        steps = {
            "measured": True,
            "basis": "crawl actions",
            "taken": disc.get("actions_taken") or 0,
            "in_app": None,
            "off_app": None,
            "deep_links_fired": disc.get("deep_links_fired") or 0,
        }
    else:
        steps = {"measured": False, "basis": None, "taken": 0,
                 "in_app": None, "off_app": None}

    return {
        "activity_coverage": activity,
        "line_coverage": line,
        "screens": screens,
        "steps": steps,
        "intent_sweep": {
            "measured": bool(sweep),
            "targets": sweep.get("targets", 0),
            "reached": len(sweep.get("reached", [])),
        },
        "ai_guidance": rep.get("ai_guidance", True),
        "degraded": bool(rep.get("degraded")),
        "degraded_reason": rep.get("degraded_reason") or "",
    }


def build(report_path: str, device: dict, out_path: str) -> dict:
    with open(report_path) as f:
        rep = json.load(f)

    mode = "pipeline"
    verdict = "failed"
    bugs: list[dict] = []
    visited = total = 0
    coverage = 0.0
    unreachable: list[dict] = []
    repro_verdict = None

    if "repro" in rep:
        repro_verdict = rep["repro"]["verdict"]
    disc = rep.get("discovery")
    sweep = (disc or {}).get("intent_sweep") or {}
    if disc:
        visited = len(disc["visited_activities"])
        total = len(rep["map"]["activities"])
        coverage = disc["coverage_pct"]
        bugs = disc["bugs"]
        unreachable = disc["unreachable"]

    if "repro" in rep and not disc:
        mode = "repro"
        verdict = ("reproduced" if repro_verdict == "reproduced"
                   else "not_reproduced")
    elif disc:
        mode = "pipeline" if "repro" in rep else "discover"
        if bugs:
            verdict = "bugs_found"
        elif visited == 0:
            # nothing was exercised — calling it "clean" would be a lie
            verdict = "inconclusive"
        else:
            verdict = "clean"

    app = rep.get("app") or rep.get("package", "the app")
    n_bugs = len(bugs)
    if verdict == "bugs_found":
        note = (f"We tested {app} on Android {device['android_version']} "
                f"({device['arch']}, {device['runtime']}). "
                f"Visited {visited}/{total} screens ({coverage}% coverage) "
                f"and found {n_bugs} unique crash{'es' if n_bugs != 1 else ''}. "
                f"Each one below comes with its stack trace and the exact "
                f"steps that triggered it.")
    elif verdict == "clean":
        note = (f"We tested {app} on Android {device['android_version']} "
                f"({device['arch']}, {device['runtime']}). "
                f"Visited {visited}/{total} screens ({coverage}% coverage) "
                f"and found no crashes. ")
        if unreachable:
            note += (f"{len(unreachable)} screen(s) couldn't be reached — "
                     f"reasons are listed below.")
        else:
            note += "Every mapped screen was reached."
    elif verdict == "inconclusive":
        note = (f"We tried to test {app} on Android {device['android_version']} "
                f"({device['arch']}, {device['runtime']}) but couldn't exercise "
                f"any of its {total} screens — every route in was blocked "
                f"(reasons listed below). No verdict on bugs: nothing was "
                f"actually tested.")
    elif verdict == "reproduced":
        note = (f"We reproduced the reported bug in {app} on Android "
                f"{device['android_version']} ({device['arch']}, "
                f"{device['runtime']}). The step-by-step trace, screenshots "
                f"and crash log are in the run artifacts.")
    elif verdict == "not_reproduced":
        note = (f"We could not reproduce the reported bug in {app} on "
                f"Android {device['android_version']} ({device['arch']}, "
                f"{device['runtime']}). The full action trace is in the run "
                f"artifacts — worth checking whether the report's steps "
                f"match this build.")
    else:
        note = (f"The test run for {app} did not complete. Check the GitHub "
                f"Actions log for details.")

    summary = {
        "verdict": verdict,
        "mode": mode,
        "device": device,
        "app": app,
        "package": rep.get("package"),
        "coverage_pct": coverage,
        "visited": visited,
        "total": total,
        "bugs": [
            {"exception": b["exception"],
             "occurrences": b["occurrences"],
             "activity": b["first_seen_activity"],
             "fingerprint": b["fingerprint"]}
            for b in bugs
        ],
        "unreachable": unreachable,
        "intent_sweep": {
            "targets": sweep.get("targets", 0),
            "reached": len(sweep.get("reached", [])),
            "attempts": sweep.get("attempts", []),
        },
        "wall_seconds": rep.get("wall_seconds"),
        "customer_note": note,
        "run_artifacts": "out/",
        "metrics": _metrics(rep, disc, sweep, visited, total, coverage,
                            unreachable),
    }
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--android-version", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--runtime", required=True)
    args = ap.parse_args()
    s = build(args.report, {
        "android_version": args.android_version,
        "arch": args.arch,
        "runtime": args.runtime,
    }, args.out)
    print(json.dumps({"verdict": s["verdict"],
                      "coverage_pct": s["coverage_pct"],
                      "bugs": len(s["bugs"])}))


if __name__ == "__main__":
    main()
