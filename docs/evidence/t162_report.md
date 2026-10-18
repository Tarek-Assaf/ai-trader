# T-162 faithful Pine-port diagnostic

**Owner verdict:** The faithful Pine port reproduces **1/31** entries within **+/-2 three-minute bars** in `closed` mode. The 25/31 success bar is not met; per-entry comparison stops here without tuning or a historical rerun.

Generated: `2026-10-18T22:21:43.250890+00:00`. Owner timestamps were parsed as `America/New_York`.

## Entry reproduction

| HA mode | Exact | +/-1 bar | +/-2 bars | Port entries | Long | Short |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| closed | 1 | 1 | 1 | 33 | 8 | 25 |
| forming | 0 | 0 | 0 | 0 | 0 | 0 |

## Port contract

- Setup uses the Pine Open/Close source series.
- Refresh entries intentionally do not require the Heikin Ashi state gate.
- Management starts on the entry bar.
- Intrabar ordering is target-first (TP3, TP2, TP1/BE before stop).
- VWAP is volume-weighted when volume exists, otherwise equal typical-price weighting is used for zero-volume recorder bars.

## Alignment metadata

- Owner rows: **31 per HA mode** (62 CSV rows); duplicate timestamp units: **0**.
- Signal group anchor: **UTC epoch aligned 54-minute groups**; 18 three-minute bars per group.
- Recorder bars carry volume: **false** (volume values are 0.0).
- Owner-minute bar gaps recorded explicitly: **2**.

## Exit comparison source

Owner exit rows parsed: **54**. Port trades include exit time/type where a trade completed; open trades remain explicit.

## Per-entry differences

`docs/evidence/t162_entries.csv` contains one row per owner entry for both HA modes with owner time in New York and UTC, direction, nearest port entry, signed delta in three-minute bars, first differing rule, and available owner-minute OHLC/HA/EMA/ADX/DI/VWAP/ATR values. Missing bars are explicit rows rather than dropped.

## Safety

```json
{"broker_calls":0,"orders":0,"sealed_holdout_read":false,"lake_writes":0,"production_state_writes":0,"gate_evaluated":false}
```

## Pod run

- Pod command exited `0`.
- Result timestamp: `2026-10-18T22:21:43.250890+00:00`.
- Closed mode: 1 exact / 1 within +/-1 / 1 within +/-2.
- Forming mode: 0 exact / 0 within +/-1 / 0 within +/-2.
- No broker calls, orders, sealed-holdout reads, lake writes, production-state writes, or gate evaluation.
