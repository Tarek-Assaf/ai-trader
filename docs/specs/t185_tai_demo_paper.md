# T-185: Tai port on the Capital.com DEMO account (owner-directed paper trading)

## Why
Owner 2026-10-19 09:50 Riyadh: "Run the test on the 3m hist data and let us try paper trading for
today using the same strategy." (D-066). This is an owner-directed demo experiment of the T-162
Tai port. It is NOT a promoted strategy: it does not count toward Gate 1 or Gate 2, never feeds
the trial counter, the ledger or promotion files, and every order, alert and report carries the
label "Tai port, owner-directed demo experiment, loosely matched to the owner".

## Scope (build; demo only)
1. A runtime strategy runner that, on each closed 3m NSXUSD bar from the live recorder feed,
   computes the unchanged T-162 port signal (session-anchored VWAP as corrected in T-182) and
   sends at most ONE order intent through the normal path: OrderManager -> Risk Guardian ->
   Capital.com DEMO adapter, with a broker-side stop. No second position while one is open.
2. Exits follow the port management: stop from the structure rule, scale-out thirds at 1R/2R/3R
   and stop to entry after TP1, where the broker supports partial closes; otherwise the nearest
   safe equivalent (a bracket with stop and a 3R limit, plus a software scale-out), documented.
3. Sizing (owner, 2026-10-19 11:39 Riyadh, answered in chat): fixed 0.1 lot/contract per trade,
   whatever the stop distance. Implement as an explicit setting TAI_DEMO_SIZE=0.1 used only by this
   experiment; do not edit config/risk.yaml or any gate file. The size still goes through the Risk
   Guardian, which can lower it or veto, and the broker minimum/step is respected (read it from the
   instrument rules, do not assume). With 0.1 the 1R/2R/3R thirds cannot be split: if the broker
   step does not allow 3 partial closes, use a bracket (structure stop + 3R limit) and record in
   the journal that scale-out was not possible. Report the dollar value of 1 R per trade in the
   journal so results can be converted to money.
4. Market hours respected (no entries when the market is closed or inside the roll/spread
   blackout the Guardian already uses). Kill switch and /stop commands must stop new entries and
   flatten, tested with a mock broker.
5. Telegram alert on entry, exit and kill switch with the experiment label.
6. Disabled by default: new env flag TAI_DEMO_EXPERIMENT=false. Runs only when
   BROKER_ENV=demo AND LIVE_TRADING_ENABLED=false AND TAI_DEMO_EXPERIMENT=true; any other
   combination refuses to start (tested).
7. Journal every decision (signal, veto, order, fill, exit) so the owner can compare it with his
   own TradingView view of the same day.

## Not in scope / hard limits
No live account, no gate or risk file edits, no LLM in the order path, no secrets in code, logs or
backups. Tests use a mock broker only; the first demo run happens only after the owner redeploys
the Pod image and sets TAI_DEMO_EXPERIMENT=true (advance notice required before any redeploy).

## Acceptance
Unit tests for: signal-to-intent, one-position rule, Guardian veto respected, kill switch,
the three-flag start guard, market-closed refusal, scale-out/stop-to-entry bookkeeping.
CI green (ruff 0.9.6 line length 100, mypy on src, pytest). One log entry, hand to claude.
