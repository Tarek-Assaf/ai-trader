# T-190: Dry-run day report (trades and P&L of the Tai demo experiment, as if it had traded)

## Why
The Tai demo runner is on the Pod in dry-run. In dry-run `TaiDemoRunner.on_bar` only journals
`dry_run_would_submit`; it creates no virtual position, so there are no exits and no P&L, and a
signal that appears while a virtual trade would still be open is not suppressed. The owner wants a
trades-and-P&L report late today (2026-10-19, Riyadh evening) to see how the strategy worked.

## Build (read-only; no broker call, no network, no redeploy, no holdout, no lake writes)
1. `scripts/report_t190_dry_run_day.py` (arguments: lake path, epic, `--from` UTC timestamp, `--to`
   UTC timestamp, `--start-balance` read from the same setting the runtime uses, output dir). It
   replays the 1m recorded bid/ask rows of the window through the REAL runtime path
   (`ThreeMinuteBarBuilder` -> `TaiSignalProvider` -> `TaiDemoRunner` with `dry_run=False` against an
   in-memory `ShadowGateway`/`ShadowOrderManager`, never the broker): one-position rule, owner sizing
   (0.1 lot per 10,000 balance rounded to the broker step, using the shadow balance), 40/30/30 exits at
   1R/2R/3R, stop to entry after TP1, structure stop, market hours and blackout as in the runtime.
   Fills: entries and exits at the recorded bid/ask side that a real market order would get (buy at
   ask, sell at bid; stop and target levels triggered on the bar high/low as the runtime does; if a
   bar touches both stop and a target, the stop is taken first and the report says how often that
   ambiguity occurred).
2. Cross-check: read the API journal / log lines `dry_run_would_submit` for the same window (path as
   documented in the T-189/T-186 evidence; if the journal is not reachable, say so) and list, per signal
   time, whether the shadow replay produced the same signal (time, direction, stop). Differences are
   listed with the reason (for example the live dry-run does not apply the one-position rule).
3. Output under `docs/evidence/t190/`: `report.md` (verdict first, one table of trades with entry time,
   direction, entry, stop, TP1/2/3 hits, exit prices, points, USD, R multiple; totals: trades, win rate,
   net points, net USD, max drawdown, average R; the cross-check result; data gaps in the window),
   `trades.csv`, `summary.json`, and `equity.png` (equity in USD by trade, plain chart, labelled axes).
4. Verdict line: "N shadow trades, net X USD (Y R), cross-check agrees A of B signals; data gaps G minutes."
   Spread/slippage assumptions stated; no cost invented: use recorded bid and ask only.

## Run
Run once on the Pod for the window from the dry-run start (about 2026-10-19 15:00 UTC) to the last
closed minute at run time, and re-run on request later. Commit the generated report only after it ran.

## Rules
No broker or order call, no sealed holdout read, no lake or production writes, no risk or gate edits, no
secrets in logs. Demo labelling: every file says "dry-run shadow, not real orders". Unit test: known-answer
fixture (long hits TP1, TP2, TP3; long stops out; short with stop moved to entry after TP1; both touched
in one bar). Ruff check and format (line length 100), CI green. One log entry, hand the turn to claude.
