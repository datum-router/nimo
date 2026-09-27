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

# background: every 60s push progress.json + latest screenshot to live branch
(
  cd /tmp/live
  first=1
  while kill -0 $PPID 2>/dev/null; do
    sleep 60
    VISITED=$(grep -oE '\[crawl\] visiting [^ ]+ \([0-9]+/' pipeline.log 2>/dev/null | grep -oE '\([0-9]+' | tr -d '(' | tail -1)
    TOTAL=$(grep -oE 'map: [0-9]+ activities' pipeline.log 2>/dev/null | grep -oE '[0-9]+' | head -1)
    PHASE=$(grep -E '^\[nimo\] (Path|Analyzing|analyzing)' pipeline.log 2>/dev/null | tail -1 | sed 's/.*\] //')
    python3 - "$VISITED" "$TOTAL" "$PHASE" <<'EOF'
import json, sys, datetime
v, t, ph = sys.argv[1], sys.argv[2], sys.argv[3]
d = {"status": "running",
     "phase": ph or "starting",
     "visited": int(v) if v else 0,
     "total": int(t) if t else 0,
     "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
json.dump(d, open("run-%s/progress.json" % open("/tmp/run_id").read().strip(), "w"))
EOF
    adb -s "$ANDROID_SERIAL" exec-out screencap -p > "run-$RUN_ID/latest.png" 2>/dev/null || true
    git add "run-$RUN_ID" 2>/dev/null
    if [ "$first" = 1 ]; then git commit -qm "run $RUN_ID progress"; first=0;
    else git commit -q --amend --no-edit; fi
    git push -q -f origin "$LIVE_BRANCH" || true
  done
) &
PUSHER=$!

python3 -m agent.pipeline "${ARGS[@]}" 2>&1 | tee pipeline.log
STATUS=${PIPESTATUS[0]}
kill $PUSHER 2>/dev/null || true
wait $PUSHER 2>/dev/null || true
echo "pipeline exit: $STATUS"

# summarize — or an honest fallback, never a fake verdict
if [ -f out/pipeline_report.json ]; then
  python3 -m agent.summarize \
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
adb -s "$ANDROID_SERIAL" exec-out screencap -p > final.png 2>/dev/null || true
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
