# T-199 runtime EMA200 parity

## Root cause

`TaiSignalProvider` previously retained only `max(minimum_history * 2, 600)` bars and `from_lake` passed only the latest `minimum_history` bars. The runtime `_ema` implementation uses the same 200 period, SMA seed, and recursive alpha as the closed T-162 port, but truncating the input changes the seed whenever a series is longer than the retained window. The fix keeps all available lake history and all bars appended during a provider session. The existing `minimum_history` readiness gate remains unchanged.

Quoted implementations:

- Runtime `_ema`: `src/trader/experiments/tai_wiring.py:199-211`.
- Closed port `_ema`: `scripts/lab_t162_pine_port.py:112-124`.

Both implementations use `SMA(values[:period])`, `alpha = 2 / (period + 1)`, and the same recursive update. No strategy, risk, gate, or broker code was changed.

## Numeric sanity check

A local 850-bar synthetic series was evaluated with the same recurrence:

| Input | EMA200 at final bar |
| --- | ---: |
| Full 850-bar series | 101.896749405124496 |
| Last 600 bars only | 101.896784610789680 |
| Difference | -0.000035205665185 |

The restart test in `tests/unit/test_tai_wiring.py` feeds the same long series continuously and after a restart; both retain the full history and produce the same EMA result.

## Historical replay status

T-195's owner-Pod baseline recorded 26 port entries versus 1 runtime entry, with 25 misses (23 first failing at `ha_cross`). This branch does not claim a new T-187/T-195 replay: the owner-provided Pod URL returned HTTP 404 for the bounded read-only request, so the replay route/session was unavailable. No broker, holdout, redeploy, or secret access was attempted.

## Validation

- `python -m ruff format src/trader/experiments/tai_wiring.py tests/unit/test_tai_wiring.py` passed.
- `python -m ruff check src/trader/experiments/tai_wiring.py tests/unit/test_tai_wiring.py --line-length 100` passed.
- `python -m py_compile src/trader/experiments/tai_wiring.py tests/unit/test_tai_wiring.py` passed.
- `git diff --check` passed.
- Focused pytest could not run because this desktop environment has no `pytest` module (`No module named pytest`).
