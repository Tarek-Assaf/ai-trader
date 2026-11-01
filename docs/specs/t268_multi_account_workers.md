# T-268: extra accounts as workers inside the one container

Status: implemented in PR 488, awaiting CI, install and review. Demo only.
Author: Claude. Reviewer to take over: Codex (T-269). Owner decisions: D-084 to D-087 (queued for
`agent_state/AGENT_HANDOFF.json`, see "Hand-off file" below).

## Owner requirements (2026-11-01, chat)
- One TradingView alert integration and one Telegram bot; one container, one ngrok. "Inside the
  container you can modify it and split as you want, but outside it is the same."
- Add accounts later by adding numbered variables (`CAPITAL_IDENTIFIER_02`, `_03`, ...). Each account
  has its own credentials and settings. Demo now, live later (not built).
- Pause and kill each account separately or all at once.
- No typed start balance: the bot identifies it from the account.

## Design
Account 01 is the main process and is unchanged. Every further active account is a worker process
started by supervisord inside the same container.

| Concern | Main (account 01) | Worker NN |
|---|---|---|
| Process | `api` program | `account_NN` program, `scripts/start_account_api.sh NN` |
| Listens on | 0.0.0.0:8000 (published to 127.0.0.1) | 127.0.0.1:80NN, never published |
| Broker login | `CAPITAL_*` | `CAPITAL_*_NN` mapped onto `CAPITAL_*` for that process only |
| Database | `trader` | `trader_NN` (created and migrated at start) |
| State folder | `/workspace` | `/workspace/accounts/NN` |
| Leases | original keys | `...:acct-NN` suffix (`scoped_lease_key`) |
| Control keys | shared key + `...:acct-01` | shared key + `...:acct-NN` |
| Market data | recorder + lake | reads the same lake; no recorder of its own |
| Telegram | polls, answers commands | `TELEGRAM_POLLING=off`: alerts and reports only, labelled `[NN Name]` |

Flow of one alert: TradingView posts to ngrok to the main bot. After authentication, the main bot
copies the raw body to each address in `TRADINGVIEW_FORWARD_URLS` and `/workspace/state/forward_urls`
(read on every alert) on a separate thread, marked with header `X-Tai-Forwarded` so it is never
copied again, then handles it as before. A failed copy cannot change the reply.

Start balance: `StartBalanceStore` (per account workspace, `state/tai_start_balance.json`) records
the balance at the first sizing; lot = base lot x balance / start balance, never below the base lot.
An explicit `TAI_DEMO_START_BALANCE` still wins. A damaged file refuses entries instead of guessing.
The loader never lets a start balance fall back from the unnumbered variable to a numbered account.

Control (`execution/control.py`): `AccountControls` gives every account its own Redis key plus the
shared key and the flag files; kill beats pause. `/pause`, `/stop`, `/kill`, `/resume` take an
optional number. No number means all accounts (shared key for pause/kill; resume clears the shared
key and asks each account that is not killed to resume). An unknown number is refused. The HTTP
control route uses the all-accounts writer.

Fail-safe: the entrypoint runs `python -m trader.runtime.account_workers render`; if it fails the
include file is emptied and only account 01 runs.

## Files
New: `src/trader/runtime/account_workers.py`, `scripts/start_account_api.sh`,
`src/trader/experiments/start_balance.py`, `src/trader/reports/account_status.py`.
Changed: `docker/entrypoint.sh`, `docker/supervisord.all.conf`, `docker/Dockerfile`,
`src/trader/execution/control.py`, `src/trader/channels/telegram.py`, `src/trader/api/app.py`,
`src/trader/api/control.py`, `src/trader/api/tradingview.py`, `src/trader/experiments/tai_demo.py`,
`src/trader/experiments/tai_wiring.py`, `src/trader/core/accounts.py`,
`src/trader/execution/journal.py`, `src/trader/runtime/services.py`, `src/trader/runtime/factory.py`.

## Acceptance criteria and where each is tested
1. Workers get their own login, database, state, leases, port, label: `tests/unit/test_account_workers.py`.
2. No credential reaches the supervisor file, forward file or report; shell exports cannot run code:
   same file.
3. Per-account and all-account pause/kill/resume, unknown number refused, resume-all skips a killed
   account, forged confirmation cannot resume another account: `tests/unit/test_account_controls.py`.
4. Alert copy: targets from env and file, de-duplicated, invalid skipped, retry rules, loop guard,
   copy failure cannot change the reply: `tests/unit/test_tradingview_forward.py` and
   `tests/unit/test_tradingview_webhook.py`.
5. Start balance recorded once, survives restart, override wins, damaged file refuses:
   `tests/unit/test_start_balance.py`, `tests/unit/test_tai_demo.py`.
6. Labels, lease keys, extra-account status lines: `tests/unit/test_account_labels_and_leases.py`.

## Not proven yet
- A real second Capital.com demo account trading from one alert (needs an open market and the
  owner's second keys in `runtime.env`).
- The container start with a worker: supervisor include, database creation, `alembic upgrade head`
  against `trader_NN`. CI builds and runs the `ROLE=all` container but has no second account.
- Recorder health shown for workers (shared recorder).

## Known gaps (queued)
- T-270: backups cover only database `trader`; `trader_NN` are not backed up (B-036).
- T-271: balance and P&L per account in `/status`, `/pnl`, reports (B-037).
- Capital.com limits per login/IP are unknown; each worker holds its own session.

## Hand-off file
`agent_state/AGENT_HANDOFF.json` has `turn: codex` since 2026-10-23 and its log stops at T-222,
while T-223 to T-268 were merged by PR. CI rejects a Claude log entry while the turn is Codex
(`validate_handoff.py --previous`). The prepared update (tasks T-262..T-271, decisions D-084..D-087,
validations V-526/V-527, blockers B-036/B-037, two log entries) needs the owner to set `turn` to
`human` first, or Codex to take its turn and append a first entry. It is not in this PR for that reason.

## Reviewer checklist (CLAUDE.md safety posture)
No LLM-side module gets broker access; no agent can edit risk or gate files; no LLM-written code runs
in core/execution; stop requests never route through the LLM; no secrets in code, logs, backups or
files. Check in particular that the worker environment is only ever built in memory and exported by
`start_account_api.sh`, and that `render` output contains no values.


## Update 2026-11-02: what changed after the first real run

- **PR 489**: the worker launcher looked its account up after taking on its login, so it saw a duplicate
  of account 01 and dropped itself. Both lookups now run first (test guards the order).
- **T-270 (PR 491)**: every `trader_NN` database is dumped, validated and retained beside the main one,
  uploaded encrypted with the main dumps, and restored on a new cluster (a corrupt newest dump falls
  back to the previous; a missing backup starts that account fresh; start-up is never blocked).
  Verified against a real PostgreSQL 16. The CI Docker drill does not include an account database yet (T-277).
- **T-271 (PR 491)**: `/status` shows each account's own full status (balance, equity, entry state);
  `/positions /pnl /risk /report` show every account, `/pnl 02` one account. The main process asks the
  worker over loopback (`POST /internal/command`, read-only commands, loopback callers only, refused
  through the tunnel). Not built: charts per account (T-274), natural-language router per account (T-275).
- **Pending list**: T-272 first market-open check, T-273 Capital.com limits for two logins, T-274, T-275,
  T-276 scheduled reports from account 02, T-277 CI drill, T-269 Codex review.
