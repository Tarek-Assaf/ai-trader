# T-162 faithful Pine-port diagnostic

**Owner verdict:** The faithful Pine port reproduces **1/31** entries within **+/-2 three-minute bars** in `closed` mode. The 25/31 success bar is not met; per-entry comparison stops here without tuning or a historical rerun.

Generated: `2026-10-18T22:21:43.250890+00:00`. Owner timestamps were parsed as `America/New_York`.

## Entry reproduction

| HA mode | Exact | +/-1 bar | +/-2 bars |
| --- | ---: | ---: | ---: |
| closed | 1 | 1 | 1 |
| forming | 0 | 0 | 0 |

## Port contract

- Setup uses the Pine Open/Close source series.
- Refresh entries intentionally do not require the Heikin Ashi state gate.
- Management starts on the entry bar.
- Intrabar ordering is target-first (TP3, TP2, TP1/BE before stop).
- VWAP is volume-weighted when volume exists, otherwise equal typical-price weighting is used for zero-volume recorder bars.

## Exit comparison source

Owner exit rows parsed: **54**. Port trades include exit time/type where a trade completed; open trades remain explicit.

## Safety

```json
{"broker_calls":0,"orders":0,"sealed_holdout_read":false,"lake_writes":0,"production_state_writes":0,"gate_evaluated":false}
```

## Per-entry differences

The Pod artifact compared all 31 owner entries in both modes and recorded the nearest port entry, signed delta in minutes, direction, and first differing rule for each missing entry. The registered port is diagnostic only; because the best result is below 25/31, no switch tuning or historical rerun was attempted.

## Pod run

- Pod command exited `0`.
- Result timestamp: `2026-10-18T22:21:43.250890+00:00`.
- Closed mode: 1 exact / 1 within +/-1 / 1 within +/-2.
- Forming mode: 0 exact / 0 within +/-1 / 0 within +/-2.
- No broker calls, orders, sealed-holdout reads, lake writes, production-state writes, or gate evaluation.
