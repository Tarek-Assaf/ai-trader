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
   - Lot size (owner clarification 2026-10-19 11:53): the first trade is always 0.1 contract, whatever
     the demo balance is. Record the demo balance at experiment start (START_BALANCE). From then on
     lot = 0.1 x (current balance / START_BALANCE), rounded down to the broker size step, never below
     0.1 (floor). Examples: start 1,000 -> 0.10; balance 1,500 -> 0.15; start 10,000 and balance
     15,000 -> 0.15. The balance is read before each entry. START_BALANCE is stored in the
     experiment journal and can be reset only by the owner. Settings live in this experiment only
     (TAI_DEMO_BASE_LOT=0.1); do not edit config/risk.yaml or any gate file. The size still goes
     through the Risk Guardian, which can lower it or veto. Read the minimum deal size and size step
     from the broker instrument rules (read-only), never assume them.
   - Exits: TP1 at 1R closes 40% of the position, TP2 at 2R closes 30%, TP3 at 3R closes the last
     30%. When TP1 is hit the stop moves to entry (break-even). Implementation: first read from the
     broker instrument rules whether partial closes are supported and the minimum size and step.
     If the size allows, open the position as three separate legs (40/30/30, each with the same
     stop and its own take-profit) and move the two remaining legs' stops to entry when leg 1 fills
     TP1; or use partial closes if the API supports them. If the size cannot be split into legs of
     at least the broker minimum, round to the nearest allowed split, and if only one leg fits use
     a stop plus a 3R limit for the whole position. Record the actual split in the journal; never
     widen a stop.
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
