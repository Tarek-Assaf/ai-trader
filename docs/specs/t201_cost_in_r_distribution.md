# T-201: Why every configured-cost backtest loses: cost per trade in R, by stop size (owner request)

## Why
The owner's TradingView replay of the Tai indicator gave far better numbers than our Strategy Lab, and the same gap
appeared for more than 2,700 strategies. T-194/T-198 show a small positive zero-cost result (NSXUSD 1m +2,951R over
10,694 trades, about +0.28R each) but -27,464R after configured costs (about -2.6R per trade). Configured cost is
`spread + 2 x slippage` = 4.0 points for NSXUSD (config/strategy_lab.yaml) and each trade's R is
`gross_r - cost_points / risk_points`. When the structure stop (`risk_points`) is tiny, `cost/risk` explodes
(a 0.5-point stop costs 8R), and the SUM of ratios is dominated by those trades. The owner's own 31 trades have stops of
about 21 points (first trade: 21 points, -420 USD at 20 USD per point). Hypothesis: the backtests include many tiny-stop
trades that the owner (and a real broker minimum-stop rule plus the T-200 spread guard) would never take.

## Build (read-only, offline, training window only, no network)
Reuse the T-194/T-198 trade lists (scripts/lab_t198_mirror_control.py and the T-162 port). New script
`scripts/lab_t201_cost_in_r.py`; evidence in `docs/evidence/t201/`.
1. For NSXUSD and XAUUSD at 1m/3m/5m: distribution of `risk_points` (p1, p5, p25, median, p75, p95) and of
   `cost_points / risk_points` (same percentiles, plus the share of trades with cost > 0.25R and > 1R).
2. Same table for the owner's 31 TradingView trades (docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv:
   points = |exit - entry| of the stop leg) so the two populations can be compared.
3. Net R after configured costs and at zero cost for: (a) all trades (the T-194 baseline), (b) only trades that pass the
   T-200 spread guard (cost_points <= 0.25 x risk_points... use spread only: spread_points <= max(0.25 x risk, 2 x tick)),
   (c) only trades with risk_points >= the broker minimum stop distance from config/instruments.yaml (state the value
   used; if absent say so). Report trade counts kept, net R, net R per trade, per year.
4. Check the Pine source (docs/evidence/owner/tai_smart_filter_pine_source.txt): does it have a minimum stop distance,
   ATR floor or bar-range filter that the T-162 port does not implement? Quote the lines or say none.
5. State in one plain paragraph whether the loss is (i) tiny-stop trades, (ii) a cost-model scale problem, or (iii) a real
   lack of edge, with the numbers that decide it. Also say whether the 2,700-strategy gap has the same cause (reuse
   `risk_points`/cost columns in the Strategy Lab outputs if they exist; otherwise say what is missing).

## Rules
No broker or order call, no network, no sealed holdout read, no risk/gate/strategy_lab config edits (read only), no
redeploy, no secrets. Do not change any code in src/.

## Acceptance
All five items answered with tables, ruff check/format (line 100), mypy, CI green. One log entry, hand turn to claude.
