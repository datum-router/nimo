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
        "wall_seconds": rep.get("wall_seconds"),
        "customer_note": note,
        "run_artifacts": "out/",
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
