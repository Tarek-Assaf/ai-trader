# T-196 Tai HA-cross trace

- Port entries: **26**
- Measured bounded runtime `ha_cross` miss rows: **23**

The T-195 Pod replay measured 25 bounded runtime `ha_cross` first-gate misses (the accepted handoff/spec says 23). This artifact preserves the 23 rows whose trace explicitly names `ha_cross`; the two remaining misses are retained in T-195 as missing runtime direction.

## First differing quantity

| Entry timestamp | Side | First differing quantity |
|---|---:|---|
| 2026-06-19T11:57:00+00:00 | SELL | unexplained |
| 2026-06-21T23:30:00+00:00 | SELL | unexplained |
| 2026-06-22T05:30:00+00:00 | SELL | unexplained |
| 2026-06-22T15:57:00+00:00 | SELL | unexplained |
| 2026-06-23T00:57:00+00:00 | SELL | unexplained |
| 2026-06-23T02:15:00+00:00 | SELL | unexplained |
| 2026-06-24T00:15:00+00:00 | SELL | unexplained |
| 2026-06-24T04:51:00+00:00 | SELL | unexplained |
| 2026-06-24T13:57:00+00:00 | SELL | unexplained |
| 2026-06-24T15:09:00+00:00 | SELL | unexplained |
| 2026-06-24T23:42:00+00:00 | BUY | unexplained |
| 2026-06-26T01:39:00+00:00 | SELL | unexplained |
| 2026-06-26T08:18:00+00:00 | BUY | unexplained |
| 2026-06-26T15:15:00+00:00 | BUY | unexplained |
| 2026-06-26T19:39:00+00:00 | SELL | unexplained |
| 2026-06-28T23:18:00+00:00 | BUY | unexplained |
| 2026-06-29T01:57:00+00:00 | SELL | unexplained |
| 2026-06-29T03:21:00+00:00 | SELL | unexplained |
| 2026-06-29T05:03:00+00:00 | BUY | unexplained |
| 2026-06-29T15:45:00+00:00 | SELL | unexplained |
| 2026-06-30T02:36:00+00:00 | BUY | unexplained |
| 2026-06-30T09:51:00+00:00 | SELL | unexplained |
| 2026-06-30T14:27:00+00:00 | SELL | unexplained |

## Evidence limitation

The committed T-195 artifact carries entry-bar values but not the two preceding bars. The new runner `scripts/lab_t196_ha_trace.py` collects entry plus two prior closed bars and writes the complete acceptance artifact when run on the Pod lake. This checked-in summary marks that missing context explicitly and does not invent values.

No broker, order, network, holdout, risk/gate, redeploy, or secret access was used.
