# T-162: Faithful port of the Tai Smart Filter Pine script, then prove the agent reproduces the owner's 31 entries

Status: spec for Codex (queue when T-161 is reviewed). Diagnostic only: no broker calls, no
orders, no gate/cost/ledger/promotion writes, no lake writes, sealed holdout never read, no
tuning. Pod data only. All four timeframes 1m/3m/5m/60m where lake data is used and both NSXUSD
and XAUUSD for the later historical rerun; 1m mandatory.

## Why
Owner 2026-10-18/19: "same strategy and indicators as in the shared document, no differences;
find what is wrong in our agent, fix it, then rerun the historical test". The owner uploaded the
real source (docx = the full Pine v5 script, now stored as
`docs/evidence/owner/tai_smart_filter_pine_source.txt`). Our earlier specs were written from a
summary. Reading the real source shows our replay and backtest differ from it in these ways
(line numbers refer to the stored source file):

1. **Refresh entries do not check the Heikin Ashi state at all** (lines 584-596, 602-616).
   `refreshBuy/Sell` need only: refresh on, flat, `bar_index - lastExitBar >= 12`, the smart
   filter (EMA200 side, ADX/DI, VWAP side, VWAP distance), the risk limit and the date. No HA
   cross and no HA colour. T-158 found 29 of the owner's 31 entries are refreshes, and T-157
   still required `ha_state == direction` for every entry (T-158 said so itself). That alone
   can explain the 19 "ha_state" failures. A refresh needs a previous exit (`lastExitBar` set).
2. **Trade management runs on the entry bar itself** (entry block lines 753-836, management
   from line 931): the entry bar's own high/low can already hit TP1/TP2/TP3 or the stop.
3. **Intrabar order is target-first, not stop-first** (lines 939-1032): before TP1 the script
   tests TP3, then TP2, then TP1, then the stop. After TP1/TP2 it tests TP3, TP2, then the
   break-even stop at entry. Our lab used stop-first (conservative), which changes exits and
   therefore when the next refresh entry becomes allowed.
4. **Exit bar and refresh counter**: `lastExitBar` is set on the bar where `inTrade` turns
   false (lines 1314-1315). `barsSinceExit` counts chart (3m) bars. Entries are checked before
   management on each bar, so no new entry on the exit bar.
5. **Signals are evaluated every 3m chart bar** from `request.security` values of the Heikin
   Ashi ticker on a 54-minute timeframe (`tfmult` 18 x 3m, lines 194-248) with
   `lookahead_off`. A cross is `ta.crossover(haClose, haOpen)` on the chart-bar series. In
   TradingView Replay the higher-timeframe value is the forming candle, which can cross and
   uncross inside one 54m bar; closed-bar mode is the alternative. Both must be reproduced.
6. **Indicators on the chart timeframe**: `ta.ema(close,200)`, `ta.dmi(14,14)` (Wilder),
   `ta.vwap(hlc3)` (volume-weighted, session anchored), `ta.atr(14)` (RMA). Swing stop
   `lowest(low[1],5) - 0.2*ATR` (excludes the signal bar itself). Risk skip when risk <= 0 or
   risk/ATR > 2.0. Targets 1R/2R/3R from `entry = close` of the signal bar.
7. Owner settings: Open/Close setup, "No Filtering" sideways filter, date filter from
   2023-01-01 +0300, all smart filters on. (Source file title says Renko, but the chosen setup
   is Open/Close; confirm Renko is not used.)
8. **Data**: the owner traded NQ1 futures with real volume; our recorder data is Capital.com
   NAS100 CFD. State how our VWAP is weighted (volume or not) and whether the recorded bars
   carry volume. Level differences between NQ1 and NAS100 are expected; compare entry/exit
   TIMES and directions, as before.

## Steps
1. Write `scripts/lab_t162_pine_port.py`: an exact bar-by-bar Python port of the stored script
   (one function per Pine block, comment with source line numbers), usable as the single
   reference implementation. Do not reuse lab_tai_smart_filter.py logic; write the port from
   the source text.
2. Known-answer unit tests: hand-made 3m series where each rule is satisfied once (new signal,
   refresh entry with opposite HA colour, TP1-then-BE, TP3, same-bar target-vs-stop order,
   entry-bar exit) with expected results written from the Pine source.
3. Replay on the recorded NAS100 3m data 5-16 Oct 2026 with the owner CSV read as New York time
   (T-160). Compare with the owner's 31 entries (+-0/1/2 three-minute bars) and with exit times
   and exit types (stop / BE / TP). Run both HA modes (closed and forming/replay). Report entries
   reproduced, extra entries the port would take, and the first differing rule per missing one.
4. Success bar for "agent operates as the indicator": at least 25 of 31 entries reproduced.
   If lower, STOP and report the per-entry differences (HA values, EMA/ADX/VWAP/ATR numbers at
   the entry minute next to the port's); no tuning and no extra variants.
5. Only after step 4 passes: replace the signal/exit logic used by the Tai lab test with the port
   (new task), and rerun the historical test. Not part of this task.

## Deliverables
`scripts/lab_t162_pine_port.py`, `tests/unit/test_t162_pine_port.py`,
`docs/evidence/t162_report.md` (plain owner verdict first), `docs/evidence/t162_port.json`,
`docs/evidence/t162_entries.csv`.
