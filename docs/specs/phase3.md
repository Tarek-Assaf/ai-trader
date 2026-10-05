# Phase 3 Spec: Control, Chat, Visualization, Reports, SettingsService

Author: Claude. For: Codex. Covers T-030 to T-034. Depends on Phase 2 (Risk, Execution, audit). Read `ARCHITECTURE.md` 5.9 and 12, and `docs/specs/settings_service.md`.

## Principles
- Stop, pause, kill and status are **deterministic code paths**. They never call an LLM and never wait for one.
- Only the owner's Telegram chat id (allow-list in config/secrets) is obeyed. Every other chat is silently ignored and logged (no reply, no content echoed).
- Secrets never appear in messages, logs, charts or reports.
- Everything the owner can change goes through SettingsService (T-034). Chat cannot edit `risk.yaml`, `risk_ceilings.yaml` or `strategy_lab.yaml` directly.

## T-030: Telegram channel (`src/trader/channels/telegram.py`)
- Long-poll (no public webhook needed on RunPod). Token from env `TELEGRAM_BOT_TOKEN`; owner id from `TELEGRAM_OWNER_CHAT_ID`.
- Commands: `/status`, `/positions`, `/pnl`, `/pause`, `/resume`, `/stop` (flatten + halt new entries), `/kill` (flatten + disable execution until manual reset), `/risk`, `/report daily|weekly|monthly`, `/chart <instrument> [tf]`, `/help`.
- Alerts (outbound): order filled/rejected, stop hit, risk halt, data stale, backup stale, circuit breaker open, daily summary. Each alert has a stable `alert_key` and a per-key rate limit so a flapping condition cannot spam.
- Inline buttons: Confirm / Cancel for dangerous actions (resume after halt, SettingsService changes). Button callbacks carry a short-lived nonce stored server-side; replays and expired nonces are rejected.
- Acceptance: non-owner updates ignored (test with fixture updates from another chat id); every command has a test using a fake Telegram transport; alert rate limiting tested; no token in logs (test captures log output); a Telegram outage never blocks the trading loop (send queue with retry, bounded size).

## T-031: Chat router (`src/trader/channels/router.py`)
Two paths:
1. **Fast path (no LLM):** regex/keyword matcher for stop, kill, pause, resume, status, positions, pnl in English (Arabic phrases optional later). Matches execute immediately via the same functions the commands use. Example phrases that must work: "stop", "stop trading", "close everything", "kill it", "pause", "what's my status", "how are we doing".
2. **LLM intent path:** anything else is classified by the cheap-tier model into a typed `Intent` (query, chart request, report request, settings change request, question about a trade, small talk, unknown). The LLM output is parsed into the typed schema; invalid output means "unknown" and a clarifying reply.
- Refusals: requests to raise limits above ceilings, trade directly ("buy 10 NAS100 now"), disable the Risk Guardian, reveal secrets, or run code are refused with a short reason. The LLM path **cannot** call broker, execution, or SettingsService code; it can only emit an `Intent` that deterministic code validates and, for settings changes, turns into a typed request that needs the T-034 confirm flow.
- If the LLM is unavailable (circuit breaker open or budget exhausted), the fast path and all commands still work; the reply for other text says the assistant is limited.
- Acceptance: parametrized tests over stop phrases prove the LLM mock is **never called**; dangerous-request fixtures are refused; a test with the LLM raising errors still lets `stop` flatten positions.

## T-032: Visualization (`src/trader/viz/`)
- Functions return `Figure` objects and save PNG (matplotlib, headless Agg) and HTML (plotly optional, only if already a dependency; ask before adding).
- Charts: candlesticks with entry/exit/stop/target markers, equity curve, drawdown, per-session P&L heatmap, strategy comparison bars, spread by session.
- Inputs are typed data frames from the repository layer, never raw SQL in the chart code.
- Acceptance: each chart renders from fixtures without network and the PNG is non-empty with expected size; chat `/chart` returns an image through the fake Telegram transport; empty data renders a "no data" chart, not an exception.

## T-033: Reports (`src/trader/reports/`)
- Daily (after the NY close), weekly (Friday close), monthly. Content: P&L (gross, costs, net), trades, win rate, expectancy, max drawdown, risk usage, per-instrument split, best/worst trade, strategy stage changes, data and backup health, LLM spend vs cap, and open issues.
- Delivery: Telegram (summary + chart images) and email (HTML + plain text) via SMTP settings from env (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_EMAIL_TO`). If SMTP is not configured, skip email and log once; never fail the report.
- Numbers are computed by code from the ledger; an LLM may only write the short commentary paragraph and cannot change a number (the template inserts numbers, the commentary is a separate field).
- Acceptance: sample reports render from fixtures (snapshot tests); email send tested with a fake SMTP server; missing SMTP is a no-op; scheduled via the existing scheduler in `config/schedule.yaml`.

## T-034: SettingsService (follow `docs/specs/settings_service.md` exactly)
Additional notes from this phase:
- Telegram confirm button and TOTP prompts are the only owner-facing UI; the service itself is UI-agnostic.
- Acceptance as listed in the task: agents cannot import it (static import test, same pattern as the broker import test), nothing can exceed `risk_ceilings.yaml`, loosening during a loss halt is rejected, every change is in `audit_log` and notifies Telegram.

## Addendum A: Control path rules for T-030 / T-031 (Claude, 2026-10-05)
Ties Telegram and the router to the T-027 control module (`src/trader/execution/control.py`: `ControlMode`, `ControlWriter`, `build_control_sources`).

1. **Single writer.** `/pause`, `/resume`, `/stop`, `/kill` and their fast-path phrases call only `ControlWriter` (and the flatten function for stop/kill). No other module in `channels/` imports the control sources for writing. Add an import-boundary test.
2. **Safe direction is immediate.** `/pause`, `/stop`, `/kill` and their phrases take effect on the first message with no confirm step and no TOTP, because they can only reduce risk. Reply states the resulting mode and the time.
3. **Unsafe direction is gated.** `/resume` (and any settings change) needs all of: owner chat id, a Confirm button press carrying a server-side nonce (single use, 60 s expiry), and the control module's existing resume authorization (TOTP/replay protection as built in T-027). A resume after `/kill` additionally requires the manual reset the architecture defines; Telegram alone cannot clear a kill.
4. **No LLM or agent path to control.** The T-031 `Intent` schema has no pause/resume/stop/kill variant. If the LLM path classifies text as one of these, the router must not act on it; it replies "use /pause, /resume, /stop or /kill". No agent tool, prompt or MCP surface may expose `ControlWriter`.
5. **Fail closed.** If the control store (flag file or Redis) cannot be read, the system behaves as paused; `/status` says so. If Telegram is down, control still works through the owner HTTP endpoints.
6. **Audit.** Every control command writes an audit event: source (`telegram`/`api`), chat id hash, previous and new mode, nonce id for gated actions. Rejected attempts (non-owner, bad nonce, expired nonce, replay) are logged without echoing content.
7. **Acceptance additions.** Tests: LLM mock never called for control phrases; Intent schema cannot represent control actions; resume without Confirm is refused; replayed and expired nonces are refused; unreadable control store yields paused; `/kill` cannot be undone from Telegram; import-boundary test for rule 1.

## Phase 3 exit checklist
All five tasks pass review; `scripts/` smoke test sends a status message to the owner on the pod (owner-run, demo only); stop phrase from Telegram flattens the demo account within 5 seconds in the demo test.
