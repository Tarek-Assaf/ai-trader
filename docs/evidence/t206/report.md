# T-206 first Tai demo session watch

## Session

- Pod image: `bfb1193` (owner-provided terminal; read-only inspection).
- Runtime: `paper` mode, `demo` broker environment, `LIVE_TRADING_ENABLED=false`.
- `/app` is not a Git repository; this watch used the files and logs supplied by the running image.
- The first enabled startup was `2026-10-20T12:33:22.534Z`:
  `Tai demo startup experiment=on dry_run=off epic=US100 warmup_bars=4520 start_balance_present=yes`.
- `TAI_DEMO_DRY_RUN=false` was confirmed in the running process environment at `2026-10-20T12:44:42Z`.
- `/workspace/state/tai_demo_positions.json` exists and contains `last_exit_at: null`, `positions: {}`, `version: 1`.

## Journal and safety review

For the session window from `12:33:22Z` through `12:44:42Z`, the image log contains:

| Event | Count | Result |
| --- | ---: | --- |
| entries | 0 | no entry event to compare |
| refusals | 0 | no refusal storm; no `spread_too_wide` |
| exits | 0 | no exit event or exit error |
| order events | 0 | no order call/event observed |
| open positions | 0 | state file is flat |

The only runtime alerts were two short `runtime_dependency_unavailable` bursts, each followed by `runtime_recovered` (12:36:24–12:36:31Z and 12:39:22–12:39:29Z). Feed polling continued with `stale=False`; no duplicate position, unprotected order, or state-file mismatch was observed. No safety blocker was found.

The audit table still contained only the existing `restore_test` rows; no Tai entry/refusal/exit audit event was present. Capital.com market/session reads were successful, and no order endpoint activity was observed.

## Port comparison

There were no Tai entries in this first session, so the port replay has no entry bars to evaluate:

- matched: **0**
- extras: **0**
- misses: **0**
- first differing quantity: **not applicable**

This is a zero-trade observation, not evidence of entry parity. There are too few trades to judge the strategy or compare quantities.

## P&L and spread

No entry or exit occurred. Realized demo P&L is therefore **0 R / 0 money**, with no per-entry spread observations. The `0.25R` spread guard was not exercised and cannot be judged from this session.

## Safety and provenance

- Read-only Pod inspection; no configuration, code, risk/gate, lake, redeploy, broker-write, order, holdout, or secret operation.
- Dry-run remains disabled in the running demo process because the owner explicitly enabled it; defaults and repository configuration were not changed.
- Follow-up: continue observing a later session if the owner wants trade-level parity evidence.
