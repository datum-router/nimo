"""Intent enumeration + direct-launch sweep.

apkmeta captures data URIs, but the manifest's intent filters carry more:
custom actions, categories, and mimeTypes. This module turns every one of
them into a concrete `adb shell am start ...` command and fires them all —
a fast, systematic sweep that reaches screens no tap sequence can find.

Unreached targets are labeled with the blockers.py taxonomy, never dropped.
"""

from __future__ import annotations

import argparse
import json

from . import apkmeta, blockers, device

VIEW = "android.intent.action.VIEW"


def enumerate_targets(apk_path: str) -> dict:
    """Static pass: every launchable intent target in the APK.

    Returns {"package": ..., "targets": [{activity, kind, command, exported,
    summary}]} where kind is explicit|deeplink|action|mime and command is
    the adb argv (without the leading "adb").
    """
    meta = apkmeta.analyze(apk_path)
    package = meta.package
    targets: list[dict] = []

    for info in meta.activities:
        if not info.exported and not info.has_intent_filter:
            continue  # not-exported, no filters: only reachable via in-app UI

        def add(kind: str, argv: list[str], summary: str) -> None:
            targets.append({
                "activity": info.name,
                "kind": kind,
                "command": ["shell", "am", "start"] + argv,
                "exported": info.exported,
                "summary": summary,
            })

        add("explicit", ["-n", f"{package}/{info.name}"],
            f"explicit launch of {info.name}")

        for uri in info.deep_links:
            add("deeplink", ["-a", VIEW, "-d", uri],
                f"VIEW {uri} -> {info.name}")

        for act in info.actions:
            if act == VIEW:
                continue  # covered by deeplink/mime targets
            cats = [c for c in info.categories
                    if c not in ("android.intent.category.DEFAULT",)]
            # fire the bare action AND action+type variants: an EDIT
            # without its declared mime type never matches the editor's
            # filter (it either resolves nowhere or to the wrong app)
            variants = [[]] + [["-t", m] for m in info.mime_types]
            for extra in variants:
                argv = ["-a", act] + extra
                for c in cats:
                    argv += ["-c", c]
                typed = f" -t {extra[1]}" if extra else ""
                add("action", argv,
                    f"custom action {act}{typed} -> {info.name}")

        for mime in info.mime_types:
            add("mime", ["-a", VIEW, "-t", mime],
                f"VIEW type {mime} -> {info.name}")

    # dedupe identical commands (manifests often repeat filters)
    seen: set[str] = set()
    uniq: list[dict] = []
    for t in targets:
        key = json.dumps(t["command"], sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    return {"package": package, "targets": uniq}


def fire_all(package: str, targets: list[dict],
             wait_s: float = 1.5) -> dict:
    """Fire every target on the connected device. Fast: ~2s per target.

    Returns {"reached": [activities], "unreachable": [blocker labels],
             "attempts": [{target summary, outcome, detail}]}.
    """
    reached: list[str] = []
    reached_set: set[str] = set()
    unreachable: list[dict] = []
    attempts: list[dict] = []

    for t in targets:
        activity = t["activity"]
        device.force_stop(package)
        # a previous target may have opened an external app (Contacts, the
        # resolver chooser, ...) that now sits modal — clear it or every
        # later am start no-ops behind it
        device.reset_foreground(package)
        device.clear_logcat()
        outcome, detail = "launch-failed", ""
        perm_dialog = False
        try:
            out = device.am_start(t["command"][3:], timeout=20)
            low = (out or "").lower()
            if "securityexception" in low or "not exported" in low:
                outcome, detail = "not-exported", out.strip()[:200]
            else:
                device.wait(wait_s)
                if device.is_permission_dialog():
                    # the app asked for a permission on launch — grant it and
                    # re-fire, otherwise every later target no-ops behind it
                    perm_dialog = True
                    device.dismiss_permission_dialog(package)
                    device.wait(1.0)
                    try:
                        device.am_start(t["command"][3:], timeout=20)
                    except device.DeviceError:
                        pass
                    device.wait(wait_s)
                cur = device.current_activity() or ""
                if (cur and cur.split("/")[0] != package
                        and not device.recent_crash(package)):
                    # an intent hijacker owns the foreground — clear it and
                    # re-fire once before calling it a failure
                    device.reset_foreground(package)
                    device.wait(0.5)
                    try:
                        device.am_start(t["command"][3:], timeout=20)
                    except device.DeviceError:
                        pass
                    device.wait(wait_s)
                    cur = device.current_activity() or ""
                crash = device.recent_crash(package)
                if crash:
                    outcome = "crash-on-launch"
                    detail = crash[:500]
                elif cur.split("/")[0] == package:
                    outcome = "reached"
                    landed = cur.split("/")[-1]
                    if activity not in reached_set:
                        reached.append(activity)
                        reached_set.add(activity)
                    detail = f"landed on {landed}"
                else:
                    detail = f"no-op (foreground: {cur or 'none'})"
                    if "ResolverActivity" in cur:
                        detail += " — intent needs disambiguation"
        except device.DeviceError as e:
            msg = str(e).lower()
            if "securityexception" in msg or "not exported" in msg:
                outcome, detail = "not-exported", str(e)[:200]
            else:
                detail = str(e)[:200]
        attempts.append({"target": t["summary"], "kind": t["kind"],
                         "outcome": outcome, "detail": detail})
        # An activity is unreachable only if NONE of its targets reached it —
        # one failed intent must not condemn an activity another target
        # already landed on.
        if outcome != "reached" and activity not in reached_set and not any(
                u["activity"] == activity for u in unreachable):
            code = {"not-exported": "not-exported",
                    "crash-on-launch": "crash-on-launch"}.get(outcome,
                                                             "launch-failed")
            if outcome == "launch-failed" and perm_dialog:
                code = "permission-loop"
            unreachable.append(blockers.label(activity, code, detail[:120]))

    return {"reached": reached, "unreachable": unreachable,
            "attempts": attempts}


def main() -> None:
    ap = argparse.ArgumentParser(description="nimo intent enumeration sweep")
    ap.add_argument("apk", help="APK to enumerate")
    ap.add_argument("--fire", action="store_true",
                    help="fire all targets on the connected device")
    args = ap.parse_args()

    enum = enumerate_targets(args.apk)
    print(f"[nimo] {enum['package']}: "
          f"{len(enum['targets'])} intent targets enumerated")
    if not args.fire:
        print(json.dumps(enum, indent=2))
        return
    res = fire_all(enum["package"], enum["targets"])
    print(f"[nimo] sweep: {len(res['reached'])} reached, "
          f"{len(res['unreachable'])} unreachable")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
