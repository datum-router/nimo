#!/usr/bin/env bash
# nimo: run the test pipeline with live progress, then summarize.
#
# Everything that touches the device lives here, because it must run
# while the device is alive. The Android emulator (reactivecircus action)
# is killed when its step ends, so the emulator path invokes this script
# INSIDE the boot step. The redroid container survives across steps, so
# the redroid path calls it from a regular step. Same script, both paths.
#
# Args: RUNTIME ANDROID_SERIAL ANDROID_VERSION ARCH MAX_MINUTES MODE HAS_BUG_REPORT
#   HAS_BUG_REPORT is "true"/"false" (a GitHub expression value).
set -u

RUNTIME="$1"
export ANDROID_SERIAL="$2"
AVER="$3"; AARCH="$4"; MAXMIN="$5"; MODE="$6"; HASBUG="$7"

ARGS=(--apk app.apk --out out --max-minutes "$MAXMIN")
if [ "$HASBUG" = "true" ]; then ARGS+=(--bug bug_report.md); fi
if [ "$MODE" = "repro" ]; then ARGS+=(--repro-only); fi

# one structured action feed powers the whole live view: screens in
# first-seen order, the last actions (with click coordinates), current screen
write_progress() {
  python3 - <<'EOF'
import json, os, datetime
ws = os.environ.get("GITHUB_WORKSPACE", ".")
run_id = open("/tmp/run_id").read().strip()
p_path = "run-%s/progress.json" % run_id
os.makedirs("run-%s" % run_id, exist_ok=True)
try:
    old = json.load(open(p_path))
except Exception:
    old = {}
feed = os.path.join(ws, "out", "crawl", "actions.jsonl")
screens, counts, acts, cur = [], {}, [], ""
try:
    lines = open(feed).read().strip().split("\n")
except OSError:
    lines = []
for ln in lines[-400:]:
    try: a = json.loads(ln)
    except Exception: continue
    act = a.get("activity") or ""
    if act and act not in counts:
        counts[act] = 0
        screens.append(act)
    if act:
        counts[act] += 1
    cur = act or cur
    acts.append(a)
acts = acts[-40:]
log = os.path.join(ws, "pipeline.log")
phase, visited, total = "", 0, 0
try:
    txt = open(log).read()
    import re
    m = re.findall(r"\[crawl\] visiting \S+ \((\d+)/", txt)
    if m: visited = int(m[-1])
    m = re.search(r"map: (\d+) activities", txt)
    if m: total = int(m.group(1))
    ph = re.findall(r"^\[nimo\] (Path|Analyzing|analyzing|sweep done|crawl done|intent sweep)[^\n]*",
                    txt, re.M)
    if ph: phase = ph[-1].strip()
except OSError:
    pass
d = {"status": "running",
     "phase": phase or old.get("phase") or "starting",
     "visited": visited or old.get("visited", 0),
     "total": total or old.get("total", 0),
     "current_activity": cur,
     "screens": [{"a": s, "n": counts[s]} for s in screens],
     "actions": acts,
     "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
json.dump(d, open(p_path, "w"))
EOF
}

# background: every 20s push progress.json + latest screenshot to live branch
(
  cd /tmp/live
  write_progress
  git add "run-$RUN_ID" 2>/dev/null
  git commit -qm "run $RUN_ID progress" || true
  git push -q -f origin "$LIVE_BRANCH" || true
  while kill -0 $PPID 2>/dev/null; do
    sleep 20
    write_progress
    # timeout: a wedged adb (sick emulator) must not stall the live feed
    timeout 30 adb -s "$ANDROID_SERIAL" exec-out screencap -p > "run-$RUN_ID/latest.png" 2>/dev/null || true
    git add "run-$RUN_ID" 2>/dev/null
    git commit -q --amend --no-edit || git commit -qm "run $RUN_ID progress"
    git push -q -f origin "$LIVE_BRANCH" || true
  done
) &
PUSHER=$!

# ---- LLM preflight ------------------------------------------------
# The free tier is bimodal: it serves fine for stretches, then returns
# 402/500 for stretches (measured 2026-10-05: twelve consecutive successes,
# then five consecutive ENOSPC 500s minutes later). A sustained outage used
# to abort the run AFTER the emulator had booted -- throwing away the slow,
# expensive part and returning nothing at all.
#
# So probe once, cheaply, before committing to the long run. If the backend
# is down, explore deterministically rather than not at all.
#
# This cannot fabricate a finding. A `reproduced` verdict comes only from the
# oracle observing a real FATAL EXCEPTION, which needs no LLM at all, so
# degraded mode loses intelligent navigation -- not honesty. It is labelled
# in the log, in summary.json and on the results page.
DEGRADED=0
if [ "${NIMO_BACKEND:-}" != "null" ]; then
  if python3 -c "
from nimo.llm.client import LLMClient
LLMClient(retries=1, backoff=1.0).chat([{'role':'user','content':'ok'}])
" >/dev/null 2>&1; then
    echo "[nimo] llm preflight: ok"
  else
    echo "[nimo] llm preflight: FAILED — backend unreachable."
    echo "[nimo] falling back to deterministic exploration (no AI guidance)."
    echo "[nimo] crash detection is unaffected: the oracle needs no LLM."
    export NIMO_BACKEND=null
    DEGRADED=1
  fi
fi

# Watchdog: a wedged adb (sick emulator) must never burn the whole
# 6-hour job timeout. Kill the pipeline past the user's budget plus
# slack; the honest "failed" fallback below still reports the outcome.
timeout "$((MAXMIN + 10))m" nimo pipeline "${ARGS[@]}" 2>&1 | tee pipeline.log
STATUS=${PIPESTATUS[0]}
if [ "$STATUS" -eq 124 ]; then
  echo "watchdog: pipeline exceeded $((MAXMIN + 10)) minutes, killed"
fi
kill $PUSHER 2>/dev/null || true
wait $PUSHER 2>/dev/null || true
echo "pipeline exit: $STATUS"

# summarize — or an honest fallback, never a fake verdict
if [ -f out/pipeline_report.json ]; then
  nimo summarize \
    --report out/pipeline_report.json \
    --out "summary.json" \
    --android-version "$AVER" \
    --arch "$AARCH" \
    --runtime "$RUNTIME"
else
  # Exit 3 means the LLM backend was unreachable (see nimo.cli). That is an
  # infrastructure outage, not a test result, and the customer note must say
  # so -- "no report" would read as though their app passed or broke.
  STATUS="$STATUS" RUNTIME="$RUNTIME" AVER="$AVER" AARCH="$AARCH" python3 <<'EOF'
import json, os
status = int(os.environ.get("STATUS") or 0)
if status == 3:
    verdict = "backend_unavailable"
    note = ("The AI backend was unreachable, so no verdict could be produced. "
            "This is an outage on the model provider, not a result for your "
            "app. Retry, or configure a provider key (NIMO_API_KEY).")
elif status == 124:
    verdict = "failed"
    note = ("The run exceeded its time budget and was stopped. "
            "Check the Actions log.")
else:
    verdict = "failed"
    note = "The test run did not produce a report. Check the Actions log."
json.dump({"verdict": verdict,
           "customer_note": note,
           "exit_code": status,
           "device": {"android_version": os.environ["AVER"],
                      "arch": os.environ["AARCH"],
                      "runtime": os.environ["RUNTIME"]},
           "bugs": [], "unreachable": [], "coverage_pct": 0,
           "visited": 0, "total": 0}, open("summary.json", "w"))
EOF
fi

# The probe only catches a backend that was ALREADY down. The tier flaps
# inside a single run -- measured: probe ok, then the first real call 402 --
# so the authoritative signal is what the pipeline actually experienced.
if [ -f out/pipeline_report.json ]; then
  if python3 -c "
import json,sys
r = json.load(open('out/pipeline_report.json'))
sys.exit(0 if r.get('degraded') or r.get('ai_guidance') is False else 1)
" 2>/dev/null; then
    echo "[nimo] run completed WITHOUT ai guidance (backend lost mid-run)"
    DEGRADED=1
  fi
fi

# Label a degraded run everywhere it surfaces. A customer reading "no bugs
# found" deserves to know the exploration was random rather than guided --
# an unlabelled degraded run is a quieter version of the false green.
if [ "$DEGRADED" -eq 1 ] && [ -f summary.json ]; then
  python3 <<'EOF'
import json
try:
    s = json.load(open("summary.json"))
except Exception:
    s = {}
s["ai_guidance"] = False
s["degraded"] = True
note = ("NOTE: the AI backend was unreachable, so the app was explored "
        "deterministically rather than intelligently. Crash detection was "
        "unaffected -- any bug listed here is a real observed crash -- but "
        "coverage is lower than a guided run and 'no bugs found' is weaker "
        "evidence than usual. Retry later, or configure a provider key.")
s["customer_note"] = (note + " " + (s.get("customer_note") or "")).strip()
json.dump(s, open("summary.json", "w"))
EOF
fi
timeout 30 adb -s "$ANDROID_SERIAL" exec-out screencap -p > final.png 2>/dev/null || true
cd /tmp/live
mkdir -p "run-$RUN_ID"
cp "$GITHUB_WORKSPACE/summary.json" "run-$RUN_ID/summary.json"
cp "$GITHUB_WORKSPACE/final.png" "run-$RUN_ID/final.png" 2>/dev/null || true
cp "$GITHUB_WORKSPACE/pipeline.log" "run-$RUN_ID/pipeline.log" 2>/dev/null || true
python3 - <<'EOF'
import json
p = "run-%s/progress.json" % open("/tmp/run_id").read().strip()
try:
    d = json.load(open(p))
except Exception:
    d = {}
d["status"] = "done"
json.dump(d, open(p, "w"))
EOF
git add "run-$RUN_ID"
git commit -q --amend --no-edit || git commit -qm "run $RUN_ID done"
git push -q -f origin "$LIVE_BRANCH" || true

# Propagate the pipeline's real exit code. summary.json, the screenshots and
# the live branch have all been published above, so failing here loses
# nothing — but it stops a run whose pipeline died from reporting a green
# check. A product whose claim is "it cannot lie about a bug" must not lie
# about whether it ran at all.
exit "$STATUS"
