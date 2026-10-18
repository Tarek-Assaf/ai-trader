# T-161 corrected-clock variant rerun

**Owner verdict:** The best registered switch is `forming_wilder_sma_ema_cme`, passing **9/31** entries somewhere within ±3 three-minute bars. It does not reach the 20/31 stop threshold; no new switches or tuning were attempted.

Generated: `2026-10-18T22:02:14.035171+00:00`. Owner timestamps were parsed as `America/New_York` and converted to UTC in comparison readers only.

## Registered switch counts (mid price)

| Variant | Center all-pass | Any pass in ±3 bars |
| --- | ---: | ---: |
| `closed_simple_sma_ema_utc` | 0 | 0 |
| `closed_wilder_firstclose_ema_utc` | 1 | 1 |
| `closed_wilder_sma_ema_cme` | 1 | 2 |
| `closed_wilder_sma_ema_riyadh` | 1 | 3 |
| `closed_wilder_sma_ema_utc` | 1 | 1 |
| `closed_wilder_sma_ema_utc_shift_minus1` | 0 | 1 |
| `closed_wilder_sma_ema_utc_shift_plus1` | 0 | 1 |
| `forming_wilder_sma_ema_cme` | 3 | 9 |
| `forming_wilder_sma_ema_utc` | 1 | 2 |

## Price bases

| Basis | Center all-pass | Any pass in ±3 bars |
| --- | ---: | ---: |
| ask | 1 | 2 |
| bid | 1 | 2 |
| mid | 1 | 2 |

## First-failure counts for the baseline

- `adx_di`: 2
- `dont_chase`: 4
- `ema_side`: 3
- `ha_state`: 19
- `missing_bar`: 1
- `pass`: 1
- `risk_atr`: 0
- `vwap_side`: 1

## T-158 Heikin-Ashi categories

{
  "flip_current_bar": 7,
  "missing": 0,
  "opposite_colour": 9,
  "same_colour_no_flip": 15
}

The report includes the last eight complete 54m HA candles before each of the first five owner entries, plus the owner's NQ1 price and the recorded NAS100 mid close at that minute.

## Coverage and safety

The bounded lake check covers NSXUSD and XAUUSD at 1m, 3m, 5m, 54m and 60m where data is present; 1m is mandatory.

The Pod produced 5670 replay rows. The committed CSV is a compact center-bar baseline index (30 rows); the full artifact SHA-256 is `635db718e01bbf79c4dc06789462c74e6b6b890cff054394430001ae77a755c7`.

{
  "broker_calls": 0,
  "cost_ledger_promotion_writes": 0,
  "gate_evaluated": false,
  "lake_writes": 0,
  "orders": 0,
  "production_state_writes": 0,
  "sealed_holdout_read": false
}
