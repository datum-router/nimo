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
  RUNTIME="$RUNTIME" AVER="$AVER" AARCH="$AARCH" python3 <<'EOF'
import json, os
json.dump({"verdict": "failed",
           "customer_note": "The test run did not produce a report. Check the Actions log.",
           "device": {"android_version": os.environ["AVER"],
                      "arch": os.environ["AARCH"],
                      "runtime": os.environ["RUNTIME"]},
           "bugs": [], "unreachable": [], "coverage_pct": 0,
           "visited": 0, "total": 0}, open("summary.json", "w"))
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
