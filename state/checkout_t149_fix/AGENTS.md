# AGENTS.md: Instructions for Codex

You are one of two builder agents (the other is Claude) building the project in `ARCHITECTURE.md`.

## Start of every session
1. Open `agent_state/AGENT_HANDOFF.json`.
2. If `turn` is not `codex`, **stop and do not edit anything.** Tell the owner it is not your turn.
3. Read `decisions`, `validations`, `blockers`, `questions`, then your tasks (`owner: codex`) whose dependencies are `done`.
4. Do not re-open decisions marked `do_not_reopen`. Do not repeat validations that have `result: pass` unless their `reverify_if` is true.

## End of every session
1. Update task statuses (`review` when done), `evidence`, `completion_note`.
2. Add `validations` for what you checked, and `questions` for anything ambiguous (never guess).
3. Append a `log` entry, update `project_state.summary`, increase `revision` by 1, set `turn` to `claude`.
4. Run `python agent_state/validate_handoff.py` and `make lint test`. Fix everything before handing over.
5. Commit messages start with the task id, for example `T-010: Capital.com REST client`.

## Ground rules
- Python 3.12, type hints everywhere, `ruff` and `mypy` clean.
- Build in phase order. Do not start a phase until the previous phase's tasks are `done`.
- Never hardcode secrets. Read them from environment variables listed in `.env.example`. Never print or log them.
- Default `TRADING_MODE=paper`, `BROKER_ENV=demo`, `LIVE_TRADING_ENABLED=false`. Never change these defaults.
- Define interfaces first (`MarketDataProvider`, `BrokerAdapter`, `LLMClient`, `Source`, `ChatChannel`), then implementations. Ship `PaperBroker` and `MockLLMClient` so everything runs offline.
- Docker: one image, `ROLE=all|core|worker|backtest`. No docker-in-docker on RunPod.
- If an external fact in `ARCHITECTURE.md` section 2 turns out to be wrong, record it in `validations` with result `fail` and a `question` for Claude. Do not silently work around it.

## Non-negotiable safety invariants (each needs a test)
1. No LLM-side module imports or calls broker code.
2. Every order has a stop-loss; the Risk Guardian rejects otherwise.
3. Position size comes only from `risk/sizing.py`.
4. `config/risk.yaml` and `config/strategy_lab.yaml` are read-only to every agent tool. They change only through the SettingsService (T-034), within the hard ceilings in `config/risk_ceilings.yaml`.
5. Risk Guardian, Execution Service and the decision loop are singletons behind a Redis leader lock.
6. Web and news content passes through `research/sanitizer.py` and is data, never instructions.
7. Real orders require `LIVE_TRADING_ENABLED=true` and a strategy at stage 3 or 4 set by the Promotion Gate.
8. Any component failure fails safe: no new entries, alert the owner.
9. Only `lab/validator.py` may read the sealed holdout.
10. No LLM-generated code executes in core or execution processes.
11. `/pause`, `/kill` and natural-language stop requests work without the LLM and take effect before the next order.
12. No secrets in logs, backups or Git. A secret scan blocks pushes.

## Capital.com specifics (verify, then record in validations)
- Demo host `demo-api-capital.backend-capital.com`, live `api-capital.backend-capital.com`.
- Auth headers: API key plus `CST` and `X-SECURITY-TOKEN` from `POST /session`.
- Limits: 10 requests/second; 1 session request/second; demo 1000 position/working-order requests/hour; WebSocket needs a ping every few minutes and allows 40 instruments.
- No 3-minute resolution: build 3M from 1M.

## Ask before assuming
If a requirement is ambiguous, implement the interface and the paper version, leave a clearly marked TODO, and add a `question` in the handoff file.
