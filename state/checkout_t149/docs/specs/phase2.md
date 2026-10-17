# Phase 2 Spec: Risk and Execution (task C-003, part 2)

Author: Claude. For: Codex. Covers T-020 to T-027. Depends on Phase 1. Read `ARCHITECTURE.md` sections 5.3, 5.4 and 12.

## Principles
- The Risk Guardian and Execution Service are **plain code, no LLM**, and the only place orders originate.
- Nothing in this phase may run against a live account. `BROKER_ENV=demo` and `LIVE_TRADING_ENABLED=false` throughout.
- Every rule is a small function with a name, an input, a reason string, and tests.

## T-020: Risk Guardian
Input: a `TradeIntent` (no size), a market snapshot, account state, open positions, config. Output: `RiskDecision` (approved, approved size, reasons, rules checked). **Reject if any rule fails.** Rules and their config keys (`config/risk.yaml`):

| Rule | Reject when |
|---|---|
| `has_stop` | no stop-loss |
| `stop_distance` | stop distance outside `min_stop_atr` to `max_stop_atr` multiples of ATR |
| `reward_risk` | reward:risk below `min_reward_risk` |
| `risk_per_trade` | computed risk above `risk_per_trade_pct` of equity |
| `daily_loss` | realized plus open loss today at or above `max_daily_loss_pct` |
| `weekly_loss` | at or above `max_weekly_loss_pct` |
| `drawdown` | drawdown from equity peak at or above `max_drawdown_pct` (halts the system until manual reset) |
| `open_positions` | total or per-instrument count at limit |
| `combined_exposure` | combined NAS100 and Gold risk above `max_combined_risk_pct` |
| `margin_usage` | margin use after the trade above `max_margin_usage_pct` |
| `leverage` | effective leverage above `max_effective_leverage` |
| `market_open` | market closed, in a break, or inside the pre/post-event buffer |
| `news_blackout` | inside a high-impact news window (`blackout_before_min`, `blackout_after_min`) |
| `data_fresh` | latest data older than `stale_data_max_seconds` |
| `spread` | spread above `max_spread_atr_ratio` of ATR, or above the instrument ceiling |
| `order_rate` | orders in the last hour above `max_orders_per_hour` |
| `stage_cap` | size above the strategy's stage cap |
| `trade_quality` | quality score below `min_trade_quality_score` |
| `loss_streak` | `consecutive_loss_pause` losses in a row; pause for `loss_cooldown_minutes` |
| `kill_pause` | kill switch or pause active |

Acceptance: every rule has unit tests (pass and fail cases); **property-based tests** (hypothesis) prove no input yields an approved order without a stop or above the risk limits; Guardian decisions are written to `audit_log`.

## T-021: Position sizing (`risk/sizing.py`)
```
risk_amount   = equity * risk_per_trade_pct / 100
stop_distance = abs(entry - stop)                 # price points
size_raw      = risk_amount / (stop_distance * value_per_point_per_unit)
size          = floor(size_raw / size_step) * size_step      # always round DOWN
```
Then clamp to the broker's max size; if `size < min_size`, **reject** (never round up beyond the risk budget). Check margin and leverage. Currency: convert if the account currency differs from the instrument's quote currency. Read `value_per_point` and size rules from broker market details, not constants.
Acceptance: tests for rounding down, minimum size rejection, margin rejection, both instruments, and a currency conversion case.

## T-022: Trade quality score
Deterministic score from 0 to 1: stop distance in ATR, spread/ATR, regime fit, multi-timeframe agreement, event proximity, liquidity/session, strategy reliability. Weights in `config/risk.yaml`. Document each component and test with fixtures.

## T-023: PaperBroker
Simulated fills using the calibrated spread model (Phase 1, T-017) and slippage; supports market orders with attached stop and take-profit, partial closes, and daily financing placeholders. Same `BrokerAdapter` interface as the real adapter, with shared **contract tests**.

## T-024: Capital.com broker adapter (demo)
- Open: `POST /api/v1/positions` with epic, direction, size, `guaranteedStop` false, and stop and profit levels or distances; the response carries a `dealReference`; confirm through `GET /api/v1/confirms/{dealReference}`. Modify and close positions through the documented endpoints. Verify every call against the live docs and record results.
- Execution events are not pushed: poll `/positions`, `/workingorders` and `/history/activity`.
- **Every order carries a broker-side stop-loss.** If the stop cannot be attached, the order is not sent.
- Respect the shared rate limiter (priority P0).
- Acceptance: a demo order round trip (open with stop, modify, close) recorded in `validations`; contract tests pass.

## T-025: P&L engine
Realized and unrealized P&L, fees and financing, R-multiples, equity curve, drawdown, exposure, margin use. Reconcile with the broker account figures; mismatches over a tolerance raise an alert.

## T-026: Order manager and reconciler
- Idempotency: store a client order id and the intended order **before** sending. On timeout or error, check broker positions and activity before any retry. Never send the same intent twice.
- Leader lock in Redis for the Execution Service and Guardian (singletons).
- Reconciler every N seconds compares internal state with broker truth. On mismatch: alert, pause new entries, heal state from the broker. On startup: reconcile first, then allow trading.
- Chaos tests: duplicate trigger, Redis loss, broker timeout after the order was accepted, process kill mid-order.

## T-027: Kill switch and pause
Three independent triggers: a flag file on the volume, a Redis key, and an API endpoint. Checked before **every** order and in every loop. `pause` stops new entries but manages open positions; `kill` halts and closes everything; resuming after `kill` needs an explicit owner action. **Must work with the LLM provider unreachable.**
Acceptance: drills for kill mid-cycle, kill mid-order, and kill while a worker scale-up happens.

## Phase 2 exit criteria
Full Guardian test coverage; demo order round trip with a broker-side stop; reconciler and chaos tests pass; kill switch drills pass; invariant tests still green.
