# T-201 cost per trade in R diagnostic

Status: **blocked**.

The authoritative T-201 diagnostic requires a six-year TRAINING mirror-control
scan for NSXUSD and XAUUSD at 1m, 3m, and 5m. That scan needs the RunPod data
lake and runtime dependencies. The owner-provided Pod terminal URL returned
HTTP 404 during the bounded read-only check (`B-033/Q-076`), and this desktop
checkout has no `/workspace/data/lake` plus no installed `sqlalchemy`/`pytest`
runtime. No broker, order, holdout, config, redeploy, or secret operation was
attempted.

## Observable offline inputs

* `docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv`
  contains 108 TradingView rows and 54 distinct trade numbers (entry and exit
  rows). This does not match the specification's description of a 31-trade
  owner comparison, so no unverified 31-trade result is asserted.
* `docs/evidence/owner/tai_smart_filter_pine_source.txt` was inspected. The
  source defines ATR-based risk (`tradeATR = ta.atr(14)`, an ATR stop multiplier,
  and a maximum-risk-in-ATR filter at lines 498-554), but no explicit minimum
  stop-point floor or bar-range filter was found in the required risk section.
* `config/strategy_lab.yaml` supplies configured costs of 4 points for NSXUSD
  and 1.6 points for XAUUSD. `config/instruments.yaml` leaves tick-size and
  minimum-stop values null for both instruments, so T-200 and minimum-stop
  subsets cannot be claimed from local configuration.

## Unavailable outputs

The six percentile cells, >0.25R/>1R shares, net-R all-trade totals, T-200
spread-guard subset, minimum-stop subset, and any 2700-strategy gap comparison
were not run. They require the missing training lake and a reachable Pod
session. This report deliberately records the blocker rather than fabricating
distribution values.

## Reproduction after the blocker is cleared

Run `python scripts/lab_t201_cost_in_r.py --output-dir docs/evidence/t201`
inside the project runtime with the training lake mounted. The script is
read-only and writes only the requested report and summary evidence.
