# T-181: Harness window-only counts (small fix to the T-180 harness; owner window dry check only)

## Why
Review of T-180 (V-448): the harness replays seven days of warm-up before the window, but it
reports signal counts, trade counts and bar counts over the warm-up as well, and keeps trades
that entered during the warm-up. Cells such as NSXUSD 5m (20 signals, 0 trades) and XAUUSD 1m
(71 signals, 0 trades) therefore cannot be read. The historical rerun (after Q-071) must count
only what falls inside the requested window.

## Task
1. In `scripts/lab_t180_hist_tai.py` (do not touch the T-162 port): keep warm-up for indicator
   state, but report separately `warmup_bars` and `window_bars`, and count `signal_count` only for
   bars inside the window. Discard trades whose entry time is before the window start (report
   `discarded_warmup_trades`). Trades entered in the window keep their management to the end.
2. For every cell with signals > 0 and trades == 0, add `zero_trade_reason` taken from the port
   itself (which entry filter blocked the signal, with counts). If the port exposes no such
   counter, report "not exposed" and do not edit the port.
3. Re-run the same dry check (NSXUSD and XAUUSD, 1m/3m/5m/60m, owner window 2026-10-06 .. 2026-10-16
   only; longer history NOT run). Write `docs/evidence/t181_report.md` and
   `docs/evidence/t181_dry_check.json`; label field in every output as in T-180.
4. Tests: add to `tests/unit/test_t180_hist_tai.py` a case where a synthetic signal falls in the
   warm-up and must not count, and the holdout refusal still holds.

## Verdict first
"Window-only counts yes/no; cells run n/8; zero-trade cells explained n; longer history NOT run."

## Rules
No broker calls, orders, sealed holdout reads, lake or production writes, tuning, secrets. Lint
with ruff check and ruff format (line length 100; no per-line noqa). One log entry, hand the turn
to claude, stop.
