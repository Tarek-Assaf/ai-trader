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
2. Exits: see item 3 (40/30/30 at 1R/2R/3R, stop to entry after TP1).
3. Sizing and exits (owner, 2026-10-19 11:43 Riyadh, in chat):
   - Risk per trade is exactly the indicator's: entry at the signal bar close, stop from the Pine
     structure rule (5-bar swing +/- 0.2 ATR, rejected above 2 ATR), no other stop logic.
   - Lot size: 0.1 contract per 10,000 of demo balance, proportional, floor 0.1, rounded down to the
     broker size step (read the minimum and step from the instrument rules, never assume). Examples:
     10,000 -> 0.10, 15,000 -> 0.15, 20,000 -> 0.20. The balance is read before each entry, so the
     size grows with gains and shrinks with losses (never below 0.1). Settings live in this
     experiment only (TAI_DEMO_BASE_LOT=0.1, TAI_DEMO_LOT_PER_BALANCE=10000); do not edit
     config/risk.yaml or any gate file. The size still goes through the Risk Guardian, which can
     lower it or veto.
   - Exits: TP1 at 1R closes 40% of the position, TP2 at 2R closes 30%, TP3 at 3R closes the last
     30%. When TP1 is hit the stop moves to entry (break-even). Split each partial to the broker step;
     if the step cannot express 40/30/30 at the current size (for example 0.1 with a 0.1 step), round
     partials to the nearest allowed sizes (never open more than the position), record the actual
     split in the journal, and if only one size step exists use a stop plus a 3R limit for the whole
     position and say so. Do not widen the stop at any time.
   - Journal the dollar value of 1 R per trade so results can be converted to money.
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
