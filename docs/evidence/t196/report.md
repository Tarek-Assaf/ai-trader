# T-196 Tai HA-cross trace

- Port entries traced: **26**
- Accepted `ha_cross` miss rows: **23**
- First visible differences: **22 `ema200`**, **1 `ha_open_54m`**.

The read-only Pod run printed 23 `T196_ROW` records. Each record contains port/runtime HA open, HA close, colour, previous colour, cross flag, EMA200, ADX, +DI, -DI and VWAP for the two prior closed bars and the entry bar. The full JSON was written to `/workspace/state/t196/summary.json`; the compact records were saved to `/workspace/state/t196/compact.txt`.

## First visible difference

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

## Cause

The first visible difference is EMA200 on 22 rows and HA open on one row at the copied 10-significant-digit precision. The 23 rows therefore have a named visible difference; the trace does not prove that the visible indicator drift is the runtime gate cause, and it does not identify any additional HA state difference.

## Code comparison and minimal fix

Runtime HA assignment (`src/trader/experiments/tai_wiring.py:304):

> `ha_open[index], ha_close[index] = previous`

Pine port closed-series assignment (`scripts/lab_t162_pine_port.py:337):

> `opens[i], closes[i] = previous_complete`

These quoted lines perform the same previous-group assignment, so no real differing code line was found in this run. The exact remaining unknown is why the runtime rejects the rows when its EMA200 or HA-open value differs at the copied precision. The minimal fix is to add a bounded runtime trace at the gate rejection point and align only the state proven to differ; T-196 makes no production or gate change.
