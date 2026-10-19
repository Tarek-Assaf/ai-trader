# T-195 runtime miss diagnostic

Measured on the owner Pod with `scripts/lab_t195_runtime_miss.py`, `NSXUSD`, holdout boundary `2026-07-01T00:00:00Z`, and a bounded 14-day pre-holdout loader. The run read 13,154 minute rows and built 3,859 closed 3-minute bars.

- Port entries: **26**
- Bounded runtime entries: **1**
- Unbounded runtime entries: **1**
- Matched entries: **1**
- Misses: **25** (23 missing runtime direction, 2 bar/stop differences)
- Cause counts: `runtime_gate_ha_cross=25`; no history-window, warmup, position-lock, or unexplained cases.

The bounded 600-bar provider did not recover any additional entry. The two `bar_or_stop_difference` rows have a nearest runtime candidate at 2026-06-24T16:00:00Z; the other 23 misses fail first at the runtime `ha_cross` gate.

| Timestamp (UTC) | Side | Cause | Port entry / stop | Runtime candidate entry / stop | First failing bounded gate | History bars |
|---|---|---|---|---|---|---:|
| 2026-06-19T08:21:00+00:00 | BUY | bar_or_stop_difference | 30278.921 / 30249.203843524592 | 29556.867 / 29462.963656558964332 | — | 600 |
| 2026-06-19T11:57:00+00:00 | SELL | missing_runtime_direction | 30262.32 / 30281.5112111378 | — | ha_cross | 600 |
| 2026-06-21T23:30:00+00:00 | SELL | missing_runtime_direction | 30219.055 / 30256.351975421672 | — | ha_cross | 600 |
| 2026-06-22T05:30:00+00:00 | SELL | missing_runtime_direction | 30240.708 / 30267.824759658448 | — | ha_cross | 600 |
| 2026-06-22T15:57:00+00:00 | SELL | missing_runtime_direction | 30320.586 / 30377.42052887693 | — | ha_cross | 600 |
| 2026-06-22T23:21:00+00:00 | BUY | bar_or_stop_difference | 30360.649 / 30334.88497838238 | 29556.867 / 29462.963656558964332 | — | 600 |
| 2026-06-23T00:57:00+00:00 | SELL | missing_runtime_direction | 30266.175 / 30271.209335323256 | — | ha_cross | 600 |
| 2026-06-23T02:15:00+00:00 | SELL | missing_runtime_direction | 30262.566 / 30311.61962030889 | — | ha_cross | 600 |
| 2026-06-24T00:15:00+00:00 | SELL | missing_runtime_direction | 29353.703 / 29381.041960623064 | — | ha_cross | 600 |
| 2026-06-24T04:51:00+00:00 | SELL | missing_runtime_direction | 29414.199 / 29469.779715014396 | — | ha_cross | 600 |
| 2026-06-24T13:57:00+00:00 | SELL | missing_runtime_direction | 29454.681 / 29494.675408277486 | — | ha_cross | 600 |
| 2026-06-24T15:09:00+00:00 | SELL | missing_runtime_direction | 29369.498 / 29500.044786382216 | — | ha_cross | 600 |
| 2026-06-24T16:00:00+00:00 | BUY | matched | 29556.867 / 29462.963656558964332 | 29556.867 / 29462.963656558964332 | — | 600 |
| 2026-06-24T23:42:00+00:00 | BUY | missing_runtime_direction | 29773.364 / 29738.88289788457 | — | ha_cross | 600 |
| 2026-06-26T01:39:00+00:00 | SELL | missing_runtime_direction | 29375.137 / 29435.314793981925 | — | ha_cross | 600 |
| 2026-06-26T08:18:00+00:00 | BUY | missing_runtime_direction | 29193.186 / 29130.558000782872 | — | ha_cross | 600 |
| 2026-06-26T15:15:00+00:00 | BUY | missing_runtime_direction | 29238.22 / 29122.427219577297 | — | ha_cross | 600 |
| 2026-06-26T19:39:00+00:00 | SELL | missing_runtime_direction | 29172.407 / 29228.112319150947 | — | ha_cross | 600 |
| 2026-06-28T23:18:00+00:00 | BUY | missing_runtime_direction | 29212.014 / 29144.57730857682 | — | ha_cross | 600 |
| 2026-06-29T01:57:00+00:00 | SELL | missing_runtime_direction | 29203.164 / 29215.309529042534 | — | ha_cross | 600 |
| 2026-06-29T03:21:00+00:00 | SELL | missing_runtime_direction | 29191.242 / 29205.903371881883 | — | ha_cross | 600 |
| 2026-06-29T05:03:00+00:00 | BUY | missing_runtime_direction | 29269.566 / 29237.358834888 | — | ha_cross | 600 |
| 2026-06-29T15:45:00+00:00 | SELL | missing_runtime_direction | 29232.271 / 29384.63117276517 | — | ha_cross | 600 |
| 2026-06-30T02:36:00+00:00 | BUY | missing_runtime_direction | 29746.153 / 29688.956252852488 | — | ha_cross | 600 |
| 2026-06-30T09:51:00+00:00 | SELL | missing_runtime_direction | 29763.442 / 29799.556551589812 | — | ha_cross | 600 |
| 2026-06-30T14:27:00+00:00 | SELL | missing_runtime_direction | 29762.821 / 29770.751522703096 | — | ha_cross | 600 |

The measured result leaves the 25 misses as Pine/runtime parity work, primarily runtime `ha_cross` gating, not a history-window issue. No code fix is applied by T-195. The run made zero broker or order calls, used no network or sealed holdout data, wrote no lake data, changed no risk gates, and did not redeploy anything.
