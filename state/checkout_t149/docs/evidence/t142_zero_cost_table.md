# T-142 zero-cost diagnostic

The diagnostic removed spread and slippage from the evaluation path. Gate 1 stayed unchanged, and the original multiple-testing count stayed at 2,628. It found **zero Gate 1 passes**. The full per-strategy rows, including each strategy's three trade statistics and stored with-cost average, remain in the Pod result identified by the SHA-256 in the JSON evidence.

| Market | Timeframe | Evaluated | Missing | Total trades | Average points/trade | Best | Worst | Gate 1 passes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| NSXUSD | 60m | 347 | 0 | 134,245 | 0.0040 | 618.07 | -444.10 | 0 |
| XAUUSD | 60m | 351 | 139 | 112,564 | -0.0057 | 69.60 | -54.99 | 0 |
| NSXUSD | 5m | 347 | 135 | 324,409 | 0.3610 | 395.45 | -252.60 | 0 |
| XAUUSD | 5m | 351 | 139 | 284,535 | -0.0883 | 47.33 | -26.24 | 0 |
| NSXUSD | 3m | 212 | 0 | 384,342 | 0.4876 | 357.40 | -292.87 | 0 |
| XAUUSD | 3m | 212 | 0 | 343,651 | -0.0641 | 43.70 | -26.24 | 0 |
| NSXUSD | 1m | 55 | 55 | 0 | 0.0000 | 0.00 | 0.00 | 0 |
| XAUUSD | 1m | 55 | 55 | 0 | 0.0000 | 0.00 | 0.00 | 0 |

No broker, paper, live, gate, cost-config, or holdout changes were made.
