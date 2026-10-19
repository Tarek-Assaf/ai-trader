# T-180 historical Tai harness dry check

**Verdict:** Harness ready yes; cells run: 8/8; holdout guard tested yes; longer history NOT run.

Owner window: `2026-10-06T04:00:00+00:00` through `2026-10-17T04:00:00+00:00` (end exclusive; New York midnight dates).

Label: `port of the stored indicator, loosely matched to the owner (3/31 within 3 bars, 15/31 within 30 bars)`

| Instrument | Timeframe | Source rows/bars | Signals | Trades | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| NSXUSD | 1m | 12414 | 126 | 39 | ok |
| NSXUSD | 3m | 4070 | 31 | 31 | ok |
| NSXUSD | 5m | 2407 | 20 | 0 | ok |
| NSXUSD | 60m | 148 | 0 | 0 | ok |
| XAUUSD | 1m | 10215 | 71 | 0 | ok |
| XAUUSD | 3m | 3216 | 6 | 0 | ok |
| XAUUSD | 5m | 1830 | 2 | 0 | ok |
| XAUUSD | 60m | 49 | 0 | 0 | ok |

The harness replays the unchanged T-162 port with seven days of warm-up, uses the configured pessimistic spread/slippage model, and emits entry/exit/side/R trade rows and per-year summaries. XAUUSD is reported plainly when the lake has no rows.

## Safety

{"broker_calls": 0, "lake_writes": 0, "orders": 0, "production_state_writes": 0, "sealed_holdout_read": false, "tuning": false}

Longer history was not run; this is a dry check of the owner window only.
