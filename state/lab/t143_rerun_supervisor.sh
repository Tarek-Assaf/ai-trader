#!/usr/bin/env bash
set -u
L=/workspace/state/lab
C=/workspace/state/checkout_t143
LOG=$L/t143_rerun_supervisor.log
log(){ echo "$(date -Is) $*" >> "$LOG"; }
mem(){ cat /sys/fs/cgroup/memory.current 2>/dev/null || echo 0; }
limit(){ cat /sys/fs/cgroup/memory.max 2>/dev/null || echo unknown; }
waitrc(){ local f=$1; while [ ! -s "$L/$f.rc" ]; do sleep 30; done; cat "$L/$f.rc"; }
launch(){
  local tag=$1; shift
  log "launch $tag cgroup_bytes=$(mem) limit=$(limit)"
  ( cd "$C" && PYTHONPATH=. python scripts/lab_recent_zero_cost_worker.py "$@" > "$L/${tag}.log" 2>&1; rc=$?; echo "$rc" > "$L/${tag}.rc"; log "exit $tag rc=$rc cgroup_bytes=$(mem)" ) &
  echo $! > "$L/${tag}.pid"
}
log "supervisor start cgroup_bytes=$(mem) limit=$(limit)"
log "waiting full and xau1a"
waitrc t143_full
waitrc t143_xau_1_a
log "prereqs complete; launch pair nsx3/xau3"
launch nsx3_rerun --instrument NSXUSD --timeframe 3 --candidate-start 0 --candidate-end 2032 --batch-size 4 --staged-generations 3 --max-staged-parents 10 --memory-limit-gb 1.2 --no-progress-seconds 1800 --output "$L/t143_nsx_3_rerun.json" --checkpoint "$L/t143_nsx_3_rerun.checkpoint.json" --progress "$L/t143_nsx_3_rerun.progress.json"
launch xau3_rerun --instrument XAUUSD --timeframe 3 --candidate-start 0 --candidate-end 2201 --batch-size 4 --staged-generations 3 --max-staged-parents 10 --memory-limit-gb 1.2 --no-progress-seconds 1800 --output "$L/t143_xau_3_rerun.json" --checkpoint "$L/t143_xau_3_rerun.checkpoint.json" --progress "$L/t143_xau_3_rerun.progress.json"
waitrc t143_nsx_3_rerun
waitrc t143_xau_3_rerun
log "pair complete; launch xau1b"
launch xau1b_rerun --instrument XAUUSD --timeframe 1 --candidate-start 1445 --candidate-end 2370 --batch-size 4 --staged-generations 3 --max-staged-parents 10 --memory-limit-gb 1.2 --no-progress-seconds 1800 --output "$L/t143_xau_1_b_rerun.json" --checkpoint "$L/t143_xau_1_b_rerun.checkpoint.json" --progress "$L/t143_xau_1_b_rerun.progress.json"
waitrc t143_xau_1_b_rerun
log "all reruns complete; merge sources"
cd "$C"
PYTHONPATH=. python scripts/merge_t143_worker_outputs.py --output "$L/t143_full.merged.json" --min-trades 300 --source "$L/t143_full.json" "$L/t143_xau_1_a.json" "$L/t143_nsx_5.json" "$L/t143_xau_5.json" "$L/t143_nsx_3_rerun.json" "$L/t143_xau_3_rerun.json" "$L/t143_xau_1_b_rerun.json" > "$L/t143_merge.log" 2>&1
rc=$?
echo "$rc" > "$L/t143_full.merge.rc"
log "merge exit rc=$rc cgroup_bytes=$(mem)"