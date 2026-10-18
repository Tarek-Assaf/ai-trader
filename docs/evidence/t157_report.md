# T-157 Tai rule-by-rule replay diagnostic

Generated from the owner’s 31-entry CSV and the recorded NAS100 Capital.com 1m lake on the Pod. The diagnostic compares entry **times and directions** only; it never compares the owner’s NQ1! price to the NAS100 CFD price.

## Baseline and timing result

- Mid-price baseline (`forming_wilder_sma_ema_utc`) center-bar passes: **0/31**.
- Mid-price baseline passes somewhere in ±3 bars: **0/31**.
- Best registered switch: `closed_simple_sma_ema_utc`, **1/31** entries passing somewhere in ±3 bars.
- Acceptance stop rule applies because no registered combination reaches 20/31; no tuning was performed.

## Baseline first-failure counts

The first failing rule is evaluated in indicator order: `ha_state`, `ema_side`, `adx_di`, `vwap_side`, `dont_chase`, `risk_atr`.

- `ha_state`: 20
- `ema_side`: 3
- `adx_di`: 3
- `vwap_side`: 0
- `dont_chase`: 1
- `risk_atr`: 0
- `missing_bar`: 4

The committed CSV is a compact center-bar index for the baseline. The complete Pod artifact contains 5,104 rows across mid/bid/ask bases, nine registered switches and offsets -3..+3; rerun `scripts/lab_t157_rule_by_rule.py` to regenerate it.

## Switch counts on mid-price data

| Variant | Center all-pass | Any pass in ±3 bars |
| --- | ---: | ---: |
| `closed_wilder_sma_ema_utc` | 0 | 0 |
| `forming_wilder_sma_ema_utc` | 0 | 0 |
| `closed_wilder_sma_ema_riyadh` | 0 | 0 |
| `closed_wilder_sma_ema_cme` | 0 | 0 |
| `forming_wilder_sma_ema_cme` | 0 | 0 |
| `closed_simple_sma_ema_utc` | 0 | 1 |
| `closed_wilder_firstclose_ema_utc` | 0 | 0 |
| `closed_wilder_sma_ema_utc_shift_minus1` | 0 | 0 |
| `closed_wilder_sma_ema_utc_shift_plus1` | 0 | 0 |

## Price-basis baseline counts

| Basis | Center all-pass | Any pass in ±3 bars |
| --- | ---: | ---: |
| mid | 0 | 0 |
| bid | 0 | 0 |
| ask | 0 | 0 |

## Suspected implementation differences tested

- 54m Heikin Ashi: closed versus forming values, with UTC and CME-open grouping.
- VWAP: UTC midnight, Riyadh midnight, and CME-open session resets.
- ADX/DI smoothing: production Wilder smoothing versus simple rolling windows.
- EMA seed: production complete-SMA seed versus first-close seed.
- Bar timestamp convention: center entry bar shifted one 3m bar earlier or later.

Pod evidence: CSV SHA-256 `b365f36b34199c1b21a2806f4fd541e98c04ff3236bc2aa2fdb82f97230e81a1`; report SHA-256 `d22698d39eef079f25c1e50d0561496c7dee2d4fc762bb64a3a03f16e53756df`.

Safety: broker calls 0, orders 0, sealed holdout read false, and no gate, cost, ledger, promotion or lake writes.
