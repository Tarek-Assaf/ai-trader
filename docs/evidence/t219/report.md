# T-219 live-day parity evidence

## Scope and safety

This was a read-only Pod check. No broker session was opened, no order or dry-run
was submitted, and no recorder, risk, gate, lease, or configuration state was
changed. The production replay was bounded and terminated after it produced no
output; the recorder remained healthy afterward.

Pod checks used:

```text
python /app/scripts/lab_t149_recorded_tai.py --lake /workspace/data/lake \
  --output /tmp/t219-port/result.json --table /tmp/t219-port/report.md \
  --trades /tmp/t219-port/trades.json
python /app/scripts/lab_t187_runtime_replay.py --lake /workspace/data/lake \
  --holdout-start 2026-10-20T00:00:00+00:00 --days 2 \
  --output-dir /tmp/t219-runtime
```

The T-149 run completed with 211 historical entries across NAS100/XAUUSD and
reported `orders=0`, `broker_calls=0`, `gate1_evaluated=false`, and
`sealed_holdout_read=false`. Its selected observed range ended at 2026-10-16;
filtering its emitted trade file for 2026-10-20/21 returned zero entries. This
means that output cannot serve as a same-bars comparison for the requested live
days.

The T-187 production-path command was stopped after approximately two minutes
of CPU time without writing an output file. No network or broker call was made.
The subsequent health check was still healthy, so the replay process did not
disturb the recorder.

## Corrected 2026-10-21 21:00-23:30Z replay

The target-day replay was rerun against the current NAS100 lake with the
explicit `--epic NAS100` selector (the Pod stores the rows under
`NAS100/1m_capital`, while the recorded row label is `US100`). Both the closed
session-anchored port and the production `TaiSignalProvider` produced **zero
entries** for 2026-10-21, including the 22:30Z bar. The dry-run runner made
zero broker/order calls. The replay therefore agrees with the Pod runner, but
it does not reproduce the owner's TradingView signal.

The 22:30Z closed bar built from the Pod lake was:

```text
open 29661.5, high 29679.6, low 29656.7, close 29674.2
```

The owner's chart evidence is `NQ1!`, entry 29,858.25. These are different
instruments/data feeds, not two views of the same bar. The price difference is
~184 points, so a signal seen on NQ1! cannot be used as a parity oracle for the
Pod's Capital.com US100 runner.

Across 21:00-23:30Z the lake had 145 minute stamps; exactly one minute
(`22:18Z`) was incomplete (one of bid/ask was absent). The target 22:30Z bar
was present and closed. The log shows the runner built it at `22:33:33.941Z`
with `warmup_bars=5173`; the 22:30Z bar itself was not late by a feed stall,
but it was delivered after the three-minute boundary. The only outcome summary
in the requested window was at 22:00:27Z: `evaluated=18`, `no_signal=18`,
`stale_or_incomplete=0`, `market_closed_or_break=0`, `entry=0`, `exit=0`.
There were no `last_no_signal_gate` records. Reconnect/backfill activity began
after 22:35Z, after the target bar.

## Why the displayed non-trade occurred

The evidence does not show a strategy mismatch or a missing stop calculation.
It shows an instrument mismatch: the chart was NQ1! while the Pod was running
US100. The safe fix is to compare like with like: either use a TradingView
chart for the Capital.com US100 feed, or provide a licensed NQ1! feed and map
the runner to that broker instrument. Renaming `US100` to `NQ1!` would be unsafe
because the Capital.com adapter does not trade the futures contract.

The indicator's stop is dynamic, not a fixed 50-point constant. For a SELL it
uses the highest high of the previous five closed 3M bars plus `0.20 * ATR`;
the candidate is rejected when risk exceeds `2.0 * ATR`. A chart distance near
49.7 points is therefore an output of that bar's ATR/swing state, not a rule
that should be hard-coded as 50.

## Current runner outcome

At the final health sample (2026-10-21 21:13:33Z):

* runtime state: `running`; database and Redis: `reachable`;
* recorder: `healthy`; `ping_reply_ok=true`; `feed_stalled=false`;
* recorder reconnect count: 7, last reason `ConnectionClosedError`;
* Tai demo: US100 `evaluated=2`, `no_signal=2`, `entry=0`, `exit=0`, last gate
  `ha_cross`;
* mode was `paper`, demo broker, with live trading disabled.

No current-day Tai entry or exit was recorded, and no trade occurred in the
checked interval.

## Lake coverage and bar-builder check

The live Capital lake files reached 2026-10-21 21:13Z for NAS100 and 20:58Z for
XAUUSD. In the requested 2026-10-21 14:00-20:00Z half-open window, the lake
contained:

| Epic | Rows | Expected | Missing minutes |
|---|---:|---:|---|
| NAS100 | 712 | 720 | 16:41, 19:04 |
| XAUUSD | 693 | 720 | 14:18, 14:31, 15:01, 15:13, 16:09, 16:41, 16:47, 18:31, 18:52, 19:13, 19:21 |

The live log had no `bar commit` records whose `boundary_minute` fell in this
14:00-20:00Z window, so a commit-level live-versus-lake value comparison is not
available for that interval. The lake therefore demonstrates coverage gaps,
but it does not prove whether each gap was a dropped feed update or a builder
write gap.

## Verdict and next decisive experiment

The available live evidence is consistent with a quiet day: the runner stayed
healthy and Tai produced no signal. A definitive closed-port-versus-production
parity verdict for 2026-10-20/21 is **inconclusive** because the T-149 selection
did not include those dates and the T-187 replay did not finish within the
bounded read-only run.

The decisive safe experiment is to wait until the recorder has finished writing
the target day, copy only the two target-day parquet slices to `/tmp`, and run
both replays against that temporary lake with an explicit `--epic NAS100` and a
small date window. Capture the port/runtime entry lists and the live bar-builder
commit records side by side. This does not open a second Capital.com session or
touch the recorder.


