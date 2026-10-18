# T-146 movement matching report

Diagnostic only. No broker, orders, paper trading, holdout, Gate 1, ledger or promotion activity.

Generated: `2026-10-17T11:02:46.825410+00:00`
In-sample: `2022-01-01T00:00:00+00:00` to `2025-01-01T00:00:00+00:00`
Out-of-sample: `2025-01-01T00:00:00+00:00` to `2026-07-01T00:00:00+00:00`

Each result uses past-only ATR-normalised windows, DTW and a correlation baseline, with a random control matched for count and time spread.
Net movement applies the cost to a stated trade direction; an adverse move is charged the cost rather than made less negative. OOS verdicts also require the query's own realized move to be positive after following the direction predicted by its past matches.

## Groups

- **NSXUSD 60m**: 0 in-sample windows, 0 OOS queries, 0 evaluated under the query cap, 18 result rows in 0.371s; charts: `/workspace/state/t153_runner/groups/nsxusd_60m/t146_charts/nsxusd_60m`
  - length=30 method=dtw horizon=6: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=30 method=dtw horizon=12: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=30 method=dtw horizon=30: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=30 method=correlation horizon=6: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=30 method=correlation horizon=12: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=30 method=correlation horizon=30: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=60 method=dtw horizon=6: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=60 method=dtw horizon=12: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=60 method=dtw horizon=30: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=60 method=correlation horizon=6: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=60 method=correlation horizon=12: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=60 method=correlation horizon=30: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=120 method=dtw horizon=6: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=120 method=dtw horizon=12: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=120 method=dtw horizon=30: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=120 method=correlation horizon=6: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=120 method=correlation horizon=12: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  - length=120 method=correlation horizon=30: trades=0, win_rate=None, mean_net_points=None, control_mean_net_points=None, edge_points=None, sign_test_p=None, verdict=NOT_FOUND
  Multiple-testing verdicts: NOT_FOUND; query stride=1; Bonferroni alpha is recorded per row.

A movement is a candidate only when its out-of-sample net-of-cost result beats the matched random control after the pre-registered trial correction. This diagnostic does not promote a strategy.
