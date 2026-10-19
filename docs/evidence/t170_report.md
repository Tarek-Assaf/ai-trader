# T-170 owner-bar clock and condition diagnosis

**Verdict:** clock aligned **no** (peak offset +1, 11 of 31 prices contained); lowest-agreement condition is **direction** (0 of 31 at offset 0); equal-weight VWAP gives **4 of 31** within +/-3 (baseline 4). The target of 25 is not met. The numbers point to a signal-direction/condition mismatch rather than a simple clock shift; the moving 197.90-249.60 point basis is also material.

This is one bounded, replay-only NSXUSD run with seven days of full warm-up over 4,013 complete 3-minute bars. The port was not edited. The complete per-entry basis lists, condition rows and raw equal-weight replay are reproducible from `scripts/lab_t170_conditions.py`; the committed aggregate artifact records the Pod hashes and requested counts.

## Clock containment

After subtracting the overall median owner-minus-port close basis (235.80), the price-containment counts by bar offset are:

```text
{"-10": 3, "-9": 2, "-8": 3, "-7": 9, "-6": 9, "-5": 7, "-4": 5, "-3": 9, "-2": 8, "-1": 9, "0": 8, "1": 11, "2": 6, "3": 8, "4": 10, "5": 9, "6": 6, "7": 7, "8": 7, "9": 5, "10": 5}
```

The +1 peak is weak rather than a clear alignment correction. Per-New-York-day median basis ranges from 197.90 to 249.60, supporting a moving basis rather than a single safe correction.

## Per-condition agreement with owner direction

Counts at offsets -1, 0 and +1 bars:

```json
{
  "-1": {"direction": 0, "ha_state": 8, "vwap_side": 6, "ema_filter": 25, "adx_di": 20, "risk": 13, "cooldown": 24},
  "0": {"direction": 0, "ha_state": 8, "vwap_side": 5, "ema_filter": 24, "adx_di": 19, "risk": 19, "cooldown": 23},
  "+1": {"direction": 0, "ha_state": 8, "vwap_side": 5, "ema_filter": 25, "adx_di": 21, "risk": 21, "cooldown": 23}
}
```

## Registered equal-weight VWAP variant

The equal-weight running `hlc3` VWAP, reset on the same UTC-day boundary as the port, produced 33 trades and 4 of 31 owner entries within +/-3 bars. It did not improve the baseline.

## Safety and reproducibility

Pod command exit was 0. Raw Pod hashes are in `docs/evidence/t170_conditions.json`. Safety counters are all zero/false: no broker calls, orders, sealed holdout reads, lake writes, production writes, tuning or strategy search.
