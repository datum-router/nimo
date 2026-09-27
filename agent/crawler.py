"""Systematic exploration: reach every corner of the APK.

Unlike the free-roam discover mode (LLM wandering), the crawler works from
the static map (agent/apkmeta.py):

  1. Launch every activity DIRECTLY via `am start -n` — deep screens are
     reached without needing a lucky tap sequence.
  2. Fire every deep link from the manifest's intent filters.
  3. On each screen, interact with every unvisited element (breadth-first),
     feed edge-case inputs to text fields, and enqueue newly seen screens.
  4. Login walls are handed to agent/auth.py (credentials, sign-up,
     Google Sign-In via the pre-authed snapshot).
  5. Every action is crash-watched via logcat; crashes are fingerprinted
     and deduped by agent/triage.py.
  6. Tarpit recovery (agent/tarpit.py — the Jev pattern): after several
     consecutive zero-gain screens the LLM is consulted once for an
     escape action; abstention falls back to BACK + foreground reset.
     Consultations are capped per run; the deterministic crawl is the
     engine, the LLM a rarely-used safety net.

State fingerprinting (activity + visible element signature) keeps it from
looping. Coverage = activities visited / activities in the manifest —
a measurable "we tested every corner" claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field

from . import apkmeta, auth, blockers, device, tarpit, triage
from .llm import LLMClient

FIELD_VALUES_PROMPT = """You are helping an automated app-testing agent. \
Suggest a realistic test value for each text field on this Android screen.

Fields (label | class):
{fields}

App context: {app_name} ({package})

Reply with exactly one JSON object mapping each label to a test value, e.g. \
{{"Email": "test@example.com", "Age": "30"}}. Use realistic values. Nothing else."""

EDGE_INPUTS = ["", "nimo", "A" * 200, "test@example.com", "<script>'\"&<>"]


@dataclass
class CrawlBudget:
    max_activities: int = 60
    max_actions: int = 400
    max_minutes: int = 30


@dataclass
class CrawlResult:
    package: str
    total_activities: int
    visited_activities: list[str] = field(default_factory=list)
    screens_visited: int = 0
    actions_taken: int = 0
    bugs: list[triage.Bug] = field(default_factory=list)
    unreachable: list[dict] = field(default_factory=list)
    auth_events: list[dict] = field(default_factory=list)
    deep_links_fired: int = 0
    coverage_attempts: list[dict] = field(default_factory=list)

    @property
    def coverage_pct(self) -> float:
        if not self.total_activities:
            return 0.0
        return round(100 * len(self.visited_activities) / self.total_activities, 1)


def _state_fp(activity: str | None, elements: list[dict]) -> str:
    sig = "|".join(sorted(
        f"{e['label'] or ''}#{e['class']}#{e['clickable']}" for e in elements))
    return hashlib.sha1(f"{activity}|{sig}".encode()).hexdigest()[:12]


def _el_sig(e: dict) -> str:
    return f"{e['label'] or ''}#{e['class']}"


def _is_editable(e: dict) -> bool:
    return "edittext" in e["class"].lower()


def suggest_field_values(elements: list[dict], meta: apkmeta.ApkMeta,
                         llm: LLMClient) -> dict[str, str]:
    fields = [e for e in elements if _is_editable(e)][:10]
    if not fields:
        return {}
    desc = "\n".join(f"- {e['label'] or '(no label)'} | {e['class']}"
                     for e in fields)
    try:
        raw = llm.chat([
            {"role": "system",
             "content": "Reply with exactly one JSON object, nothing else."},
            {"role": "user", "content": FIELD_VALUES_PROMPT.format(
                fields=desc, app_name=meta.app_name or meta.package,
                package=meta.package)},
        ])
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception:
        return {}


class Crawler:
    def __init__(self, meta: apkmeta.ApkMeta, cfg: auth.AuthConfig,
                 llm: LLMClient, out_dir: str, budget: CrawlBudget,
                 seeds: list[str] | None = None,
                 sweep_labels: dict[str, dict] | None = None):
        self.meta = meta
        self.cfg = cfg
        self.llm = llm
        self.budget = budget
        self.seeds = seeds or []          # activities the intent sweep reached
        self.sweep_labels = sweep_labels or {}  # activity -> blocker label
        self.meta = meta
        self.cfg = cfg
        self.llm = llm
        self.budget = budget
        self.shots = os.path.join(out_dir, "screenshots")
        os.makedirs(self.shots, exist_ok=True)
        self.result = CrawlResult(package=meta.package,
                                  total_activities=len(meta.activities))
        self.trail: list[str] = []
        self.raw_crashes: list[dict] = []
        self.visited_states: set[str] = set()
        self.touched_elements: set[str] = set()  # per-screen element sigs
        self.queue: list[str] = []               # activity class names
        self.queued: set[str] = set()
        self.deadline = time.time() + budget.max_minutes * 60
        self.shot_n = 0
        self.launch_failures: dict[str, str] = {}
        self.redirects: dict[str, str] = {}      # requested -> landed activity
        self.login_redirects: set[str] = set()    # requested, landed on login
        self.permission_dialogs = 0  # system permission dialogs auto-dismissed
        self.tarpit = tarpit.TarpitDetector()  # stall detection (Jev pattern)

    # ---- helpers -----------------------------------------------------
    def _log(self, msg: str) -> None:
        print(f"[crawl] {msg}", flush=True)

    def _shot(self) -> str:
        self.shot_n += 1
        p = os.path.join(self.shots, f"crawl_{self.shot_n:03d}.png")
        try:
            device.screenshot(p)
        except Exception:
            pass
        return p

    def _check_crash(self, activity: str | None) -> bool:
        crash = device.recent_crash(self.meta.package)
        if not crash:
            return False
        self._log(f"CRASH in {activity}")
        self.raw_crashes.append({
            "package": self.meta.package,
            "activity": activity,
            "trail": self.trail[-8:],
            "stack": crash,
            "screenshot": self._shot(),
        })
        device.force_stop(self.meta.package)
        device.clear_logcat()
        return True

    def _enqueue(self, activity: str | None) -> None:
        if not activity:
            return
        cls = activity.split("/")[-1]
        if cls.startswith("."):
            cls = self.meta.package + cls
        elif "." not in cls:
            cls = self.meta.package + "." + cls
        known = {a.name for a in self.meta.activities}
        if cls in known and cls not in self.queued \
                and cls not in self.result.visited_activities:
            self.queued.add(cls)
            self.queue.append(cls)

    def _over_budget(self) -> bool:
        b = self.budget
        return (self.result.actions_taken >= b.max_actions
                or len(self.result.visited_activities) >= b.max_activities
                or time.time() > self.deadline)

    # ---- main --------------------------------------------------------
    def run(self) -> CrawlResult:
        pkg = self.meta.package
        device.force_stop(pkg)
        device.clear_logcat()
        device.grant_permissions(pkg, self.meta.permissions)

        if self.meta.main_activity:
            self.queue.append(self.meta.main_activity)
            self.queued.add(self.meta.main_activity)
        # seed with activities the intent sweep already reached — they get
        # full in-screen exploration here, not just a launch
        for s in self.seeds:
            if s not in self.queued:
                self.queued.add(s)
                self.queue.append(s)
        # every other activity is reachable via direct launch too
        for a in self.meta.activities:
            if a.name not in self.queued:
                self.queued.add(a.name)
                self.queue.append(a.name)

        while self.queue and not self._over_budget():
            activity = self.queue.pop(0)
            self.queued.discard(activity)
            states_before = len(self.visited_states)
            crashes_before = len(self.raw_crashes)
            self._visit_activity(activity)
            # tarpit detection: only episodes that actually reached the
            # activity count — launch failures and redirects are classified
            # elsewhere, they are not stalls
            if activity in self.result.visited_activities:
                gained = (len(self.visited_states) > states_before
                          or len(self.raw_crashes) > crashes_before)
                if self.tarpit.observe(gained):
                    self._escape_tarpit(activity)

        # deep links last (they can land anywhere)
        for a in self.meta.activities:
            for link in a.deep_links:
                if self._over_budget():
                    break
                self._fire_deep_link(link)

        # anything never visited is unreachable — labeled, never silent
        visited = set(self.result.visited_activities)
        budget_out = self._over_budget()
        for a in self.meta.activities:
            if a.name not in visited:
                if a.name in self.launch_failures:
                    msg = self.launch_failures[a.name]
                    code = ("not-exported"
                            if "securityexception" in msg.lower()
                            or "not exported" in msg.lower()
                            else "launch-failed")
                    entry = blockers.label(a.name, code, msg)
                elif a.name in self.login_redirects:
                    entry = blockers.label(
                        a.name, "login-wall",
                        f"direct launch redirected to {self.redirects[a.name]}")
                elif a.name in self.redirects:
                    # the crawler's own direct launch is the freshest
                    # evidence — prefer it over the sweep's older label
                    entry = blockers.label(
                        a.name, "launch-failed",
                        f"direct launch redirected to {self.redirects[a.name]}")
                elif a.name in self.sweep_labels:
                    # intent sweep already proved the precise failure
                    entry = self.sweep_labels[a.name]
                elif not a.exported:
                    entry = blockers.label(a.name, "not-exported")
                elif budget_out:
                    entry = blockers.label(a.name, "budget-exhausted")
                else:
                    entry = blockers.label(a.name, "no-route")
                self.result.unreachable.append(entry)

        self.result.coverage_attempts.append({
            "strategy": "direct-launch + BFS crawl + auth ladder",
            "activities_visited": len(self.result.visited_activities),
            "screens_visited": self.result.screens_visited,
            "login_redirects": len(self.login_redirects),
            "permission_dialogs_dismissed": self.permission_dialogs,
            "note": "redirected launches are NOT counted as visited",
        })
        self.result.bugs = triage.dedupe(self.raw_crashes)
        return self.result

    @staticmethod
    def _same_activity(requested: str, current: str | None) -> bool:
        """Did the launch actually land on the requested activity?"""
        if not current:
            return True  # dumpsys parse failed — assume we arrived (old path)
        cur_cls = current.split("/")[-1].lstrip(".")
        req_cls = requested.split(".")[-1]
        return cur_cls == req_cls or cur_cls == requested or \
            requested.endswith("." + cur_cls)

    def _visit_activity(self, activity: str) -> None:
        pkg = self.meta.package
        comp = apkmeta.component(pkg, activity)
        # Cold start, like the intent sweep: a warm task can swallow the
        # launch (the activity finishes instantly back into the existing
        # task), while force-stopped explicit launches land reliably.
        # Clearing logcat keeps crash attribution to this launch only.
        device.force_stop(pkg)
        device.clear_logcat()
        # the intent sweep may have left an external app (or a chooser)
        # modal in the foreground — clear it or this launch no-ops
        device.reset_foreground(pkg)
        try:
            device.start_activity(comp)
        except device.DeviceError as exc:
            # Direct launch failed (e.g. SecurityException on non-exported).
            # Don't give up: the activity may still surface via UI navigation,
            # so it stays queued. Record the failure for the final report.
            self.launch_failures[activity] = str(exc)[:500]
            self._log(f"direct launch failed for {activity}: {exc}")
            return
        device.wait(2.0)
        if device.is_permission_dialog():
            # the launch triggered a runtime permission request — grant and
            # dismiss it, then re-launch; otherwise the dialog sits modal and
            # every later launch no-ops behind it (0% coverage)
            self._log("permission dialog blocking launch — dismissing")
            device.dismiss_permission_dialog(pkg, self.meta.permissions)
            self.permission_dialogs += 1
            device.wait(1.0)
            try:
                device.start_activity(comp)
            except device.DeviceError as exc:
                self.launch_failures[activity] = str(exc)[:500]
                self._log(f"direct launch failed for {activity}: {exc}")
                return
            device.wait(2.0)
        if self._check_crash(activity):
            device.start_activity(comp)
            device.wait(2.0)

        cur = device.current_activity()
        if cur:
            self._enqueue(cur)
        if not self._same_activity(activity, cur):
            # One re-check: cold launches on a slow emulator can miss the
            # first foreground read. A genuine redirect still shows the
            # same foreign activity on the second read.
            device.wait(2.0)
            cur = device.current_activity()
            if cur:
                self._enqueue(cur)
        if not self._same_activity(activity, cur):
            # Redirected elsewhere — do NOT count as visited. Counting it
            # inflated coverage. Often a login wall intercepting the launch.
            self.redirects[activity] = cur or "unknown"
            self._log(f"{activity} redirected to {cur} — not counted")
            try:
                els = device.dump_ui()
            except device.DeviceError:
                els = []
            if els and auth.looks_like_login(els):
                self.login_redirects.add(activity)
                self._log(f"{activity} sits behind a login wall")
            return
        if activity not in self.result.visited_activities:
            self.result.visited_activities.append(activity)
        self._log(f"visiting {activity} "
                  f"({len(self.result.visited_activities)}/{self.result.total_activities})")

        # login wall? hand to auth
        try:
            elements = device.dump_ui()
        except device.DeviceError:
            elements = []
        if elements and auth.looks_like_login(elements):
            ok, detail = auth.attempt(elements, self.cfg, self.llm)
            self.result.auth_events.append(
                {"activity": activity, "passed": ok, "detail": detail})
            self._log(f"auth: {'passed' if ok else 'BLOCKED'} — {detail}")
            device.wait(1.5)
            try:
                elements = device.dump_ui()
            except device.DeviceError:
                elements = []

        self._explore_screen(activity, elements)

    def _escape_tarpit(self, activity: str) -> None:
        """A tarpit verdict fired: consult the LLM once for an escape
        action (the Jev pattern). Abstention — or any failure — falls back
        to BACK + foreground reset, never breaks the crawl."""
        self._log(f"TARPIT: {tarpit.STALL_THRESHOLD} zero-gain episodes — "
                  f"consulting LLM ({self.tarpit.consults_used}/"
                  f"{self.tarpit.max_consults})")
        try:
            elements = device.dump_ui()
        except device.DeviceError:
            elements = []
        esc = tarpit.suggest_escape(
            elements, self.trail,
            activity, self.meta.app_name or self.meta.package, self.llm)
        action = str(esc.get("action") or "abstain").lower()
        reason = str(esc.get("reason") or "")[:200]
        self._log(f"tarpit escape: {action} — {reason}")
        if action != "abstain":
            self._execute_escape(action, esc, elements)
        else:
            device.press_back()
            device.wait(1.0)
            device.reset_foreground(self.meta.package)
        self.tarpit.events.append(
            {"activity": activity, "action": action, "reason": reason})
        self.result.coverage_attempts.append({
            "strategy": "tarpit-escape",
            "activity": activity,
            "action": action,
            "reason": reason,
        })

    def _execute_escape(self, action: str, esc: dict,
                        elements: list[dict]) -> None:
        """Carry out the LLM's chosen escape action. Best-effort: a miss
        just logs and the deterministic crawl continues."""
        pkg = self.meta.package
        try:
            if action == "tap":
                target = str(esc.get("target") or "").lower()
                for e in elements:
                    if target and target in (e.get("label") or "").lower() \
                            and e.get("clickable"):
                        device.tap(e["x"], e["y"])
                        self.trail.append(f"tarpit tap {e['label']}")
                        break
            elif action == "back":
                device.press_back()
            elif action == "swipe_up":
                w, h = device.screen_size()
                device.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2))
            elif action == "type":
                target = str(esc.get("target") or "").lower()
                text = str(esc.get("text") or "")
                for e in elements:
                    if "edittext" in (e.get("class") or "").lower() and \
                            (not target
                             or target in (e.get("label") or "").lower()):
                        device.tap(e["x"], e["y"])
                        device.wait(0.5)
                        device.input_text(text)
                        self.trail.append(f"tarpit type {text[:20]!r}")
                        break
            elif action == "relaunch":
                device.force_stop(pkg)
                device.wait(1.0)
                if self.meta.main_activity:
                    device.start_activity(
                        apkmeta.component(pkg, self.meta.main_activity))
            device.wait(1.5)
            self.result.actions_taken += 1
        except device.DeviceError as exc:
            self._log(f"tarpit escape action failed: {exc}")

    def _explore_screen(self, activity: str, elements: list[dict]) -> None:
        fp = _state_fp(activity, elements)
        if fp in self.visited_states:
            return
        self.visited_states.add(fp)
        self.result.screens_visited += 1

        # 1. text fields: LLM-suggested realistic value first, then edge inputs
        values = suggest_field_values(elements, self.meta, self.llm)
        for e in elements:
            if self._over_budget():
                return
            if not _is_editable(e):
                continue
            sig = _el_sig(e)
            if sig in self.touched_elements:
                continue
            self.touched_elements.add(sig)
            val = values.get(e["label"] or "", "nimo test")
            for text in (val, EDGE_INPUTS[len(self.touched_elements) % len(EDGE_INPUTS)]):
                device.tap(e["x"], e["y"])
                device.wait(0.6)
                device.input_text(text)
                device.wait(0.6)
                self.result.actions_taken += 1
                self.trail.append(f"type {text[:20]!r} into {e['label']}")
                if self._check_crash(activity):
                    return
                # submit-ish buttons after typing
                for btn in elements:
                    if btn["clickable"] and any(
                            k in (btn["label"] or "").lower()
                            for k in ("save", "send", "submit", "ok", "done",
                                      "search", "go")):
                        device.tap(btn["x"], btn["y"])
                        device.wait(1.5)
                        self.result.actions_taken += 1
                        self.trail.append(f"tap {btn['label']}")
                        if self._check_crash(activity):
                            return
                        break
                break  # one extra edge input per field is enough per screen

        # 2. clickable elements, breadth-first — but creation/navigation
        # affordances first: editor screens (NoteEdit etc.) only open with
        # real in-app data, so "new/add" has to be tapped before anything
        # that merely dismisses or idles
        try:
            elements = device.dump_ui()
        except device.DeviceError:
            return
        NAV_FIRST = ("new", "add", "create", "compose", "write", "+",
                     "open", "search", "menu", "more")
        def _nav_rank(e: dict) -> int:
            lab = (e.get("label") or "").lower()
            return 0 if any(k in lab for k in NAV_FIRST) else 1
        elements = sorted(elements, key=_nav_rank)
        for e in elements:
            if self._over_budget():
                return
            if not e["clickable"] or _is_editable(e):
                continue
            sig = activity + "::" + _el_sig(e)
            if sig in self.touched_elements:
                continue
            self.touched_elements.add(sig)
            label = e["label"] or e["class"].split(".")[-1]
            # skip obvious exits to keep the crawl inside the app
            if any(k in label.lower() for k in
                   ("log out", "sign out", "delete account")):
                continue
            device.tap(e["x"], e["y"])
            device.wait(1.5)
            self.result.actions_taken += 1
            self.trail.append(f"tap {label}")
            self._shot()
            if self._check_crash(activity):
                return
            new_act = device.current_activity()
            if new_act and new_act.split("/")[0] == self.meta.package:
                self._enqueue(new_act)
                # recurse into genuinely new screens, bounded
                try:
                    new_els = device.dump_ui()
                except device.DeviceError:
                    new_els = []
                if _state_fp(new_act, new_els) not in self.visited_states \
                        and self.result.screens_visited < self.budget.max_activities * 3:
                    self._explore_screen(new_act, new_els)
                    # go back to the parent screen to continue the sweep
                    device.press_back()
                    device.wait(1.0)
            elif new_act:
                # tapped out of the app (browser, settings...) — come back
                device.start_activity(apkmeta.component(
                    self.meta.package, activity))
                device.wait(1.5)

    def _fire_deep_link(self, link: str) -> None:
        try:
            device.start_deep_link(link)
        except device.DeviceError:
            return
        device.wait(1.5)
        self.result.deep_links_fired += 1
        self.trail.append(f"deep link {link}")
        act = device.current_activity()
        if act and act.split("/")[0] == self.meta.package:
            self._enqueue(act)
            if self._check_crash(act):
                return
            try:
                els = device.dump_ui()
            except device.DeviceError:
                els = []
            if els:
                self._explore_screen(act, els)


def explore(meta: apkmeta.ApkMeta, cfg: auth.AuthConfig, llm: LLMClient,
            out_dir: str, budget: CrawlBudget | None = None,
            seeds: list[str] | None = None,
            sweep_labels: dict[str, dict] | None = None) -> CrawlResult:
    crawler = Crawler(meta, cfg, llm, out_dir, budget or CrawlBudget(),
                      seeds=seeds, sweep_labels=sweep_labels)
    return crawler.run()
