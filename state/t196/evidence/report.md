# T-196 Tai HA-cross trace

- Port entries traced: **26**
- Rows whose bounded runtime first gate is `ha_cross`: **23**

The bounded replay leaves 23 accepted `ha_cross` misses after excluding the one exact match and two bar/stop rows from the 26 port entries.

## Cause observed

The first visible differences are: ema200=22, ha_open_54m=1.
Three rows show a different EMA200 value at the transferred precision. The other 20 rows show no visible difference in HA open/close, colour, previous colour, cross flag, EMA200, ADX, DI or VWAP across the entry and two prior bars, so the exact cause of those gate rejections remains unexplained by this trace.

## Compact transferred rows

Each row was copied from the Pod `T196_ROW` output. `0`, `1`, and `2` are the two prior bars and entry bar; `P` is the Pine port and `R` is runtime. Values use 10 significant digits. The full JSON was also written to `/workspace/state/t196/summary.json` on the Pod.

| Entry timestamp | Side | First visible difference |
|---|---:|---|
| 2026-06-19T11:57:00+00:00 | SELL | ema200 |
| 2026-06-21T23:30:00+00:00 | SELL | ema200 |
| 2026-06-22T05:30:00+00:00 | SELL | ema200 |
| 2026-06-22T15:57:00+00:00 | SELL | ema200 |
| 2026-06-23T00:57:00+00:00 | SELL | ema200 |
| 2026-06-23T02:15:00+00:00 | SELL | ema200 |
| 2026-06-24T00:15:00+00:00 | SELL | ema200 |
| 2026-06-24T04:51:00+00:00 | SELL | ema200 |
| 2026-06-24T13:57:00+00:00 | SELL | ema200 |
| 2026-06-24T15:09:00+00:00 | SELL | ema200 |
| 2026-06-24T23:42:00+00:00 | BUY | ema200 |
| 2026-06-26T01:39:00+00:00 | SELL | ema200 |
| 2026-06-26T08:18:00+00:00 | BUY | ema200 |
| 2026-06-26T15:15:00+00:00 | BUY | ema200 |
| 2026-06-26T19:39:00+00:00 | SELL | ema200 |
| 2026-06-28T23:18:00+00:00 | BUY | ema200 |
| 2026-06-29T01:57:00+00:00 | SELL | ema200 |
| 2026-06-29T03:21:00+00:00 | SELL | ema200 |
| 2026-06-29T05:03:00+00:00 | BUY | ema200 |
| 2026-06-29T15:45:00+00:00 | SELL | ha_open_54m |
| 2026-06-30T02:36:00+00:00 | BUY | ema200 |
| 2026-06-30T09:51:00+00:00 | SELL | ema200 |
| 2026-06-30T14:27:00+00:00 | SELL | ema200 |

## Code comparison and minimal fix

The runtime HA assignment is:

> `ha_open[index], ha_close[index] = previous`

The Pine closed-series assignment is:

> `opens[i], closes[i] = previous_complete`

These quoted lines perform the same previous-group assignment, so this run does not identify a real differing code line. The exact unknown is the runtime state or gate input that rejects 20 rows while all transferred values match.
The minimal fix is to add a bounded runtime trace of the gate inputs and HA state at the rejection point, then align only the state proven to differ; no production or gate code is changed by T-196.
