# Phase 8 Spec: Autonomous Demo Soak

Author: Claude. For: Codex. Read `ARCHITECTURE.md` 3, 5.2 to 5.5, 12 and 14, `docs/specs/phase4.md`, `docs/specs/phase5.md`, `config/risk.yaml`, `config/schedule.yaml`. Demo only. Nothing here may place an order outside `OrderManager`, change `risk.yaml` or `strategy_lab.yaml`, or enable live trading.

## Goal
Phases 0 to 7 built the parts. Phase 8 wires them into one process that runs unattended on the Capital.com **demo** account for at least four weeks with no safety incident, and proves it with evidence. Today the app startup wires only the control source, the Telegram runtime and the Capital client; the decision loop, the report scheduler and real command data are not connected.

## Proposed tasks (the owner of the handoff adds them with ids at the start of Phase 8)

### T-090: Runtime composition (core pod)
1. A `runtime` module started from the FastAPI lifespan when `ROLE` is `all` or `core` and `TRADING_MODE` is `paper` or `auto` with `BROKER_ENV=demo`. It builds: leader lock (`execution/leader.py`), Risk Guardian, OrderManager with the demo broker, reconciler, decision loop, report scheduler, SettingsService.
2. The decision loop runs only while it holds the Redis leader lock. Losing the lock stops new entries within one cycle and alerts (invariant 5, 8).
3. Cycle triggers from `config/schedule.yaml`: bar close on 1M/3M/5M, session open, news event. A cycle follows the 5.2 decision cycle and writes the audit trail under one `cycle_id`.
4. LLM default is the mock. With a real provider the monthly cap from the owner applies; exceeding it degrades to `NO_TRADE` and alerts (invariant 13). The usage ledger is the DB ledger, not the in-memory one used by the Telegram router today.
5. Fail safe: any component failure, stale data, reconciliation mismatch or broker error means no new entries, an alert, and automatic retry with backoff. Open positions keep their broker-side stop.
6. `/pause`, `/stop`, `/kill` keep working with Redis, the LLM or the database down (invariant 11); add a chaos test for each.

Acceptance: a replayed recorded day through the full pipeline with the mock LLM and the paper broker produces the same cycles, intents and audit rows twice (determinism); killing Redis, dropping the feed and returning broker 5xx each end in no new entries plus an alert, never an exception loop; a second core instance cannot trade.

### T-091: Real command data and reports
Replace the placeholder replies for `/status`, `/positions`, `/pnl`, `/risk`, `/report` and `/chart` with data from the broker, reconciler and journal (read-only). Wire the daily report (and weekly/monthly) to the scheduler with email and Telegram delivery, including LLM token cost per job from the ledger (the decision from D-028). A missing SMTP configuration downgrades to Telegram only with a warning, never an error loop.

Acceptance: each command returns current numbers in a fixture run and still answers when the LLM is raising; the daily report renders with and without trades; no secret or chat id appears in logs (log-capture test).

### T-092: Soak monitor and incident rules
A `soak` module that records, per day: cycles run, intents, Guardian rejections by rule, orders, fills, reconciliation mismatches, alerts, uptime, restarts, LLM spend, and equity/drawdown. Incident rules (each raises an alert and is written to `docs/evidence/soak/` as JSON): an order without a server-side stop; a position open with no stop on the broker; a reconciliation mismatch not healed in 5 minutes; risk per trade above the limit; daily loss limit exceeded; the kill or pause switch not honored; secrets found in logs. A single incident resets the four-week clock and requires a post-mortem in the handoff.

Acceptance: simulated incidents of each kind are detected in tests; the summary command (`/soak`) reports days run, incidents and the clock; evidence files contain no secrets.

### T-093: Stage gates in demo
Strategies move from lab Gate 1 to paper to demo exactly as `config/strategy_lab.yaml` says (Gate 2: 4 weeks and 200 demo trades). The Promotion Gate records each stage change; the Risk Guardian enforces the stage cap. No live stage is reachable in Phase 8.

Acceptance: a test proves no code path reaches stage 3 or 4 without `LIVE_TRADING_ENABLED=true` and a recorded gate (invariant 7).

### T-094: Scaling wiring and RunPod check (optional during the soak)
Wire the autoscaler behind `scaling.enabled` (default false) in the core pod only. First verify the RunPod REST base URL and the pod list/create/terminate response shapes with one read-only list call and record it as evidence. The owner confirms the hourly cap before enabling.

## Soak procedure
1. Owner redeploys the Pod from the latest main image; Codex runs the names-only env check and the smoke test.
2. Start the soak with the mock LLM. Move to a real LLM only after the owner sets `ANTHROPIC_API_KEY` and a monthly cap.
3. Weekly review by Claude from the soak summary: incidents, Guardian rejection mix, spend, backup status. Phase 8 passes after four consecutive weeks without an incident, then Q-gates for Phase 9 go to the owner (jurisdiction and tax check, live flag, both secrets from D-028).

## Out of scope in Phase 8
Live or micro-live trading, loosening risk limits, WhatsApp, self-hosted LLM, new instruments.
