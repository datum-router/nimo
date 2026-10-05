"""Main reproduction loop for nimo.

Two modes:

repro (default) — bug report + APK in, verdict out:
    nimo repro --apk demo/01-notepad/app.apk \\
        --package bander.notepad \\
        --bug demo/01-notepad/bug_report.md \\
        --out out/notepad/

discover — APK only, the agent explores and hunts for bugs itself:
    nimo repro --discover --apk app.apk \\
        --package com.example.app \\
        --out out/discovery/

Flow: install APK -> loop { dump UI -> LLM picks action -> execute ->
check logcat } -> write repro_report.json with steps, screenshots, verdict
(or the list of bugs found in discover mode).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

from . import actions, device, gestures
from ..llm import get_backend
from .prompts import (DISCOVER_SYSTEM_PROMPT, SYSTEM_PROMPT,
                      render_discover_step, render_step)

#: Exit code for "ran to completion, the bug did not reproduce".
#: Distinct from 2 (argparse usage error) so CI can accept a legitimate
#: negative result without also swallowing an invocation mistake.
EXIT_NOT_REPRODUCED = 4

MAX_STEPS = 25
MAX_DISCOVER_BUGS = 5


def _find_xy(elements: list[dict], label: str) -> tuple[int, int] | None:
    label = label.strip().lower()
    for e in elements:
        if e["label"].strip().lower() == label:
            return e["x"], e["y"]
    for e in elements:  # substring fallback
        if label and label in e["label"].strip().lower():
            return e["x"], e["y"]
    return None


def reproduce(apk: str, package: str, bug_report: str | None, out_dir: str,
              max_steps: int = MAX_STEPS, discover: bool = False) -> dict:
    """Run the agent.

    repro mode (default): needs a bug report; verdict is reproduced/not_reproduced.
    discover mode: no bug report; the agent explores the APK hunting for bugs and
    returns every crash it finds.
    """
    os.makedirs(out_dir, exist_ok=True)
    shots = os.path.join(out_dir, "screenshots")
    os.makedirs(shots, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    serial = device.check_connected()
    print(f"[nimo] device: {serial} | mode: {'discover' if discover else 'repro'}")

    device.force_stop(package)
    device.install(apk)
    device.clear_logcat()
    device.launch(package)
    device.wait(2.0)

    llm = get_backend()
    print(f"[nimo] llm: {llm.base_url} / {llm.model}")

    # Live action feed, same file shape the crawler writes, so the hosted
    # run page renders repro runs exactly as it renders discovery runs.
    feed = actions.ActionFeed(out_dir)
    feed.record("launch", label=package,
                activity=device.current_activity() or "")

    report_text = ""
    if not discover:
        with open(bug_report) as f:
            report_text = f.read()

    history: list[str] = []
    steps: list[dict] = []
    bugs_found: list[dict] = []
    # Steps that navigated out of the app under test. Recorded rather than
    # merely corrected: a run that spent most of its budget escaping the app
    # produced weak evidence, and the report must be able to say so.
    off_app_steps: list[dict] = []
    verdict = "not_reproduced"
    crash_log: str | None = None

    for i in range(max_steps):
        try:
            elements = device.dump_ui()
        except device.DeviceError as exc:
            # UI dump failing often means the app crashed mid-transition
            elements = []
            history.append(f"step {i}: ui dump failed ({exc})")

        system = DISCOVER_SYSTEM_PROMPT if discover else SYSTEM_PROMPT
        if discover:
            user_msg = render_discover_step(package, elements, history, len(bugs_found))
        else:
            user_msg = render_step(report_text, elements, history)
        # Hand the raw dump to the backend as well as the rendered prompt.
        # A network model reads the prompt; the deterministic fallback needs
        # the structured elements to pick an unvisited control. Optional by
        # design -- a plain LLMClient has no `observe` and ignores this.
        observe = getattr(llm, "observe", None)
        if callable(observe):
            observe(elements)
        action_raw = llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]
        )
        try:
            start = action_raw.index("{")
            end = action_raw.rindex("}") + 1
            action = json.loads(action_raw[start:end])
        except (ValueError, json.JSONDecodeError):
            history.append(f"step {i}: llm returned non-JSON, retrying")
            continue

        kind = action.get("action")
        why = action.get("why", "")
        desc = f"{kind} {action.get('label', '')} {action.get('text', '')}".strip()
        print(f"[nimo] step {i+1}: {desc}  ({why})")

        # Coordinates of whatever this step actually touched, for the live
        # view's tap markers. Captured during execution rather than guessed
        # afterwards, because the element list is re-dumped every step.
        touched: tuple[int, int] | None = None

        try:
            if kind == "tap":
                xy = _find_xy(elements, action.get("label", ""))
                if xy:
                    device.tap(*xy)
                    touched = xy
            elif kind == "long_press":
                xy = _find_xy(elements, action.get("label", ""))
                if xy:
                    gestures.long_press(xy[0], xy[1], ms=800)
                    touched = xy
            elif kind == "type":
                xy = _find_xy(elements, action.get("label", ""))
                if xy:
                    device.tap(*xy)
                    touched = xy
                device.wait(0.5)
                device.input_text(action.get("text", ""))
            elif kind == "swipe_up":
                device.swipe(500, 1500, 500, 400)
            elif kind == "swipe_down":
                device.swipe(500, 400, 500, 1500)
            elif kind == "back":
                device.press_back()
            elif kind == "done":
                verdict = "reproduced" if action.get("verdict") == "reproduced" else "not_reproduced"
                history.append(f"step {i}: agent finished: {why}")
                break
            else:
                history.append(f"step {i}: unknown action {kind!r}, skipped")
                continue
        except device.DeviceError as exc:
            history.append(f"step {i}: {desc} -> device error: {exc}")
            continue

        device.wait(1.2)
        history.append(f"step {i}: {desc} ({why})")
        steps.append({"n": i + 1, "action": action, "why": why})

        # Feed the live view. The crawler has always written this file, so
        # the page's app map, action chips and tap markers were populated in
        # discover/pipeline mode and completely empty in repro mode -- which
        # is what the operator saw as "starting..." and "no screens yet"
        # beside a log that was visibly busy. Same feed, so the frontend
        # needs no mode-specific branch.
        try:
            feed_kind = {"tap": "tap", "long_press": "tap", "type": "type",
                         "swipe_up": "swipe", "swipe_down": "swipe",
                         "back": "back"}.get(kind or "", "tap")
            feed.record(feed_kind,
                        x=touched[0] if touched else None,
                        y=touched[1] if touched else None,
                        label=str(action.get("label") or kind or ""),
                        activity=device.current_activity() or "")
        except device.DeviceError:
            pass  # the live feed is cosmetic; never fail a run for it

        # Stay inside the app under test.
        #
        # Observed in CI run #29: step 2 tapped "Notepad, Navigate home" --
        # the ActionBar up affordance -- which dropped the run onto the
        # launcher. Steps 10-25 then explored the Google search widget, the
        # Gallery and the Camera, and the run still reported
        # `not_reproduced` for Notepad. That verdict was about an app the
        # run had never opened, which is exactly the class of false
        # statement this product exists to prevent.
        #
        # `device.reset_foreground` is not the right tool here: it treats the
        # launcher as an acceptable resting place, because explicit `am
        # start` works from home and the crawler relaunches by intent
        # anyway. A repro loop has no such next step -- it drives the UI it
        # is looking at -- so it must put the app back itself.
        try:
            cur_pkg = device.current_package()
        except device.DeviceError:
            cur_pkg = None
        if cur_pkg and cur_pkg != package:
            off_app_steps.append({"step": i + 1, "package": cur_pkg,
                                  "action": desc})
            print(f"[nimo] step {i+1} left the app (now {cur_pkg}) — relaunching "
                  f"{package}", flush=True)
            history.append(
                f"step {i}: left the app into {cur_pkg}, relaunched {package}")
            try:
                device.launch(package)
                device.wait(2.0)
                # The app's state is fresh, so screens previously judged
                # exhausted deserve another look. Without this the explorer
                # went straight back to Back and livelocked: run #31 spent
                # 19 of 25 steps pressing Back with 18 relaunches.
                notify = getattr(llm, "relaunched", None)
                if callable(notify):
                    notify()
            except device.DeviceError as exc:
                history.append(f"step {i}: relaunch failed: {exc}")

        shot = os.path.join(shots, f"step_{i+1:02d}.png")
        try:
            device.screenshot(shot)
        except device.DeviceError:
            pass

        crash_log = device.recent_crash(package)
        if crash_log:
            if discover:
                bug = {
                    "n": len(bugs_found) + 1,
                    "found_at_step": i + 1,
                    "trail": history[-8:],
                    "crash_log": crash_log,
                    "screenshot": f"screenshots/step_{i+1:02d}.png",
                }
                bugs_found.append(bug)
                print(f"[nimo] bug #{len(bugs_found)} found — relaunching to keep hunting")
                history.append(f"step {i}: BUG #{len(bugs_found)} captured, relaunching")
                device.force_stop(package)
                device.clear_logcat()
                device.launch(package)
                device.wait(2.0)
                if len(bugs_found) >= MAX_DISCOVER_BUGS:
                    print("[nimo] bug budget reached, wrapping up")
                    break
                continue
            verdict = "reproduced"
            history.append(f"step {i}: FATAL EXCEPTION detected in logcat")
            break

    # final check even if agent said done
    if verdict == "not_reproduced":
        crash_log = device.recent_crash(package)
        if crash_log:
            verdict = "reproduced"

    try:
        device.screenshot(os.path.join(shots, "final.png"))
    except device.DeviceError:
        pass

    report = {
        "tool": "nimo",
        "version": "0.1.0",
        "mode": "discover" if discover else "repro",
        "started": started,
        "finished": datetime.now(timezone.utc).isoformat(),
        "apk": os.path.basename(apk),
        "package": package,
        "llm": {"base_url": llm.base_url, "model": llm.model},
        "verdict": ("discovery_complete" if discover
                    else verdict),
        "bugs_found": bugs_found if discover else None,
        "steps_taken": len(steps),
        # How much of the step budget was spent outside the app under test.
        # A `not_reproduced` reached after escaping the app repeatedly is
        # weak evidence, and the consumer of this report must be able to see
        # that rather than infer it from the trail.
        "off_app_steps": off_app_steps,
        "steps_in_app": max(0, len(steps) - len(off_app_steps)),
        "history": history,
        "crash_log": crash_log,
    }
    with open(os.path.join(out_dir, "repro_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    if discover:
        print(f"[nimo] discovery complete: {len(bugs_found)} bug(s) found "
              f"in {len(steps)} steps")
    else:
        print(f"[nimo] verdict: {verdict} after {len(steps)} steps")
    print(f"[nimo] report: {out_dir}/repro_report.json")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="nimo bug reproduction agent")
    ap.add_argument("--apk", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--bug", required=False, default=None,
                    help="bug report markdown file (repro mode)")
    ap.add_argument("--discover", action="store_true",
                    help="APK-only exploratory bug discovery, no bug report needed")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    args = ap.parse_args()
    if not args.discover and not args.bug:
        ap.error("--bug is required unless --discover is set")
    t0 = time.time()
    report = reproduce(args.apk, args.package, args.bug, args.out,
                       args.max_steps, discover=args.discover)
    print(f"[nimo] wall time: {time.time() - t0:.1f}s")
    if args.discover:
        raise SystemExit(0)
    # Exit codes are a contract, and "the bug did not reproduce" is a RESULT,
    # not a malfunction. It used to share code 2 with argparse's usage error,
    # which left CI unable to tell "nimo ran correctly and found nothing" from
    # "nimo was invoked wrongly" -- so the demo workflow could not tolerate a
    # not_reproduced verdict without also hiding real breakage.
    #
    #   0 reproduced (a real FATAL EXCEPTION was observed)
    #   2 usage error (argparse's own code -- untouched)
    #   3 LLM backend unavailable (see nimo.cli)
    #   4 ran to completion, bug did not reproduce
    raise SystemExit(0 if report["verdict"] == "reproduced"
                     else EXIT_NOT_REPRODUCED)


if __name__ == "__main__":
    main()
