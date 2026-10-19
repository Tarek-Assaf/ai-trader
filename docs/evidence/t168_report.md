# T-168 warm-up-preserving dashboard count rerun

**Verdict:** Full-lake warm-up changes the two-day windows from zero to 10 trades (NY midnight) and 11 trades (UTC midnight), while the owner trade window remains 33 trades. Counts remain materially below the CFD (53) and NQ1 (115) dashboards, so the historical Tai rerun remains gated pending review. The unchanged port was replayed over 4,013 complete NSXUSD 3-minute bars before filtering by entry time.

| Window | Trades | SL | TP1 | TP2 | TP3 | BE | Win rate (completed) | Total R | Trades/day |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ny_midnight_13_15 | 10 | 4 | 0 | 0 | 3 | 3 | 30.00% | 5.00 | 5.00 |
| utc_midnight_13_15 | 11 | 3 | 0 | 0 | 3 | 5 | 27.27% | 6.00 | 5.50 |
| owner_trade_window | 33 | 14 | 0 | 0 | 7 | 12 | 21.21% | 7.00 | 3.00 |
| nq1_reference_window | 33 | 14 | 0 | 0 | 7 | 12 | 21.21% | 7.00 | 3.00 |

## Reference dashboards

- CFD dashboard (Q-068): 53 trades, 12 SL, 15 TP1, 9 TP2, 17 TP3, 77.36% win rate.
- NQ1 dashboard (Q-067): 115 trades, 38 SL, 12 TP2, 32 TP3; TP1 was hidden and is not guessed.

## Forming-mode diagnostics

The forming mode still produced zero trades over the full range. Of 4,013 bars, separate pass counts at HA-cross direction are: direction 101, EMA 51, ADX/DI 33, VWAP/location 24, risk 34, and all three smart filters together 4. The unchanged port has no separate no-chase predicate; refresh is only evaluated after the exit cooldown.

## Reproducibility and safety

Pod command exited 0: `timeout 1200s /app/.venv/bin/python scripts/lab_t168_counts.py --lake /workspace/data/lake --owner-file docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv --output /tmp/t168_counts.json --report /tmp/t168_report.md`.

Pod artifact SHA-256: counts `f52866325c2a4b7f3d030fd333e78ad3ccb22946eb6e3835243f5e0a8943fa3a`; report `3a3c36b00036ef4ff396259c3331b87284d7862785e5e4f911a30986614a3ab2`.

Safety counters are all zero/false: no broker calls, orders, sealed holdout read, lake writes, production-state writes, tuning, or strategy search.
