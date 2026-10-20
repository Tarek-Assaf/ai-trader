# T-203: post-exit refresh trigger

## Result

The implementation adds a `REFRESH_BARS = 12` post-exit trigger to the Tai
signal provider. The runner records `last_exit_at` in the existing T-191 state
file and calls the provider's exit hook whenever a broker, shadow, target, or
stop exit removes a managed position. Cross entries still take precedence over
refresh entries, and the refresh path applies the same EMA, ADX/DI, VWAP,
distance, swing, and risk checks.

## Pod replay

The owner-provided Pod was reachable. A read-only NSXUSD replay was run with:

```text
python scripts/lab_t195_runtime_miss.py --lake /workspace/data/lake \
  --holdout-start 2026-07-01T00:00:00Z --epic NSXUSD --days 10
```

The replay completed with **1/26 exact matches** and no extras. The first
differing quantity remains the runtime `ha_cross` gate: 25/26 port entries are
refresh entries that cannot be reproduced by the direct provider trace without
the live position exit event. The updated production path now persists that
event and starts the 12-bar countdown from it. The next controlled replay must
exercise the full runner exit callback rather than the provider-only trace.

Safety evidence: broker calls 0, order calls 0, sealed holdout reads false,
lake writes false, risk/gate edits false, redeploy false, and secrets used
false.

## Checks

- `python -m compileall -q src tests`: passed.
- `git diff --check`: passed.
- `python agent_state/validate_handoff.py`: passed (structural JSON schema
  skipped because `jsonschema` is not installed).
- `pytest` and `make lint test` could not run in this Windows checkout because
  the dependencies and `make` are unavailable.
