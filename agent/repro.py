"""Main reproduction loop for nimo.

Usage:
    python -m agent.repro --apk demo/01-notepad/app.apk \\
        --package bander.notepad \\
        --bug demo/01-notepad/bug_report.md \\
        --out out/notepad/

Flow: install APK -> loop { dump UI -> LLM picks action -> execute ->
check logcat } -> write repro_report.json with steps, screenshots, verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

from . import device
from .llm import LLMClient
from .prompts import SYSTEM_PROMPT, render_step

MAX_STEPS = 25


def _find_xy(elements: list[dict], label: str) -> tuple[int, int] | None:
    label = label.strip().lower()
    for e in elements:
        if e["label"].strip().lower() == label:
            return e["x"], e["y"]
    for e in elements:  # substring fallback
        if label and label in e["label"].strip().lower():
            return e["x"], e["y"]
    return None


def reproduce(apk: str, package: str, bug_report: str, out_dir: str,
              max_steps: int = MAX_STEPS) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    shots = os.path.join(out_dir, "screenshots")
    os.makedirs(shots, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    serial = device.check_connected()
    print(f"[nimo] device: {serial}")

    device.force_stop(package)
    device.install(apk)
    device.clear_logcat()
    device.launch(package)
    device.wait(2.0)

    llm = LLMClient()
    print(f"[nimo] llm: {llm.base_url} / {llm.model}")

    with open(bug_report) as f:
        report_text = f.read()

    history: list[str] = []
    steps: list[dict] = []
    verdict = "not_reproduced"
    crash_log: str | None = None

    for i in range(max_steps):
        try:
            elements = device.dump_ui()
        except device.DeviceError as exc:
            # UI dump failing often means the app crashed mid-transition
            elements = []
            history.append(f"step {i}: ui dump failed ({exc})")

        action_raw = llm.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_step(report_text, elements, history)},
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

        try:
            if kind == "tap":
                xy = _find_xy(elements, action.get("label", ""))
                if xy: device.tap(*xy)
            elif kind == "long_press":
                xy = _find_xy(elements, action.get("label", ""))
                if xy: device.swipe(xy[0], xy[1], xy[0], xy[1], ms=800)
            elif kind == "type":
                xy = _find_xy(elements, action.get("label", ""))
                if xy: device.tap(*xy)
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

        shot = os.path.join(shots, f"step_{i+1:02d}.png")
        try:
            device.screenshot(shot)
        except device.DeviceError:
            pass

        crash_log = device.recent_crash(package)
        if crash_log:
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
        "started": started,
        "finished": datetime.now(timezone.utc).isoformat(),
        "apk": os.path.basename(apk),
        "package": package,
        "llm": {"base_url": llm.base_url, "model": llm.model},
        "verdict": verdict,
        "steps_taken": len(steps),
        "history": history,
        "crash_log": crash_log,
    }
    with open(os.path.join(out_dir, "repro_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    print(f"[nimo] verdict: {verdict} after {len(steps)} steps")
    print(f"[nimo] report: {out_dir}/repro_report.json")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="nimo bug reproduction agent")
    ap.add_argument("--apk", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--bug", required=True, help="bug report markdown file")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    args = ap.parse_args()
    t0 = time.time()
    report = reproduce(args.apk, args.package, args.bug, args.out, args.max_steps)
    print(f"[nimo] wall time: {time.time() - t0:.1f}s")
    raise SystemExit(0 if report["verdict"] == "reproduced" else 2)


if __name__ == "__main__":
    main()
