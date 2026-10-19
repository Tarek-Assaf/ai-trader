# T-184 historical Tai port 3m diagnostic

**Verdict:** diagnostic only. The unchanged T-162 Tai port was replayed through `scripts/lab_t180_hist_tai.py` on the configured six-year TRAINING range. This does not count as a Gate 1 pass and does not feed trial, ledger or promotion state.

Label: `port of the stored indicator, loosely matched to the owner (3/31 within 3 bars, 15/31 within 30 bars)`

Training range: `2020-10-17` through `2026-10-17` (end exclusive), with seven days of warm-up. The sealed-holdout guard was set to `2026-10-17T04:00:00Z` and refused any end beyond that boundary.

Owner window: `2026-10-06` through `2026-10-17` (end exclusive, New York dates).

## Full TRAINING range — configured costs

| Year | Instrument | Trades | Win rate | Avg R | Profit factor | Max DD (R) | Net R |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2020 | NSXUSD | 234 | 16.2% | -0.891 | 0.287 | 211.691 | -208.534 |
| 2021 | NSXUSD | 994 | 20.0% | -1.306 | 0.252 | 1303.191 | -1298.456 |
| 2022 | NSXUSD | 1025 | 17.9% | -1.266 | 0.247 | 1302.025 | -1297.891 |
| 2023 | NSXUSD | 887 | 17.4% | -4.161 | 0.080 | 3693.825 | -3691.221 |
| 2024 | NSXUSD | 958 | 19.7% | -2.104 | 0.173 | 2015.800 | -2015.800 |
| 2025 | NSXUSD | 952 | 20.1% | -0.787 | 0.379 | 753.989 | -748.770 |
| 2026 | NSXUSD | 701 | 21.5% | -0.328 | 0.616 | 242.405 | -230.224 |
| **Overall** | **NSXUSD** | **5751** | **19.2%** | **-1.650** | **0.210** | **9506.001** | **-9490.896** |
| 2020 | XAUUSD | 223 | 10.8% | -3.467 | 0.047 | 773.148 | -773.148 |
| 2021 | XAUUSD | 1100 | 11.7% | -5.344 | 0.033 | 5879.922 | -5878.395 |
| 2022 | XAUUSD | 1035 | 11.1% | -4.395 | 0.039 | 4548.361 | -4548.361 |
| 2023 | XAUUSD | 934 | 12.7% | -4.864 | 0.038 | 4544.553 | -4542.812 |
| 2024 | XAUUSD | 1095 | 11.3% | -4.317 | 0.044 | 4727.061 | -4727.061 |
| 2025 | XAUUSD | 1070 | 16.7% | -1.279 | 0.218 | 1371.159 | -1368.819 |
| 2026 | XAUUSD | 839 | 19.4% | -0.459 | 0.488 | 404.166 | -384.884 |
| **Overall** | **XAUUSD** | **6296** | **13.5%** | **-3.530** | **0.066** | **22233.118** | **-22223.481** |

## Full TRAINING range — zero-cost diagnostic

| Year | Instrument | Trades | Win rate | Avg R | Profit factor | Max DD (R) | Net R |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2020 | NSXUSD | 234 | 25.6% | 0.350 | 1.837 | 16.000 | 82.000 |
| 2021 | NSXUSD | 994 | 24.2% | 0.347 | 1.913 | 13.000 | 345.000 |
| 2022 | NSXUSD | 1025 | 22.3% | 0.273 | 1.688 | 12.000 | 280.000 |
| 2023 | NSXUSD | 887 | 23.8% | 0.331 | 1.867 | 11.000 | 294.000 |
| 2024 | NSXUSD | 958 | 24.9% | 0.379 | 2.025 | 10.000 | 363.000 |
| 2025 | NSXUSD | 952 | 23.4% | 0.329 | 1.879 | 14.000 | 313.000 |
| 2026 | NSXUSD | 701 | 23.5% | 0.315 | 1.807 | 13.000 | 221.000 |
| **Overall** | **NSXUSD** | **5751** | **23.8%** | **0.330** | **1.860** | **16.000** | **1898.000** |
| 2020 | XAUUSD | 223 | 18.8% | 0.193 | 1.518 | 7.000 | 43.000 |
| 2021 | XAUUSD | 1100 | 26.0% | 0.425 | 2.194 | 9.000 | 467.000 |
| 2022 | XAUUSD | 1035 | 23.1% | 0.300 | 1.766 | 13.000 | 311.000 |
| 2023 | XAUUSD | 934 | 25.5% | 0.399 | 2.094 | 12.000 | 373.000 |
| 2024 | XAUUSD | 1095 | 23.1% | 0.332 | 1.922 | 13.000 | 364.000 |
| 2025 | XAUUSD | 1070 | 22.3% | 0.266 | 1.660 | 13.000 | 285.000 |
| 2026 | XAUUSD | 839 | 22.5% | 0.280 | 1.708 | 18.000 | 235.000 |
| **Overall** | **XAUUSD** | **6296** | **23.6%** | **0.330** | **1.873** | **18.000** | **2078.000** |

## Owner-window result

| Cost mode | Instrument | Trades | Win rate | Avg R | Profit factor | Max DD (R) | Net R |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| real | NSXUSD | 22 | 36.4% | 0.469 | 1.936 | 6.185 | 10.318 |
| real | XAUUSD | 0 | not exposed | not exposed | not exposed | 0.000 | 0.000 |
| zero | NSXUSD | 22 | 40.9% | 1.000 | 5.400 | 2.000 | 22.000 |
| zero | XAUUSD | 0 | not exposed | not exposed | not exposed | 0.000 | 0.000 |

## Interpretation

- With the real configured costs, both instruments are negative over the full training range; the cost-free replay is a diagnostic counterfactual only.
- XAUUSD had 3m rows in this lake and therefore is reported, rather than treated as a no-row case.
- Metrics use completed trades from the script output, in chronological entry order. Profit factor is gross positive R divided by absolute negative R; max drawdown is the peak-to-trough drawdown of cumulative R.
- The owner-window result is reported separately and is not substituted for the full training result.

## Safety and provenance

```json
{"broker_calls": 0, "gate1_evaluated": false, "lake_writes": 0, "orders": 0, "production_state_writes": 0, "sealed_holdout_read": false, "tuning": false}
```

Pod artifact SHA-256 values are recorded in `t184_summary.json`; the Pod checkout head was `617769f0d929eb868e4310e19b5b22a0734a5366`. No broker calls, orders, holdout reads, lake writes, production-state writes or tuning occurred.
