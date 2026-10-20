# T-202 first differing EMA200 and HA-cross quantity

- Status: **ok** (read-only bounded Pod run, NSXUSD, ten pre-holdout trading dates).
- Port entries: **26**; bounded and unbounded runtime entries: **1** each; exact matches: **1/26**.
- Safety: broker/order/network/holdout/lake-write/risk-edit/redeploy values are all zero or false.

## EMA200 seed and recomputation

| Side | Series start | Seed bar/index | Seed value |
|---|---|---:|---:|
| Closed port | 2026-06-17T00:00Z | 2026-06-17T09:57Z / 199 | 30125.183525 |
| Bounded runtime | 2026-06-18T04:00Z | index 199 | 30087.069945 |
| Unbounded runtime | 2026-06-18T04:00Z | index 199 | 30087.069945 |

The port uses the SMA of its first 200 closes, then alpha=2/(200+1) recursion. The runtime starts later and therefore seeds from a different first 200-close window. Carrying the port EMA state at the first runtime bar and recursively applying the runtime closes gives a zero delta for every inspected entry (the first three values are exactly 0.0). The original bounded deltas for the first three entries are +0.2621746073, +0.1593712751, and +0.3976940906 points (port minus bounded).

## Ten HA rows

| Timestamp | Side | Trigger | Port HA prev O/C | Runtime HA prev O/C | Port HA O/C | Runtime HA O/C | Port buy/sell | Runtime buy/sell | EMA port/bounded/aligned |
|---|---|---|---|---|---|---|---|---|---|
| 2026-06-19T08:21:00+00:00 | BUY | refresh | 30171.319075170/30200.17500 | 30171.319075208/30200.17500 | 30171.319075170/30200.17500 | 30171.319075208/30200.17500 | 0/0 | 0/0 | 30241.551051216/30241.288876609/30241.551051216 |
| 2026-06-19T11:57:00+00:00 | SELL | refresh | 30300.240692198/30296.42425 | 30300.240692198/30296.42425 | 30300.240692198/30296.42425 | 30300.240692198/30296.42425 | 0/0 | 0/0 | 30272.298502310/30272.139131035/30272.298502310 |
| 2026-06-21T23:30:00+00:00 | SELL | refresh | 30365.093968524/30359.90325 | 30365.093968531/30359.90325 | 30365.093968524/30359.90325 | 30365.093968531/30359.90325 | 0/0 | 0/0 | 30312.022663036/30311.624968945/30312.022663036 |
| 2026-06-22T05:30:00+00:00 | SELL | refresh | 30277.778290770/30266.08125 | 30277.778290771/30266.08125 | 30277.778290770/30266.08125 | 30277.778290771/30266.08125 | 0/0 | 0/0 | 30272.167073909/30272.928143142/30272.167073909 |
| 2026-06-22T15:57:00+00:00 | SELL | refresh | 30470.234795186/30558.64900 | 30470.234795173/30558.64900 | 30470.234795186/30558.64900 | 30470.234795173/30558.64900 | 0/0 | 0/0 | 30428.367246203/30427.885049782/30428.367246203 |
| 2026-06-22T23:21:00+00:00 | BUY | refresh | 30284.370099962/30298.04000 | 30284.370099962/30298.04000 | 30284.370099962/30298.04000 | 30284.370099962/30298.04000 | 0/0 | 0/0 | 30337.343404807/30337.754115351/30337.343404807 |
| 2026-06-23T00:57:00+00:00 | SELL | refresh | 30303.332399991/30261.57400 | 30303.332399988/30261.57400 | 30303.332399991/30261.57400 | 30303.332399988/30261.57400 | 0/0 | 0/0 | 30318.047087296/30318.288056663/30318.047087296 |
| 2026-06-23T02:15:00+00:00 | SELL | refresh | 30282.453199995/30227.91925 | 30282.453199998/30227.91925 | 30282.453199995/30227.91925 | 30282.453199998/30227.91925 | 0/0 | 0/0 | 30303.602202967/30303.863651018/30303.602202967 |
| 2026-06-24T00:15:00+00:00 | SELL | refresh | 29412.012334409/29409.85150 | 29412.012334426/29409.85150 | 29412.012334409/29409.85150 | 29412.012334426/29409.85150 | 0/0 | 0/0 | 29485.634679449/29485.639535333/29485.634679449 |
| 2026-06-24T04:51:00+00:00 | SELL | refresh | 29481.087244825/29413.57975 | 29481.087244832/29413.57975 | 29481.087244825/29413.57975 | 29481.087244832/29413.57975 | 0/0 | 0/0 | 29468.477243112/29469.276354348/29468.477243112 |

All ten rows have the same HA values to displayed precision and both sides have false buy/sell flags. The first differing quantity is therefore the **entry trigger**, not a 54m HA value: each inspected port entry is a `refresh` entry.

## Why the previous HA-cross label was misleading

In `scripts/lab_t162_pine_port.py:446-451`, the closed port appends a direction after `last_exit` once `i - last_exit >= REFRESH_BARS` and `_smart_ok` passes, even when neither raw HA cross flag is true. In `src/trader/experiments/tai_wiring.py:439-453`, the runtime assigns a direction only from `features["buy"][index]` or `features["sell"][index]`. The runtime has no equivalent post-exit refresh state. This explains the 25 missing runtime directions without claiming a fabricated HA quantity difference.

## Minimal fix

Carry the closed-port EMA state into the runtime feed and add an explicit post-exit `last_exit`/refresh trigger that reuses `_smart_ok` after `REFRESH_BARS`; keep the position state in the signal runner. T-202 is diagnostic only and does not edit production `tai_wiring.py`. Re-run T-199 after that narrow fix; its 1/26 result remains blocked and is not re-judged here.

## Safety and provenance

All evidence came from the owner-provided Pod terminal URL above. No broker calls, orders, network calls from the script, sealed holdout reads, lake writes, risk/gate edits, or redeploy occurred. The compact committed artifact preserves the ten rows and the Pod seed metadata from `/workspace/t202_run3/summary.json`.
