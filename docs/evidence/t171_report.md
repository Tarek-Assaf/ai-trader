# T-171 condition polarity and cross timing

**Verdict:** polarity problem no (conditions with opposite >= 21: none); silent-signal problem yes (conditions with none >= 21: direction); owner enters 1 of 31 within 2 bars of a same-direction cross; mirror gives 0 of 31 within +/-3 (baseline 4). The target is not met; the numbers point to signal or entry-rule divergence rather than a broad polarity inversion.

## Three-way split at owner bars

{
  "-1": {
    "direction": {
      "same": 0,
      "opposite": 0,
      "none": 31
    },
    "ha_state": {
      "same": 8,
      "opposite": 7,
      "none": 16
    },
    "vwap_side": {
      "same": 22,
      "opposite": 7,
      "none": 2
    },
    "ema_filter": {
      "same": 25,
      "opposite": 4,
      "none": 2
    },
    "adx_di": {
      "same": 20,
      "opposite": 2,
      "none": 9
    }
  },
  "0": {
    "direction": {
      "same": 0,
      "opposite": 0,
      "none": 31
    },
    "ha_state": {
      "same": 8,
      "opposite": 7,
      "none": 16
    },
    "vwap_side": {
      "same": 18,
      "opposite": 11,
      "none": 2
    },
    "ema_filter": {
      "same": 24,
      "opposite": 5,
      "none": 2
    },
    "adx_di": {
      "same": 19,
      "opposite": 3,
      "none": 9
    }
  },
  "1": {
    "direction": {
      "same": 0,
      "opposite": 0,
      "none": 31
    },
    "ha_state": {
      "same": 8,
      "opposite": 7,
      "none": 16
    },
    "vwap_side": {
      "same": 20,
      "opposite": 9,
      "none": 2
    },
    "ema_filter": {
      "same": 25,
      "opposite": 4,
      "none": 2
    },
    "adx_di": {
      "same": 21,
      "opposite": 2,
      "none": 8
    }
  }
}

## Cross timing

Median same-direction cross age: 46.5; histogram: {"0": 0, "1-2": 1, "11-20": 0, "21-60": 3, "3-5": 0, "6-10": 0, "none": 27}.
Per-entry same and opposite cross ages are in the JSON artifact.

## Mirror re-score

{
  "baseline": {
    "exact": 1,
    "within_1": 3,
    "within_2": 4,
    "within_3": 4
  },
  "mirror": {
    "exact": 0,
    "within_1": 0,
    "within_2": 0,
    "within_3": 0
  }
}

Safety counters are all zero/false; no broker, orders, holdout, lake-write, production-state, tuning or strategy-search activity.
