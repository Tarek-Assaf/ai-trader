# T-187 Pod replay report

## Verdict

**FAIL: the wired runtime does not reproduce the closed T-162 port exactly.** The bounded replay completed successfully and found 2 matching entries out of 25 session_1800_ny port entries, with 0 extras and 23 misses. The misses are explained by 22 `missing_runtime_direction` cases and one `bar_or_stop_difference` at `2026-06-22T23:21:00Z`.

The runtime emitted two entries:

- `2026-06-22T16:00:00Z` SELL, entry `30353.864`, stop `30377.306305385721031`
- `2026-06-24T16:00:00Z` BUY, entry `29556.867`, stop `29462.963656558964332`

The first mismatch is the port BUY at `2026-06-22T23:21:00Z`, entry `30360.649`, stop `30334.88497838238`; the script classified it as `bar_or_stop_difference` against the runtime candidate.

## Run

Command:

```text
python scripts/lab_t187_runtime_replay.py --lake /workspace/data/lake --holdout-start 2026-07-01T00:00:00Z --output-dir /tmp/t187
```

The run used 5,203,143 minute rows from the ten selected trading dates 2026-06-19 through 2026-06-30, excluding the sealed holdout boundary at 2026-07-01T00:00:00Z. The report also recorded 27 epoch-anchor entries for information only.

## Safety

The dry-run produced 2 `dry_run_would_submit` journal lines and made 0 order calls. The script recorded 0 broker calls, no network access, no sealed holdout read, no lake writes, no risk or gate edits, and no redeploy.
