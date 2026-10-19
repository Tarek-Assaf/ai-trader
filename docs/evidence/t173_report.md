# T-173 Heikin-Ashi timeframe multiplier diagnostic

**Verdict:** a different tfmult explains it yes; best variant 9, epoch, closed: owner enters 6 of 31 within 2 bars of a same-direction cross; matched exact/+/-1/+/-2/+/-3 = 1/4/5/5 of 31 (baseline 1/3/4/4); any variant >=25 of 31 within +/-2: no.

The registered grid contains 52 replay-only variants (NSXUSD 3m owner window with full warm-up). This gain is below 15/31 and is noise across 52 variants; it is not a finding. The next likely cause is Renko/setupType, a different data source (NQ1! futures versus NSXUSD CFD bars), or a different Pine version; no further variant was tested.

## Best result per tfmult

```text
[
  {
    "tfmult": 5,
    "best_anchor": "epoch",
    "best_mode": "closed",
    "within_2_cross": 6,
    "within_2_matches": 2,
    "within_3_matches": 2
  },
  {
    "tfmult": 9,
    "best_anchor": "epoch",
    "best_mode": "closed",
    "within_2_cross": 6,
    "within_2_matches": 5,
    "within_3_matches": 5
  },
  {
    "tfmult": 3,
    "best_anchor": "epoch",
    "best_mode": "closed",
    "within_2_cross": 4,
    "within_2_matches": 3,
    "within_3_matches": 3
  },
  {
    "tfmult": 18,
    "best_anchor": "ny_midnight",
    "best_mode": "forming",
    "within_2_cross": 4,
    "within_2_matches": 5,
    "within_3_matches": 5
  },
  {
    "tfmult": 6,
    "best_anchor": "ny_midnight",
    "best_mode": "closed",
    "within_2_cross": 3,
    "within_2_matches": 5,
    "within_3_matches": 5
  },
  {
    "tfmult": 10,
    "best_anchor": "epoch",
    "best_mode": "closed",
    "within_2_cross": 3,
    "within_2_matches": 4,
    "within_3_matches": 4
  },
  {
    "tfmult": 12,
    "best_anchor": "epoch",
    "best_mode": "closed",
    "within_2_cross": 3,
    "within_2_matches": 2,
    "within_3_matches": 2
  },
  {
    "tfmult": 15,
    "best_anchor": "ny_midnight",
    "best_mode": "forming",
    "within_2_cross": 3,
    "within_2_matches": 2,
    "within_3_matches": 2
  },
  {
    "tfmult": 20,
    "best_anchor": "epoch",
    "best_mode": "forming",
    "within_2_cross": 1,
    "within_2_matches": 5,
    "within_3_matches": 5
  },
  {
    "tfmult": 24,
    "best_anchor": "epoch",
    "best_mode": "forming",
    "within_2_cross": 1,
    "within_2_matches": 2,
    "within_3_matches": 2
  },
  {
    "tfmult": 30,
    "best_anchor": "ny_midnight",
    "best_mode": "forming",
    "within_2_cross": 1,
    "within_2_matches": 2,
    "within_3_matches": 2
  },
  {
    "tfmult": 36,
    "best_anchor": "epoch",
    "best_mode": "forming",
    "within_2_cross": 0,
    "within_2_matches": 4,
    "within_3_matches": 4
  },
  {
    "tfmult": 60,
    "best_anchor": "ny_midnight",
    "best_mode": "forming",
    "within_2_cross": 0,
    "within_2_matches": 4,
    "within_3_matches": 4
  }
]
```

Full per-variant cross timing, matches, direction splits and safety counters are in the JSON artifact.

Safety counters are all zero/false; no broker, orders, holdout, lake-write, production-state, tuning or strategy-search activity.
