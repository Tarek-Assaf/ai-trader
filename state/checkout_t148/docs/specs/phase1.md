# Phase 1 Spec: Data (task C-003, part 1)

Author: Claude. For: Codex. Covers T-010 to T-017. Depends on Phase 0. Read `ARCHITECTURE.md` sections 2 and 4 and `docs/legacy_audit.md`.

## Rules for this phase
- External facts below are **believed correct and must be verified** against live docs and the demo account. Record each result in `validations` (V-004 and V-005 are already waiting for you).
- Capital.com credentials come from environment variables only. Use the **demo** host by default.
- Only the core process talks to Capital.com. Workers read from our own data lake.
- Reference code from the earlier project is in `legacy_reference` notes on the tasks. Rewrite, do not copy.

## T-010: Capital.com REST client
- Hosts: demo `https://demo-api-capital.backend-capital.com`, live `https://api-capital.backend-capital.com`.
- Session: `POST /api/v1/session` with header `X-CAP-API-KEY` and the account identifier and password. Response headers `CST` and `X-SECURITY-TOKEN` authenticate later calls. Sessions expire after inactivity (believed about 10 minutes): use `GET /api/v1/ping` and re-create the session on 401. Never log tokens.
- Endpoints to wrap: session, ping, market search (find epics), market details (`/markets/{epic}`: dealing rules, min/max size, size step, margin, market status), historical prices (`/prices/{epic}` with `resolution`, `from`, `to`, `max`; some documentation shows `/history/prices`, so confirm which works), account info.
- Resolutions available: MINUTE, MINUTE_5, MINUTE_15, MINUTE_30, HOUR, HOUR_4, DAY, WEEK. There is **no 3-minute** resolution.
- **Rate limiter (shared, in Redis):** 10 requests per second across *all* calls; `POST /session` 1 per second; demo `POST /positions` and `POST /workingOrders` 1000 per hour. Priority classes: P0 trading and position management, P1 account and positions polling, P2 history downloads. History downloads use at most about half the budget. On HTTP 429, back off and report.
- Pagination and size limits for price history: discover the maximum candles per request and page accordingly.
- Acceptance: works against the owner's demo account (owner supplies key through RunPod Secrets or a local `.env`); rate-limiter unit tests; tokens never in logs; epics for NAS100/US100 and Gold discovered and recorded in `instruments.yaml` and `validations` (V-005).

## T-011: Capital.com WebSocket client
- Endpoint `wss://api-streaming-capital.backend-capital.com/connect`. Subscribe to OHLC (bid and ask) for configured epics; max 40 instruments; the connection needs periodic ping (documented 10-minute session) and auto-reconnect with re-subscribe.
- Acceptance: forced-disconnect test reconnects and resumes without losing minutes (gaps are back-filled from REST history).

## T-012: HistData importer
- Download or read monthly ZIP files per symbol. Targets: Nasdaq-100 (`NSXUSD`) and `XAUUSD`. Confirm coverage dates and record in `validations` (V-004).
- Parse M1 bars. The earlier project parsed the MetaTrader layout (date `YYYY.MM.DD`, time `HH:MM`, open, high, low, close, volume); generic ASCII layouts differ, so detect and validate the format per file.
- **Timezone:** HistData timestamps are US Eastern **without** daylight saving, so UTC-5 all year. Convert: `utc = timestamp + 5 hours`. Tests must include dates around the US daylight-saving changes and a market-open alignment check.
- **Price basis:** bars come from **bid** quotes. Mark `price_basis = bid` in the lake. Do not treat as mid.
- QA: duplicates, conflicting duplicates, gaps (classify weekend, session, unexpected), outliers, zero or negative prices, high below low. Write a QA report per file into `data_lineage`.
- Output: Parquet at `data/lake/{symbol}/1m/{yyyy}/{mm}.parquet`, columns `ts_utc, open, high, low, close, volume, price_basis, source`.
- Acceptance: re-importing the same file is idempotent; timezone tests pass; QA report generated.

## T-013: Resampler
- `data/resample.py` builds any timeframe from 1M. UTC-aligned bins; 3M bars start at minutes 0, 3, 6...; open first, high max, low min, close last, volume sum.
- Each bar carries `complete` (true when every constituent minute is present) and `n_minutes`. Incomplete bars are never silently dropped; downstream code can filter them.
- The same function serves historical, recorded and live data so backtests and live trading see identical candles.
- Acceptance: property tests (OHLC invariants, no lookahead); identical results from the historical and live paths for the same minutes.

## T-014: Own data recorder
- Continuously record Capital.com 1M candles with **bid and ask** OHLC and the spread to `data/lake/{symbol}/1m_capital/{yyyy}/{mm}.parquet`. Source tag `capital_ws` or `capital_rest` for back-filled minutes.
- Acceptance: no gaps across a reconnect; restart resumes from the last stored minute.

## T-015: Market hours service
- Interface: `is_open(symbol, ts)`, `next_open(symbol, ts)`, `next_close(symbol, ts)`, `is_break(symbol, ts)`, `upcoming_holidays(symbol, days)`.
- Order of authority: (1) the broker's market status from market details (confirm the field name and values, record in V-005); (2) session rules in `instruments.yaml` plus a holiday calendar; (3) data-driven check: no new tick for N seconds means treat as closed.
- Reuse the gap classification ideas from `scripts/classify_market_gaps.py` of the earlier project.
- Acceptance: tests for weekend, daily break, a US holiday, early close, and a stale-feed fallback.

## T-016: Instruments
- `config/instruments.yaml` is drafted. Implement the mapper: aliases (`NAS100`, `US100`, `NSXUSD`, `NASDAQ100` resolve to one instrument; `XAUUSD`, `GOLD`, `XAU` likewise), and read tick size, point value, min/max size, size step and margin from broker market details at startup, cached with a refresh interval.
- Acceptance: adding an instrument needs config only; unknown alias gives a clear error.

## T-017: Basis and spread calibration
- Compare HistData bid candles with Capital.com bid candles over overlapping windows: price basis (difference distribution) per instrument and session. Also run a **timezone sanity check**: cross-correlate minute returns at lags of 0 and plus or minus 1 to 6 hours and confirm the best lag matches the UTC conversion.
- Spread model: from HistData tick files (they carry bid and ask) and the recorded Capital.com data, build spread statistics per instrument and per session (median, 90th and 99th percentile). Export to `config`-style parameters used by the PaperBroker and the backtester.
- Acceptance: report in `docs/reports/basis_and_spread.md` with the numbers; cost model parameters saved with lineage.

## Phase 1 exit criteria
NAS100 and Gold data stored as 1M, 3M and 5M from both HistData and the recorder; QA reports; basis and spread report; market-hours tests pass; V-004 and V-005 resolved; the recorder has run unattended for 48 hours without gaps.
