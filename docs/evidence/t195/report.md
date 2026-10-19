# T-195 runtime miss diagnostic

Status: **blocked pending a bounded lake scan**.

The accepted T-187 replay established the baseline: 25 session-anchored port entries, 2 bounded runtime entries, 0 extras, and 23 misses (22 `missing_runtime_direction` and one `bar_or_stop_difference`). T-195's diagnostic script is implemented in `scripts/lab_t195_runtime_miss.py` and includes the bounded versus unbounded history comparison and first-failing-gate trace.

The owner Pod was reachable, but the legacy replay loader materialized the full lake before the diagnostic could run. The process reached approximately 3 GB RSS and was stopped. The required 25-row side-by-side output is therefore not claimed. The next safe step is to add a date-filtered lake reader or run the diagnostic from a prefiltered parquet slice, then rerun the same read-only script.

Safety counters for the attempted run: 0 broker calls, 0 order calls, no network access, no sealed holdout reads, no lake writes, and no redeploy.
