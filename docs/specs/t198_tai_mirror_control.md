# T-198: Tai mirror control (Q-057 option B; Lane B, no demo account needed)

Owner answered Q-057 with option B on 2026-10-19. D-040 parked the Tai tests until the owner finished
validating his trades; the owner has since asked for the history tests (D-066, T-194), so this control
runs now as the last piece of that diagnostic. It is a side check, not a Gate 1 decision.

## Correct definition (the earlier versions were degenerate, about 9 trades a year)
1. Run the unchanged port (`port.replay(bars, "closed")` through `lab_t180_hist_tai._replay`) exactly as
   T-194 does. Do NOT change signal, filter, session or indicator code.
2. For every trade the ORIGINAL run produced (same entry bar, same entry price, same stop distance D and
   same target distances), build a mirror trade: opposite direction, entry at the same bar open price,
   stop at entry +/- D on the INVERTED side, targets at the same R multiples on the inverted side,
   same exit rules (stop to entry after first target, time exit) applied bar by bar from the entry bar.
3. Mirror trades are therefore as many as original trades (about 1,000 or more a year on 3m), never 9.
   If the count differs from the original by more than 0, the run is wrong; stop and report why.
4. Costs: apply the same configured cost model (spread, slippage) to the mirror trades and also report
   zero-cost. Label results "mirror control of the port, diagnostic only".

## Scope
NSXUSD and XAUUSD, 1m, 3m, 5m; skip 60m (no signals). Full TRAINING window 2020-10-17 to 2026-10-17
(end exclusive), seven days warm-up, same as T-194. Pod, read-only.

## Acceptance criteria
- New wrapper `scripts/lab_t198_mirror_control.py` reusing the T-194/T-180 harness, plus a unit test using
  a tiny synthetic bar series: original long trade wins 2R, mirror short trade loses 1R; original stop hit,
  mirror reaches its first target. Test must import like `tests/unit/test_lab_t194_history_all_tf.py`
  (insert repo root into sys.path).
- `docs/evidence/t198/report.md` (verdict first) and `summary.json`: per cell original vs mirror trades,
  win rate, net R, profit factor, max drawdown, configured and zero-cost, per-year split. State plainly
  whether original + mirror net R is near the cost only (no edge) or the original beats the mirror
  (directional edge).
- Mirror trade count equals original trade count in every cell.
- Holdout guard exercised; no broker call, no sealed holdout read, no tuning, no risk or gate edits,
  no secrets, no production or lake writes. Ruff line length 100, CI quality SUCCESS.
- One log entry, hand the turn to claude, stop. Run on the Pod once; build the report from printed output.
