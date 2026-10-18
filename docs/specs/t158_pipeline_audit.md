# T-158: Pipeline audit. Find where OUR system goes wrong (known-answer tests)

Status: spec for Codex. Diagnostic only: no broker calls (not even read-only price calls), no
orders, no gate/cost/ledger/promotion writes, no lake writes, sealed holdout never read, no
parameter tuning. Pod data only. One task, one report. After this task we do not repeat T-155 to
T-157 style "try another variant" rounds.

## Why
Owner 2026-10-18: the Tai indicator prints signals on Capital.com US100 too (Q-068), the strategy
and his 31 trades are known, and every other valid strategy we tried also failed. T-157 got 0/31
matches on every price basis and with nine variants. When a correct strategy and correct data give
0/31, the fault is in OUR pipeline (data, time handling, bar building, indicator maths, replay or
engine), not in the indicator and not in the owner's information. We stop asking the owner for
more input. We prove each stage of the pipeline against an answer known in advance.

## Method
For each stage below: define the known answer first, run the real production code path (not a
copy written for the test), record PASS/FAIL with numbers. Run all stages even if an early one
fails, then list every failure. Tests that use synthetic data must say so.

### Stage 1: Time alignment (the most likely hidden fault)
Use the owner's trades CSV (NQ1 futures, 3m, times UTC per owner confirmation 2026-10-17).
Compare ONLY moves, never price levels. For every trade with both entry and exit rows:
1. Compute the direction of the NQ1 move between entry time and exit time (exit price minus entry
   price) and the direction of the recorded NAS100 mid move over the same interval.
2. Repeat after shifting the NAS100 clock by every step from -12h to +12h in 30-minute steps.
   Report the agreement rate (same sign) per shift, and the correlation of moves.
3. Expected: the best shift is 0 with a clearly higher agreement than the other shifts. If the
   best shift is not 0, or agreement at 0 is under 70%, FAIL and state the offset (this would mean
   our recorded timestamps, the owner CSV times, or the lake bar-time convention are shifted).
4. Also report: bar timestamp convention in the lake (bar open or bar close), DST/session break
   handling around the Oct 2026 weekends, duplicate or missing 1m bars per day for 5-16 Oct, and
   bid/ask sanity (ask >= bid, spread distribution, no zero or negative prices).

### Stage 2: Bar building
Known answer: build 3m, 5m, 54m and 60m bars from the 1m lake with the production code and
compare with an independent plain-Python aggregation written in the test (first open, max high,
min low, last close, anchored at the documented start). Report mismatches per timeframe.
Check the 54m grouping explicitly: where each 54m bar starts per session anchor (UTC midnight,
CME open 17:00 Chicago, Riyadh midnight), how many 3m bars it contains (must be 18), and what
happens at the Sunday session gap and the daily break.

### Stage 3: Indicator maths against references
For EMA(200), ATR(14), ADX(14)/+DI/-DI (Wilder), VWAP (hlc3, per-session reset) and Heikin Ashi:
1. Compare the production functions with hand-checkable tiny examples (10 to 30 numbers, expected
   values written in the test from a textbook calculation and shown in the report).
2. Compare with an independent reference on 2 days of real 3m data: `pandas_ta` or `ta` if they
   install on the Pod, otherwise a second plain-Python implementation. State which one.
   Tolerance 1e-9 for exact formulas; for EMA/Wilder state the warm-up bars excluded.
3. Heikin Ashi on the 54m series: print the last 8 candles (HA open, close, colour) before each of
   the first 5 owner entries so the owner and Claude can read them by eye.

### Stage 4: Rule logic in the replay
1. Label each owner entry as new signal (HA flip) or refresh entry (flat >= 12 bars since last
   exit, rules agree) using the CSV spacing. Report how many are each. A refresh entry needs only
   the HA state, not a fresh cross; check the replay treats them differently and state whether
   T-157 did.
2. For each entry, report the 54m HA state as: (a) flip on the current bar, (b) same colour as the
   direction but no flip, (c) opposite colour. Counts per category. This shows whether our
   `ha_state` rule asks for a cross when the indicator only needs the colour (or the reverse).
3. Positive control: build a synthetic 3m series where every rule is satisfied at a known bar and
   confirm the production replay reports exactly that signal; then break one rule at a time and
   confirm the signal disappears. FAIL if any case behaves differently.

### Stage 5: Engine and cost arithmetic (may explain why other valid strategies also failed)
1. Hand-made ledger: 10 trades with known entry, exit, direction, size and cost. Run the
   production backtest/accounting and compare P&L, win rate, drawdown and cost to values computed
   by hand in the test.
2. Lookahead cheat test: run a deliberately cheating strategy (buys when the NEXT bar closes up)
   on 2 real days; it must show a very high win rate. If it does not, the engine is blocking or
   mis-timing fills. Run a coin-flip control; it must lose about the cost per trade.
3. Buy-and-hold check on a trending day: engine P&L must equal last close minus first open (minus
   cost), within rounding.
4. Report whether `docs/evidence/positive_control.json` (T-137) covers each item above and list
   the items it does NOT cover.

## Deliverables
- `scripts/lab_t158_pipeline_audit.py` (read-only, deterministic, prints a PASS/FAIL table).
- Unit tests for the synthetic known-answer cases (Stages 2 to 5).
- `docs/evidence/t158_pipeline_audit.json` and `docs/evidence/t158_report.md`.
- The report starts with a plain-language verdict for the owner (max 10 lines): which stages
  PASS, which FAIL, the single most likely cause, and the proposed fix (not applied). If every
  stage passes, say so plainly and state what that rules out.

## Acceptance criteria
- All five stages run on Pod data (Stage 1 and the real-data parts of Stages 2 to 5) and every
  check is reported PASS or FAIL with numbers; none skipped silently. A check that cannot run is
  reported NOT RUN with the reason.
- Production code paths are used for the tested functions; any independent reference is labelled.
- Stage 1 reports the agreement-by-shift table (-12h to +12h) and names the best shift.
- No fixes, tuning or config changes in this task. Fixes become a separate task after review.
- Safety: broker calls 0, orders 0, sealed holdout read false, no gate/cost/ledger/promotion/lake
  writes (stated in the report with hashes of the evidence files).
- Tests, ruff check and ruff format (line length 100) pass; one log entry; hand turn to claude.
- Stop rule: no extra variants beyond this spec. Report and stop.
