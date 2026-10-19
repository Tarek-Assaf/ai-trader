# T-193 recorder reconnect and missing-minute measurement

## Scope and limitations

This is a read-only measurement on the connected Pod. No broker write, order, redeploy,
risk edit, gate edit, credential use, or sealed-holdout read was performed. The requested
script, `/app/scripts/lab_t189_minute_gaps.py`, was not present in the reachable Pod
checkout, and no market-details JSON was found under `/workspace`. The counts below are
therefore an observed-side analysis of the recorder parquet files. They count minutes with
at least one bid or ask side and cannot apply the requested market-hours filter or reproduce
the absent script exactly.

The reachable terminal was the user-provided `gt00...` endpoint. The fixed heartbeat endpoint
ending in `46tq...` returned HTTP 404. The two lake files inspected were:

- `/workspace/data/lake/NAS100/1m_capital/2026/10.parquet` (26,602 rows,
  2026-10-05 08:28 through 2026-10-19 23:51 UTC)
- `/workspace/data/lake/XAUUSD/1m_capital/2026/10.parquet` (22,040 rows,
  2026-10-05 08:28 through 2026-10-19 23:51 UTC)

## Observed minute coverage

`minutes_with_any_side` is the number of distinct UTC minutes containing either side.
`paired_bid_ask` contains both sides; `bid_only` and `ask_only` contain one side. The
`missing_side_minutes` column is `bid_only + ask_only`. Closed or unknown minutes are not
in the denominator because market-details JSON was unavailable.

| instrument | UTC date | minutes_with_any_side | paired_bid_ask | bid_only | ask_only | missing_side_minutes |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| NAS100 | 2026-10-11 | 86 | 85 | 1 | 0 | 1 |
| NAS100 | 2026-10-12 | 1,425 | 1,414 | 11 | 0 | 11 |
| NAS100 | 2026-10-13 | 1,424 | 1,416 | 8 | 0 | 8 |
| NAS100 | 2026-10-14 | 1,427 | 1,417 | 10 | 0 | 10 |
| NAS100 | 2026-10-15 | 1,424 | 1,418 | 6 | 0 | 6 |
| NAS100 | 2026-10-16 | 1,255 | 1,243 | 12 | 0 | 12 |
| NAS100 | 2026-10-19 | 924 | 914 | 10 | 0 | 10 |
| XAUUSD | 2026-10-09 | 1,242 | 1,240 | 2 | 0 | 2 |
| XAUUSD | 2026-10-12 | 832 | 830 | 2 | 0 | 2 |
| XAUUSD | 2026-10-13 | 1,361 | 1,357 | 4 | 0 | 4 |
| XAUUSD | 2026-10-14 | 1,348 | 1,345 | 3 | 0 | 3 |
| XAUUSD | 2026-10-15 | 1,330 | 1,315 | 15 | 0 | 15 |
| XAUUSD | 2026-10-16 | 1,204 | 1,186 | 18 | 0 | 18 |
| XAUUSD | 2026-10-19 | 833 | 824 | 9 | 0 | 9 |

On 2026-10-19 UTC, the NAS100 missing-side minutes were 08:05, 09:01, 11:04, 13:14,
13:32, 13:53, 14:17, 17:46, 18:20, and 19:03. XAUUSD missing-side minutes were 09:37,
10:24, 10:43, 11:44, 14:08, 14:15, 15:31, 16:51, and 17:30.

## Recorder health snapshot

The redacted `/health` response reported `status=healthy`, `mode=paper`,
`broker_env=demo`, `live_enabled=false`, `db=reachable`, and `redis=reachable`. The
recorder reported `configured=true`, `expected_open=true`, `market_status=TRADEABLE,TRADEABLE`
from `market_status_source=broker,broker`, `ping_reply_ok=true`, and `stale_epics=[]`.
At the snapshot it had `ticks_received=2990`, `last_tick_at` and `last_update_at`
`2026-10-19T23:53:03.375385+00:00`, `last_closed_bar_at=2026-10-19T23:52:00+00:00`,
and `last_liveness_at=2026-10-19T23:53:02.882185+00:00`. It reported
`reconnect_count=174`, `last_reconnect_reason=ConnectionClosedError`,
`rejected_candles=19`, and `last_reject_reason=ask_close_below_bid_close`.

## Reconnect correlation

The scheduler log recorded generic `Capital.com WebSocket disconnected (ConnectionClosedError)`
warnings at 23:17, 23:29, 23:32, 23:36, 23:38, 23:40, 23:44, 23:47, and 23:50 UTC on
2026-10-19. Each was followed about one second later by an HTTP `GET .../api/v1/ping`
with status 200. The API error log had only historical account-mapping-unverified errors
from October 6–8. No current close code, close reason, seconds since last ping reply, or
connection-age field was logged, and the startup health JSONL tail contained only old
`status=starting` digests rather than a current seven-day reconnect digest.

The repeated generic close followed by a successful ping is most consistent with a remote
WebSocket closure or session-liveness interruption. This evidence does not support the
"two Capital.com sessions on one key" theory: there was no overlapping-session identity,
authentication error, or account collision in the available logs. A useful follow-up, if
future evidence supports it, would be one session owner or a bounded login throttle. This
is a proposal only and was not implemented.

