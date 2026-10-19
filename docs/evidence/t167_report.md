# T-167 dashboard count comparison

**Verdict:** The NSXUSD port produced no trades in either Oct 13–15 two-day window. In the owner trade window (Oct 6–16 NY time), it produced 33 closed trades: 14 SL, 7 TP3, 12 BE, no TP1/TP2, 21.21% win rate excluding BE, and +21.00R. This is materially below the owner dashboard references (CFD: 53 trades; NQ1: 115 trades), so the historical Tai rerun remains gated pending owner review; no count or outcome-mix equivalence is claimed.

| Window | Mode | Trades | SL | TP1 | TP2 | TP3 | BE | Win rate (completed) | Total R |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ny_midnight_13_15 | closed | 0 | 0 | 0 | 0 | 0 | 0 | 0.00% | 0.00 |
| ny_midnight_13_15 | forming | 0 | 0 | 0 | 0 | 0 | 0 | 0.00% | 0.00 |
| utc_midnight_13_15 | closed | 0 | 0 | 0 | 0 | 0 | 0 | 0.00% | 0.00 |
| utc_midnight_13_15 | forming | 0 | 0 | 0 | 0 | 0 | 0 | 0.00% | 0.00 |
| owner_trade_window | closed | 33 | 14 | 0 | 0 | 7 | 12 | 21.21% | 21.00 |
| owner_trade_window | forming | 0 | 0 | 0 | 0 | 0 | 0 | 0.00% | 0.00 |
| nq1_reference_window | closed | 33 | 14 | 0 | 0 | 7 | 12 | 21.21% | 21.00 |
| nq1_reference_window | forming | 0 | 0 | 0 | 0 | 0 | 0 | 0.00% | 0.00 |

## Reference dashboards

- CFD dashboard (Q-068): 53 trades, 12 SL, 15 TP1, 9 TP2, 17 TP3, 77.36% win rate.
- NQ1 dashboard (Q-067): 115 trades, 38 SL, 12 TP2, 32 TP3; TP1 was hidden and is not guessed here.
- The NQ1 window is evaluated only against the NSXUSD data available to the port; no futures bars are claimed.

## Reproducibility and safety

Pod command: `timeout 1200s /app/.venv/bin/python scripts/lab_t167_counts.py --lake /workspace/data/lake --owner-file docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv --output /tmp/t167_counts.json --report /tmp/t167_report.md`; exit code 0.


Safety counters are all zero/false: no broker calls, orders, sealed holdout read, lake writes, production-state writes, tuning, or strategy search activity.

