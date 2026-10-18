# T-155 Tai indicator parity

Diagnostic-only; recorded NAS100 data only. No broker, holdout, gate, cost, ledger, promotion, or lake writes.

## Independent reference comparisons

| Indicator | Compared | Max absolute difference | Median absolute difference | Missing actual | Missing reference |
|---|---:|---:|---:|---:|---:|
| ema200 | 3775 | 0.0 | 0.0 | 0 | 0 |
| atr14 | 3961 | 0.0 | 0.0 | 0 | 0 |
| adx14 | 3948 | 0.0 | 0.0 | 0 | 0 |
| plus_di14 | 3961 | 0.0 | 0.0 | 0 | 0 |
| minus_di14 | 3961 | 0.0 | 0.0 | 0 | 0 |
| Heikin Ashi open | 3974 | 0.0 | 0.0 | 0 | 0 |
| Heikin Ashi close | 3974 | 0.0 | 0.0 | 0 | 0 |

TA-Lib/pandas-ta availability: unavailable in the Pod image.

## VWAP sensitivity

| VWAP mode | Entries evaluated | All filters passed | Failing conditions |
|---|---:|---:|---|
| utc_equal | 31 | 0 | HA 20, EMA 12, ADX/DI 21, VWAP side 14, dont-chase 20, risk/ATR 9 |
| cme_equal | 31 | 0 | HA 20, EMA 12, ADX/DI 21, VWAP side 13, dont-chase 23, risk/ATR 9 |
| cme_tick_volume | 31 | 0 | HA 20, EMA 12, ADX/DI 21, VWAP side 13, dont-chase 23, risk/ATR 9 |

## Remaining unverifiable items

- TradingView VWAP session anchor and real NQ1! volume
- 54m session anchoring
- futures-vs-CFD data

## Safety

{"broker_calls": 0, "cost_ledger_promotion_writes": 0, "gate1_evaluated": false, "lake_writes": 0, "orders": 0, "sealed_holdout_read": false}
