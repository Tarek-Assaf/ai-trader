# T-147: Tai Trading Smart Filter test (owner-supplied hypothesis)

Status: spec for Codex. Start only after T-145 is accepted. Diagnostic only: no broker calls,
no trial-counter/ledger/promotion writes, sealed holdout never read, Gate 1 unchanged.

## Why
The owner trades with the TradingView indicator "Tai Trading - Smart Filter" (Pine v5) on 3m
NQ and reports about 30 trades at ~70% wins. It is a hypothesis to test on our data, not the
agent's method. The test also shows which parts of it matter, so the agent learns the reason.
Source text: owner upload TaiTrading_Smart_Renko (1).docx (rules summarized below).

## Rules to implement exactly (Open/Close setup, owner settings)
- Signal TF = chart TF x 18 (3m -> 54m). Heikin Ashi built on the signal TF, no lookahead
  (use only closed higher-TF bars). Buy: HA close crosses above HA open. Sell: crosses below.
- Sideways filter: "No Filtering" (owner setting). RSI(7) top 45 / bottom 10 only used by
  the other filter modes.
- Smart filters (all on): close > EMA200 for buys, < for sells; ADX(14,14) >= 20 with
  +DI > -DI for buys, -DI > +DI for sells; close above VWAP for buys, below for sells
  (VWAP resets per session/day, hlc3); |close - VWAP| / ATR(14) <= 1.5.
- Stop: buy = lowest low of the previous 5 bars - 0.2*ATR(14); sell = highest high of the
  previous 5 bars + 0.2*ATR(14). Skip the trade if risk <= 0 or risk > 2.0*ATR(14).
- Targets: TP1/TP2/TP3 at 1R/2R/3R from entry (entry = signal bar close).
- Refresh entry: if flat and >= 12 bars since last exit and filters+risk rules agree, enter
  again in the filter direction (same rules as above).
- Start date 2023-01-01 (owner setting). Our test also reports 2022 separately.
- Only one trade at a time.

## Exit accounting (two versions, both reported)
1. Owner scale-out (the REAL result): sell 1/3 at each target; after TP1 the stop of the
   remaining position moves to entry. Per trade: SL before TP1 = -1R; TP1 then entry stop =
   +0.33R; TP2 then entry stop = +1.0R; TP3 = +2.0R. Costs deducted in points then converted.
2. Indicator dashboard accounting (for comparison only): -1R, +1R, +2R, +3R as the script books.
Also report single-exit variants (all out at 1R, 2R or 3R; ATR stop/target) on the same signals.
Intrabar order: if stop and a target are hit in the same bar, assume the stop first
(conservative) and state this. Entry/exit prices use closed-bar data only.

## Scope
NSXUSD and XAUUSD; 1m, 3m, 5m, 60m (1m mandatory, never 60m only). Signal TF = chart TF x 18
for each. Years: 2022 separate, 2023, 2024 (mark not fresh), 2025, 2026 to day before holdout.
Cost modes: zero cost and normal cost (4 pts NSXUSD, 1.6 XAUUSD per round trip).

## Ablation (learn the reason)
Run with each part removed one at a time: EMA200, ADX, VWAP direction, don't-chase limit,
swing stop (replace by ATR stop), refresh entries, and scale-out vs single exit. Report which
parts add or remove net points and trade count per timeframe/market.

## Agent variants (separate table)
Bounded tuning of: signal TF multiplier, ADX minimum, VWAP distance, swing lookback, buffer,
max risk ATR, target multiples. Count every trial in a trial total reported in the evidence.
Pre-registered in-sample 2022-2024 vs out-of-sample 2025 to pre-holdout. Report the best by
out-of-sample net points only with trade count (>=30 flagged), never by in-sample.
Mirror-direction control for each run.

## Acceptance
- Evidence: docs/evidence/t147_tai_filter_summary.json and a readable table .md.
- Per timeframe/market/year: trades, win rate (real accounting), net pts zero/normal cost,
  total R, PF, max drawdown, average R per trade; both accounting versions side by side.
- Statement of how much the dashboard accounting overstates the real result.
- Report CPU/RAM first, plan parallel splits with disjoint ranges before launching, 13 GB cap.
- Tests, ruff check and format pass; one log entry; hand turn to claude.
- Not Gate 1: nothing here is promoted; any candidate must still pass Gate 1 later.
