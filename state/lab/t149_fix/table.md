# T-149 recorded Tai Smart Filter diagnostic

Diagnostic only on the 10-11 observed October 2026 days (symbol-specific). Indicators use bid/ask mid prices; fills use the real side and recorded spread. 60m is omitted because the window is too short.

| Group | Trades | Trades/day | Any target | Positive real result | Real net points | Real total R | Max DD points | Recorded median spread | Assumed cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NAS100:1 | 42 | 3.818 | 0.381 | 0.381 | -39.671 | -14.000 | 92.634 | 1.800 | 4.000 |
| NAS100:3 | 27 | 2.455 | 0.333 | 0.333 | -153.357 | -1.667 | 206.345 | 1.800 | 4.000 |
| NAS100:5 | 28 | 2.545 | 0.393 | 0.393 | -188.176 | -2.667 | 237.675 | 1.800 | 4.000 |
| XAUUSD:1 | 59 | 5.900 | 0.254 | 0.254 | -42.826 | -28.333 | 42.826 | 0.500 | 1.600 |
| XAUUSD:3 | 30 | 3.000 | 0.467 | 0.467 | 14.313 | -2.333 | 13.866 | 0.500 | 1.600 |
| XAUUSD:5 | 20 | 2.000 | 0.450 | 0.450 | -5.859 | -4.667 | 15.109 | 0.500 | 1.600 |

No owner trade list was present under docs/owner; no trade matching was invented.
Recorded median spreads are shown beside the assumed lab round-trip costs; older results used the higher assumed costs.

Safety flags: broker calls 0, orders 0, sealed holdout read false, Gate 1 evaluated false.
