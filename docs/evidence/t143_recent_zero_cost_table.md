# T-143 recent zero-cost diagnostic

The main checkpoint reached 8,890 rows and four disjoint XAUUSD 1m tails added the remaining 590 rows. The merged result contains **9,480 unique strategies**, exactly matching the expected candidate count. No Gate 1 evaluation, broker calls, orders, or sealed-holdout reads were performed.

| Market | Timeframe | Rebuilt | Zero-cost avg | Normal-cost avg | Zero-cost profitable | Normal-cost profitable | Total trades |
|---|---:|---:|---:|---:|---:|---:|---:|
| NSXUSD | 60m | 2,370 | -3.7152 | -7.7152 | 738 | 704 | 8,945 |
| NSXUSD | 1m | 2,370 | -0.3897 | -4.3897 | 900 | 740 | 9,808 |
| XAUUSD | 60m | 2,370 | -1.6017 | -3.2017 | 891 | 809 | 8,827 |
| XAUUSD | 1m | 2,370 | 1.4145 | -0.1855 | 655 | 486 | 2,332 |

The Pod merged result SHA-256 is `45fddf384ee78288f3f9c514046f7e055ed96eaa02c278a6a7976923996e84bf`.
