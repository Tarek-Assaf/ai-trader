# T-201 configured cost per trade in R

- Status: **ok**; offline six-cell TRAINING scan completed on the reachable Pod.
- Holdout was excluded; safety fields are all zero or false.

| Instrument | TF | Trades | Median cost/R | P05 | P95 | Cost>0.25R | Cost>1R | Configured net R | Configured R/trade | Zero-cost R/trade |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NSXUSD | 1m | 10199 | 0.662073 | 0.146334 | 5.504102 | 0.8446 | 0.3420 | -26119.915 | -2.561 | 0.277 |
| NSXUSD | 3m | 5486 | 0.355149 | 0.086405 | 3.245134 | 0.6343 | 0.1726 | -9295.315 | -1.694 | 0.328 |
| NSXUSD | 5m | 4001 | 0.260860 | 0.065414 | 2.640298 | 0.5139 | 0.1285 | -3681.880 | -0.920 | 0.287 |
| XAUUSD | 1m | 10656 | 2.496797 | 0.419026 | 23.659163 | 0.9863 | 0.8193 | -103883.498 | -9.749 | 0.358 |
| XAUUSD | 3m | 6022 | 1.304952 | 0.232190 | 11.321115 | 0.9432 | 0.6106 | -22101.108 | -3.670 | 0.330 |
| XAUUSD | 5m | 4331 | 0.978667 | 0.176487 | 8.291938 | 0.9109 | 0.4916 | -14204.943 | -3.280 | 0.315 |

The configured-cost result is negative in all six cells while zero-cost gross results remain positive. The cost burden is largest for XAUUSD 1m (median 2.4968R; 81.93% above 1R) and smallest for NSXUSD 5m (median 0.2609R). This is evidence only; no config or gate was changed.
