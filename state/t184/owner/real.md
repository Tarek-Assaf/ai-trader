# T-181 historical Tai harness window-only dry check

**Verdict:** Window-only counts yes; cells run 8/8; zero-trade cells explained 4; longer history NOT run.

Owner window: `2026-10-06T04:00:00+00:00` through `2026-10-17T04:00:00+00:00` (end exclusive; New York midnight dates).

Label: `port of the stored indicator, loosely matched to the owner (3/31 within 3 bars, 15/31 within 30 bars)`

| Instrument | Timeframe | Warmup bars | Window bars | Signals | Trades | Status |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| NSXUSD | 1m | 963 | 11451 | 118 | 47 | ok |
| NSXUSD | 3m | 311 | 3759 | 29 | 22 | ok |
| NSXUSD | 5m | 181 | 2226 | 18 | 0 | ok |
| NSXUSD | 60m | 9 | 139 | 0 | 0 | ok |
| XAUUSD | 1m | 515 | 9700 | 71 | 0 | ok |
| XAUUSD | 3m | 157 | 3059 | 6 | 0 | ok |
| XAUUSD | 5m | 87 | 1743 | 2 | 0 | ok |
| XAUUSD | 60m | 0 | 49 | 0 | 0 | ok |

The harness replays the unchanged T-162 port with seven days of warm-up, uses the configured pessimistic spread/slippage model, and emits entry/exit/side/R trade rows and per-year summaries. XAUUSD is reported plainly when the lake has no rows.

## Safety

{"broker_calls": 0, "lake_writes": 0, "orders": 0, "production_state_writes": 0, "sealed_holdout_read": false, "tuning": false}

Zero-trade cells report `not exposed` because the unchanged T-162 port does not expose entry-filter counters.

Longer history was not run; this is a dry check of the owner window only.
