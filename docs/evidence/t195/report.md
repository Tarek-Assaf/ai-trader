# T-195 runtime miss diagnostic

Measured on the owner Pod with `scripts/lab_t195_runtime_miss.py`, `NSXUSD`, holdout boundary `2026-07-01T00:00:00Z`, and a bounded 14-day pre-holdout loader. The run read 13,154 minute rows and built 3,859 closed 3-minute bars.

- Port entries: **25**
- Bounded runtime entries: **2**
- Unbounded runtime entries: **2**
- Matched entries: **2**
- Misses: **23** (22 missing runtime direction, 1 bar/stop difference)
- Cause counts: `pine_difference=23`, `bar_or_stop_difference=2`; no history-window, warmup, position-lock, or unexplained cases.

The bounded 600-bar provider did not recover any additional entry. The 23 rows below fail first at the runtime `ha_cross` gate; the two rows labelled `bar_or_stop_difference` are emitted by the bounded trace but differ from the port entry/stop comparison. The raw script summary records HA54m, EMA200, ADX, +DI, -DI, VWAP, session anchor, and gate booleans for every row.

| Timestamp (UTC) | Side | Cause | First failing bounded gate | History bars |
|---|---|---|---|---:|
| 2026-06-22T16:00:00+00:00 | SELL | bar_or_stop_difference | — | 600 |
| 2026-06-22T16:39:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-22T16:42:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-22T16:45:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-22T23:21:00+00:00 | BUY | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-23T00:57:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-23T02:15:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-24T00:15:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-24T04:51:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-24T13:57:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-24T15:09:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-24T16:00:00+00:00 | BUY | bar_or_stop_difference | — | 600 |
| 2026-06-24T23:42:00+00:00 | BUY | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-26T01:39:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-26T08:18:00+00:00 | BUY | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-26T15:15:00+00:00 | BUY | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-26T19:39:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-28T23:18:00+00:00 | BUY | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-29T01:57:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-29T03:21:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-29T05:03:00+00:00 | BUY | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-29T15:45:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-30T02:36:00+00:00 | BUY | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-30T09:51:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |
| 2026-06-30T14:27:00+00:00 | SELL | pine_difference_or_unexplained | ha_cross | 600 |

The measured result leaves the 23 `ha_cross` misses as Pine/runtime parity work, not a history-window issue. No code fix is applied by T-195. The narrowest candidate for a later task is aligning runtime history and indicator seeding with the full-series port after review. The run made zero broker or order calls, used no network or sealed holdout data, wrote no lake data, changed no risk gates, and did not redeploy anything.
