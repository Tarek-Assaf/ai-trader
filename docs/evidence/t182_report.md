Best anchor session_1800_ny, mode closed: 11/31 within 3 bars, 17/31 within 30 bars; gate 25/31 met no; VWAP anchor correct yes.

# T-182 session-anchored 54-minute Heikin Ashi diagnostic

The three pre-declared anchors were replayed in closed and forming modes. The 25/31 gate was not relaxed and no additional offsets were tested.

| Anchor | Mode | Exact | ±1 | ±2 | ±3 | ±30 | Extra within 30 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| epoch | closed | 2 | 5 | 7 | 8 | 15 | 8 |
| epoch | forming | 0 | 0 | 0 | 0 | 1 | 0 |
| session_1800_ny | closed | 3 | 6 | 9 | 11 | 17 | 13 |
| session_1800_ny | forming | 3 | 7 | 9 | 11 | 17 | 12 |
| rth_0930_ny | closed | 0 | 0 | 0 | 0 | 0 | 0 |
| rth_0930_ny | forming | 0 | 0 | 0 | 0 | 0 | 0 |

## VWAP and safety

The Pine `ta.vwap(hlc3)` session resets at the 18:00 New York CME session open. The port now uses `session_1800_ny` for replay and reports the anchor as correct.

{
  "broker_calls": 0,
  "lake_writes": 0,
  "orders": 0,
  "production_state_writes": 0,
  "sealed_holdout_read": false,
  "tuning": false
}

## Per-entry differences

### epoch, closed

- `2026-10-06T20:03:00+00:00` short: nearest `2026-10-08T03:06:00+00:00` (621.0 bars); first difference `signal_or_refresh`.
- `2026-10-06T22:45:00+00:00` short: nearest `2026-10-08T03:06:00+00:00` (567.0 bars); first difference `signal_or_refresh`.
- `2026-10-07T01:18:00+00:00` short: nearest `2026-10-08T03:06:00+00:00` (516.0 bars); first difference `signal_or_refresh`.
- `2026-10-07T22:21:00+00:00` long: nearest `2026-10-08T02:06:00+00:00` (75.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T01:51:00+00:00` long: nearest `2026-10-08T02:06:00+00:00` (5.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T14:45:00+00:00` long: nearest `2026-10-08T02:06:00+00:00` (-253.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T15:09:00+00:00` short: nearest `2026-10-08T23:15:00+00:00` (162.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T23:03:00+00:00` short: nearest `2026-10-08T23:15:00+00:00` (4.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T14:30:00+00:00` short: nearest `2026-10-09T00:27:00+00:00` (-281.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T16:30:00+00:00` long: nearest `2026-10-09T20:51:00+00:00` (87.0 bars); first difference `signal_or_refresh`.
- `2026-10-11T22:18:00+00:00` long: nearest `2026-10-12T00:03:00+00:00` (35.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T11:00:00+00:00` short: nearest `2026-10-13T15:03:00+00:00` (81.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T18:21:00+00:00` long: nearest `2026-10-13T22:57:00+00:00` (92.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T10:18:00+00:00` short: nearest `2026-10-14T09:51:00+00:00` (-9.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T12:18:00+00:00` long: nearest `2026-10-14T00:57:00+00:00` (-227.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T22:24:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (12.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T04:30:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (-110.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T05:27:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (-129.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T13:42:00+00:00` short: nearest `2026-10-15T14:21:00+00:00` (13.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T10:12:00+00:00` short: nearest `2026-10-16T10:24:00+00:00` (4.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T11:03:00+00:00` short: nearest `2026-10-16T10:24:00+00:00` (-13.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T13:18:00+00:00` short: nearest `2026-10-16T11:57:00+00:00` (-27.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T14:42:00+00:00` short: nearest `2026-10-16T11:57:00+00:00` (-55.0 bars); first difference `signal_or_refresh`.

### epoch, forming

- `2026-10-06T20:03:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (3625.0 bars); first difference `signal_or_refresh`.
- `2026-10-06T22:45:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (3571.0 bars); first difference `signal_or_refresh`.
- `2026-10-07T01:18:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (3520.0 bars); first difference `signal_or_refresh`.
- `2026-10-07T22:21:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T01:51:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T14:45:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T15:09:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (2763.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T23:03:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (2605.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T14:30:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (2296.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T16:30:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-11T22:18:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-12T00:27:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-12T02:30:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T11:00:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (446.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T14:57:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (367.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T17:36:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (314.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T18:21:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T05:24:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (78.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T08:54:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (8.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T10:18:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-20.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T12:18:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T22:24:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-262.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T04:30:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-384.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T05:27:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-403.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T12:45:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-549.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T13:42:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-568.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T00:57:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T10:12:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-978.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T11:03:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-995.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T13:18:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-1040.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T14:42:00+00:00` short: nearest `2026-10-14T09:18:00+00:00` (-1068.0 bars); first difference `signal_or_refresh`.

### session_1800_ny, closed

- `2026-10-06T20:03:00+00:00` short: nearest `2026-10-07T01:24:00+00:00` (107.0 bars); first difference `signal_or_refresh`.
- `2026-10-06T22:45:00+00:00` short: nearest `2026-10-07T01:24:00+00:00` (53.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T14:45:00+00:00` long: nearest `2026-10-08T02:45:00+00:00` (-240.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T15:09:00+00:00` short: nearest `2026-10-08T23:15:00+00:00` (162.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T23:03:00+00:00` short: nearest `2026-10-08T23:15:00+00:00` (4.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T14:30:00+00:00` short: nearest `2026-10-09T00:27:00+00:00` (-281.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T16:30:00+00:00` long: nearest `2026-10-09T20:51:00+00:00` (87.0 bars); first difference `signal_or_refresh`.
- `2026-10-11T22:18:00+00:00` long: nearest `2026-10-12T00:03:00+00:00` (35.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T11:00:00+00:00` short: nearest `2026-10-13T15:03:00+00:00` (81.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T18:21:00+00:00` long: nearest `2026-10-13T22:57:00+00:00` (92.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T10:18:00+00:00` short: nearest `2026-10-14T09:51:00+00:00` (-9.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T12:18:00+00:00` long: nearest `2026-10-14T00:57:00+00:00` (-227.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T22:24:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (12.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T04:30:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (-110.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T05:27:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (-129.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T13:42:00+00:00` short: nearest `2026-10-15T14:21:00+00:00` (13.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T10:12:00+00:00` short: nearest `2026-10-16T10:24:00+00:00` (4.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T11:03:00+00:00` short: nearest `2026-10-16T10:24:00+00:00` (-13.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T13:18:00+00:00` short: nearest `2026-10-16T11:57:00+00:00` (-27.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T14:42:00+00:00` short: nearest `2026-10-16T11:57:00+00:00` (-55.0 bars); first difference `signal_or_refresh`.

### session_1800_ny, forming

- `2026-10-06T20:03:00+00:00` short: nearest `2026-10-07T01:21:00+00:00` (106.0 bars); first difference `signal_or_refresh`.
- `2026-10-06T22:45:00+00:00` short: nearest `2026-10-07T01:21:00+00:00` (52.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T14:45:00+00:00` long: nearest `2026-10-08T02:45:00+00:00` (-240.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T15:09:00+00:00` short: nearest `2026-10-08T23:15:00+00:00` (162.0 bars); first difference `signal_or_refresh`.
- `2026-10-08T23:03:00+00:00` short: nearest `2026-10-08T23:15:00+00:00` (4.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T14:30:00+00:00` short: nearest `2026-10-09T00:27:00+00:00` (-281.0 bars); first difference `signal_or_refresh`.
- `2026-10-09T16:30:00+00:00` long: nearest `2026-10-09T20:51:00+00:00` (87.0 bars); first difference `signal_or_refresh`.
- `2026-10-11T22:18:00+00:00` long: nearest `2026-10-12T00:03:00+00:00` (35.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T11:00:00+00:00` short: nearest `2026-10-13T15:03:00+00:00` (81.0 bars); first difference `signal_or_refresh`.
- `2026-10-13T18:21:00+00:00` long: nearest `2026-10-13T22:57:00+00:00` (92.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T10:18:00+00:00` short: nearest `2026-10-14T09:51:00+00:00` (-9.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T12:18:00+00:00` long: nearest `2026-10-14T00:57:00+00:00` (-227.0 bars); first difference `signal_or_refresh`.
- `2026-10-14T22:24:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (12.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T04:30:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (-110.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T05:27:00+00:00` short: nearest `2026-10-14T23:00:00+00:00` (-129.0 bars); first difference `signal_or_refresh`.
- `2026-10-15T13:42:00+00:00` short: nearest `2026-10-15T14:21:00+00:00` (13.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T10:12:00+00:00` short: nearest `2026-10-16T10:24:00+00:00` (4.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T11:03:00+00:00` short: nearest `2026-10-16T10:24:00+00:00` (-13.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T13:18:00+00:00` short: nearest `2026-10-16T11:57:00+00:00` (-27.0 bars); first difference `signal_or_refresh`.
- `2026-10-16T14:42:00+00:00` short: nearest `2026-10-16T11:57:00+00:00` (-55.0 bars); first difference `signal_or_refresh`.

### rth_0930_ny, closed

- `2026-10-06T20:03:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-06T22:45:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-07T01:18:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-07T22:21:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T01:51:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T14:45:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T15:09:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T23:03:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-09T14:30:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-09T16:30:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-11T22:18:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-12T00:27:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-12T02:30:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T11:00:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T14:57:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T17:36:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T18:21:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T05:24:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T08:54:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T10:18:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T12:18:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T22:24:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T04:30:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T05:27:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T12:45:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T13:42:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T00:57:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T10:12:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T11:03:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T13:18:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T14:42:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.

### rth_0930_ny, forming

- `2026-10-06T20:03:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-06T22:45:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-07T01:18:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-07T22:21:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T01:51:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T14:45:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T15:09:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-08T23:03:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-09T14:30:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-09T16:30:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-11T22:18:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-12T00:27:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-12T02:30:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T11:00:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T14:57:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T17:36:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-13T18:21:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T05:24:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T08:54:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T10:18:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T12:18:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-14T22:24:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T04:30:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T05:27:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T12:45:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-15T13:42:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T00:57:00+00:00` long: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T10:12:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T11:03:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T13:18:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
- `2026-10-16T14:42:00+00:00` short: nearest `None` (None bars); first difference `missing_signal`.
