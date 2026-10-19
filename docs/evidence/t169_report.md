# T-169 owner entry match

**Verdict:** 4 of 31 owner entries reproduced within +/-3 bars (target 25). The target is not met; the mismatches are dominated by direction/HA/VWAP condition failures and a large median price basis (-222.05), so the historical Tai rerun remains gated.

Closed-mode port trades: 33; exact=1, +/-1=3, +/-2=4, +/-3=4; 21 owner entries had no same-direction port entry within +/-10 bars.

Median port-minus-owner entry price difference for +/-3-bar matches: -222.05.

## Offset histogram (-10..+10 bars)

```text
{"-1": 1, "-10": 0, "-2": 0, "-3": 0, "-4": 0, "-5": 1, "-6": 1, "-7": 0, "-8": 0, "-9": 0, "0": 1, "1": 1, "10": 0, "2": 1, "3": 0, "4": 0, "5": 0, "6": 0, "7": 2, "8": 0, "9": 2}
```

## Unmatched owner conditions

The first 10 unmatched owner rows are recorded in the JSON. Direction, VWAP and HA-state failures recur; some rows also fail EMA, ADX/DI, risk or cooldown. The unchanged port has no separate no-chase predicate.

## Reproducibility and safety

Pod command exited 0 after replaying 4,013 full-warm-up NSXUSD 3-minute bars. Pod artifact SHA-256: match `5707a8a82836b82faff0328b960e43de48b7ade55d8515e74f69e32a7371e626`; report `8c674c830f7dc433ed3ee40a3e016da93328f86eb3f4a59ad478fdcb5afff10c`.

Safety counters are all zero/false; no broker, order, holdout, lake-write, production-state, tuning or strategy-search activity.
