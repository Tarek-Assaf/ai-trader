# T-194: Tai port historical test on all timeframes (parallel lane; needs no demo account)

Owner decision D-066 (Q-071 option A): run the historical test of the T-162/T-182 Tai port, labelled
"port of the stored indicator, loosely matched to the owner"; it is a diagnostic, not a Gate 1 pass.
T-184 already ran 3m on full TRAINING history. Task: run the same unchanged port (session_1800_ny anchors,
closed mode) with the T-180/T-184 harness on the remaining timeframes: 1m (mandatory), 5m and 60m, for
NSXUSD and XAUUSD, full TRAINING history from the HistData lake plus the recorded Capital.com data; report
trades, win rate, net R, profit factor, max drawdown, per-year and per-session splits, with and without
recorded spread costs (zero-cost numbers labelled as such), and a table across 1m/3m/5m/60m. 1m cells first.
Pod, read-only. Sealed holdout never read (the harness refusal guard must be exercised and reported); no broker
call, no tuning, no parameter sweep, no lake or production writes. Deliver `docs/evidence/t194/report.md`
(verdict first), `summary.json`, `scripts/lab_t194_history_all_tf.py` if the existing harness needs a thin
wrapper, and a unit test for any new code. Ruff line 100, CI green. One log entry, hand to claude.
