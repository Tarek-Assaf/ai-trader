# T-146 movement matching report

Diagnostic only. No broker, orders, paper trading, holdout, Gate 1, ledger or promotion activity.

Generated: 2026-10-17T13:40:12Z
In-sample: `2022-01-01T00:00:00+00:00` to `2025-01-01T00:00:00+00:00`
Out-of-sample: `2025-01-01T00:00:00+00:00` to `2026-07-01T00:00:00+00:00`

## Verdicts

- **NSXUSD 1m — verdict=INCONCLUSIVE (partial checkpoint)**: library_windows=0, oos_queries=36, queries_evaluated=36, result_rows=0, trial_count=n/a (no result rows emitted), elapsed_seconds=1200.120 rows_loaded_before_holdout=0, first_timestamp=none, last_timestamp=none, windows_after_atr_filter=0.
  - The 1200-second guard stopped this group after the checkpoint; no complete result row was available to classify.
- **NSXUSD 3m — verdict=INCONCLUSIVE (partial checkpoint)**: library_windows=0, oos_queries=36, queries_evaluated=36, result_rows=0, trial_count=n/a (no result rows emitted), elapsed_seconds=1200.116 rows_loaded_before_holdout=0, first_timestamp=none, last_timestamp=none, windows_after_atr_filter=0.
  - The 1200-second guard stopped this group after the checkpoint; no complete result row was available to classify.
- **NSXUSD 5m — verdict=INCONCLUSIVE (partial checkpoint)**: library_windows=0, oos_queries=36, queries_evaluated=36, result_rows=0, trial_count=n/a (no result rows emitted), elapsed_seconds=1200.115 rows_loaded_before_holdout=0, first_timestamp=none, last_timestamp=none, windows_after_atr_filter=0.
  - The 1200-second guard stopped this group after the checkpoint; no complete result row was available to classify.
- **NSXUSD 60m — verdict=INCONCLUSIVE (empty library)**: library_windows=0, oos_queries=0, queries_evaluated=0, result_rows=18, trial_count=360, elapsed_seconds=0.371 rows_loaded_before_holdout=0, first_timestamp=none, last_timestamp=none, windows_after_atr_filter=0.
  - The 18 registered NOT_FOUND rows are retained diagnostics, but the group verdict is inconclusive because the usable library is empty.
- **XAUUSD 1m — verdict=INCONCLUSIVE (partial checkpoint)**: library_windows=0, oos_queries=36, queries_evaluated=36, result_rows=0, trial_count=n/a (no result rows emitted), elapsed_seconds=1200.115 rows_loaded_before_holdout=1018912, first_timestamp=2022-01-02T23:00:00+00:00, last_timestamp=2024-12-31T21:57:00+00:00, windows_after_atr_filter=0.
  - The 1200-second guard stopped this group after the checkpoint; no complete result row was available to classify.
- **XAUUSD 3m — verdict=INCONCLUSIVE (partial checkpoint)**: library_windows=0, oos_queries=36, queries_evaluated=36, result_rows=0, trial_count=n/a (no result rows emitted), elapsed_seconds=1200.120 rows_loaded_before_holdout=1018912, first_timestamp=2022-01-02T23:00:00+00:00, last_timestamp=2024-12-31T21:57:00+00:00, windows_after_atr_filter=0.
  - The 1200-second guard stopped this group after the checkpoint; no complete result row was available to classify.
- **XAUUSD 5m — verdict=INCONCLUSIVE (partial checkpoint)**: library_windows=0, oos_queries=36, queries_evaluated=36, result_rows=0, trial_count=n/a (no result rows emitted), elapsed_seconds=1200.114 rows_loaded_before_holdout=1018912, first_timestamp=2022-01-02T23:00:00+00:00, last_timestamp=2024-12-31T21:57:00+00:00, windows_after_atr_filter=0.
  - The 1200-second guard stopped this group after the checkpoint; no complete result row was available to classify.
- **XAUUSD 60m — verdict=INCONCLUSIVE (empty library)**: library_windows=0, oos_queries=0, queries_evaluated=0, result_rows=18, trial_count=360, elapsed_seconds=0.260 rows_loaded_before_holdout=1018912, first_timestamp=2022-01-02T23:00:00+00:00, last_timestamp=2024-12-31T21:57:00+00:00, windows_after_atr_filter=0.
  - The 18 registered NOT_FOUND rows are retained diagnostics, but the group verdict is inconclusive because the usable library is empty.

## Coverage interpretation

The runner returned zero eligible continuous in-sample windows for every group. This is a data coverage/continuity outcome and does not establish that the movement edge is absent.

The Pod lake listing had no `NSXUSD/1m` parquet source. It did show `NAS100/1m_capital` and `XAUUSD/1m`; XAUUSD had 211 monthly 1m files, but the strict contiguous-window builder still produced no eligible 30, 60 or 120 bar windows.

For timed groups, `queries_evaluated=36` means 18 registered length/method/horizon rows multiplied by `max_queries=2` before the 1200-second guard. Those six groups emitted no complete records before the guard, so their verdict remains inconclusive.

All rows preserve the registered diagnostics and costs. No promotion or trading decision is made from this checkpoint.
