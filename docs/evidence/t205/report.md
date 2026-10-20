# T-205 last miss stop parity

## Result

The one remaining T-204 miss is not a stop-input mismatch at the 2026-06-30 14:27Z
SELL bar. The first differing quantity is the **exit callback state used for the
refresh trigger**. The port's prior SELL exits at 10:09Z, then refreshes at
14:27Z (the 14:27 trade also exits on that same bar). The T-204 harness feeds
the 14:27Z exit callback before evaluating the 14:27Z bar, so the provider's
`last_exit_at` is overwritten to 14:27Z and the refresh age is zero. The
provider therefore emits no signal. `ha_cross` is false on both paths, which is
expected for a refresh entry. Recomputing the hypothetical SELL stop at that
same bar gives the same inputs and value as the port:

| Quantity | Port | Runtime at 14:27Z |
| --- | ---: | ---: |
| history bars | 4,229 full history / 600 bounded window | 600 bounded window |
| ATR14 | 24.51261351548424 | 24.51261351548424 |
| maximum prior swing high | 29,765.849 | 29,765.849 |
| SELL stop (`max_high + 0.20 * ATR`) | 29,770.751522703096 | 29,770.751522703096849 |

The nearest runtime candidate in the T-204 evidence is the **earlier** 09:51Z
SELL, not a later bar. Its wider stop is explained by that candidate's own
inputs: ATR14 `23.23775794906513`, maximum prior high `29,794.909`, and stop
`29,799.556551589813026`. Its entry and stop match the port at 09:51Z.

Therefore the first differing quantity is the callback ordering/state (`last_exit_at`
10:09Z in the port refresh path versus 14:27Z in the harness), not a stop
component or a production source calculation. The narrow fix proposal is to
evaluate the provider signal before applying an exit callback whose timestamp
is the current bar, or to supply only exits strictly before the bar to this
replay. No `src/` change is proposed by this task.

## Reproduction and safety

- Epic: `NSXUSD`; ten selected pre-holdout dates ending before `2026-07-01T00:00:00Z`.
- The Pod image's `/app` directory is not a Git repository. The replay used the
  image-supplied checkout at `/workspace/t204_src` and its lake at
  `/workspace/data/lake`.
- Read-only Python probe; no broker calls, orders, holdout reads, lake writes,
  risk/gate edits, redeploy, or secrets.
- Command context: `PYTHONPATH=/workspace/t204_src:/workspace/t204_src/src`
  with the production replay modules and the recorded lake.

## Validation

- `python -m compileall -q src tests scripts`
- `python -m py_compile scripts/lab_t195_runtime_miss.py tests/unit/test_t195_runtime_miss.py`
- `git diff --check`
- `python agent_state/validate_handoff.py` (`OK`; schema check skipped because
  `jsonschema` is unavailable on the desktop)
- `pytest` and `make lint test` are unavailable on this Windows desktop.
