# T-204 runner exit replay

## Result

The replay exercised the production `TaiSignalProvider` and called
`note_exit(exit_bar_time)` for each closed-port exit before evaluating the
comparison bars. The ten selected NSXUSD dates before the 2026-07-01 holdout
boundary produced **25 of 26 exact matches**, with no extras. This meets the
acceptance threshold of at least 22 matches.

The single miss is measured below rather than being hidden behind a generic
count. The provider emitted a nearby later SELL candidate, so the remaining
difference is a timestamp/signal selection difference after the exit callback;
the refresh callback itself reproduced the 25 refresh entries.

| Timestamp | Side | Cause | Port entry / stop | Runtime candidate entry / stop | First failing gate | History |
|---|---|---|---|---|---|---:|
| 2026-06-30T14:27:00+00:00 | SELL | runtime_no_signal_after_all_gates | 29762.821 / 29770.751522703096 | 29763.442 / 29799.556551589813026 | None | 3704 |

## Safety

The Pod run was read-only: broker calls 0, order calls 0, network access false,
sealed holdout reads false, lake writes false, risk or gate edits false,
redeploy false, and secrets used false.

## Checks

- `python -m compileall -q src tests scripts`: passed locally.
- `git diff --check`: passed locally.
- `pytest` and `make lint test` were unavailable in the Windows checkout.

The Pod command was:

```text
PYTHONPATH=src python scripts/lab_t195_runtime_miss.py \
  --lake /workspace/data/lake \
  --holdout-start 2026-07-01T00:00:00Z \
  --epic NSXUSD --days 10 --output-dir /workspace/t204_run
```
