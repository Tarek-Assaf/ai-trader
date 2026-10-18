# T-164 clock/price alignment

**Verdict:** The best constant alignment is **+3 minutes** using the recorded 1-minute **open** price after removing a median **221.05-point** NQ1/NAS100 basis. The residual is 6.30 points across 30 samples and the fixed-seed 2,000-permutation p-value is **0.0005**, so zero shift is rejected. The per-entry best shifts are widely dispersed (from -330 to +161 minutes), so no single clock offset explains every trade. The shifted T-162 port matches **4/31** owner entries within +/-2 three-minute bars, below the 25/31 acceptance threshold. Bar-close labeling gives 9 all-four filter passes versus 7 for bar-open, but does not establish a valid chart convention. The owner should supply a chart-bar CSV export or Pine Inputs/Style screenshot.

## Scan

- Owner entries: 31, parsed as America/New_York.
- Recorded bars: NSXUSD 1m mid; shifts -360..+360 minutes in one-minute steps.
- Price bases: open, close and HLC3; each shift removes one median constant basis.
- Permutation test: 2,000 permutations, seed 164.

## Best and nearby alignments

```json
[
  {
    "shift_minutes": 3,
    "basis": "open",
    "basis_offset": 221.0500000000029,
    "median_abs_residual": 6.299999999995634,
    "samples": 30
  },
  {
    "shift_minutes": 2,
    "basis": "close",
    "basis_offset": 221.32500000000073,
    "median_abs_residual": 6.475000000002183,
    "samples": 30
  },
  {
    "shift_minutes": 2,
    "basis": "hlc3",
    "basis_offset": 223.15000000000146,
    "median_abs_residual": 7.983333333333576,
    "samples": 30
  },
  {
    "shift_minutes": 3,
    "basis": "hlc3",
    "basis_offset": 225.81666666666752,
    "median_abs_residual": 8.56666666665697,
    "samples": 30
  },
  {
    "shift_minutes": 4,
    "basis": "open",
    "basis_offset": 229.10000000000218,
    "median_abs_residual": 9.225000000002183,
    "samples": 30
  },
  {
    "shift_minutes": -2,
    "basis": "hlc3",
    "basis_offset": 226.6166666666686,
    "median_abs_residual": 9.39999999999418,
    "samples": 29
  }
]
```

## Per-entry shifts

Best basis: open. Shifts in owner-entry order: `-41, -206, -199, 74, 45, -1, -15, -16, 75, 121, 26, 2, -121, -330, 24, -180, -280, -304, -123, 6, -113, -12, -282, -4, -75, -57, -89, 161, -58, 13, -64`. The values are dispersed rather than a common offset; see `t164_entries.csv`.

## Filters at selected shift

```json
{
  "ema": 26,
  "adx_di": 22,
  "vwap_side": 24,
  "vwap_distance": 13,
  "all_four": 7,
  "rows_with_bar": 30
}
```

## Bar-label convention

```json
{
  "owner_time_is_bar_open": {
    "ema": 26,
    "adx_di": 22,
    "vwap_side": 24,
    "vwap_distance": 13,
    "all_four": 7,
    "rows_with_bar": 30
  },
  "owner_time_is_bar_close": {
    "ema": 24,
    "adx_di": 19,
    "vwap_side": 23,
    "vwap_distance": 21,
    "all_four": 9,
    "rows_with_bar": 29
  }
}
```

## Coverage

```json
{
  "NSXUSD": {
    "1m": 12350,
    "3m": 4013,
    "5m": 2355,
    "60m": 118
  },
  "XAUUSD": {
    "owner_rows": 0,
    "note": "no owner rows in this window"
  }
}
```

## Safety

```json
{
  "broker_calls": 0,
  "orders": 0,
  "sealed_holdout_read": false,
  "lake_writes": 0,
  "production_state_writes": 0,
  "gate_evaluated": false,
  "tuning": false
}
```

The Pod run was diagnostic-only: no broker calls, orders, sealed holdout reads, lake writes, production-state writes, gate evaluation or tuning.

## Pod artifact hashes

The complete Pod files were hashed in place; compact local evidence records their SHA-256 values in `t164_shifts.json`.
