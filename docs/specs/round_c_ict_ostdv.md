# Round C: ICT concepts and the OSTDV overnight-range levels

Owner decision (2026-10-11, Riyadh evening): continue the search with a new idea family built from
(1) the OSTDV levels in the owner's four pictures and (2) ICT concepts, test it, then decide. Costs,
gates and holdout handling stay unchanged. NAS100 (NSXUSD) and gold (XAUUSD) are separate markets and
are reported separately.

## Definitions (fixed before any result is seen)
- Asia range: high and low of bars opening 18:45 to 22:03 New York time (US daylight saving applies).
  Visible only after 22:03; stays in force until 18:45 the next day.
- Trading day: the 18:00 New York open to the 17:00 close (the day's high and low for statistics).
- A level "k ranges beyond the Asia edge" = edge +/- k x (Asia high - Asia low). Owner chart mapping:
  0/1 edges, -0.5/1.5 -> k 0.5 (retracement), -1/2 -> 1 (SB), -1.5/2.5 -> 1.5 (LV), -2..-2.5 / 3..3.5 ->
  k 2..2.5 (micro SMR, centre 2.272), -3/4 -> 3 (SMR), -3.5..-4 / 4.5..5 -> 3.5..4 (extremes).
  Orientation (owner, 2026-10-11): the Fib follows the trade. For a sell, 0 is the Asia high and 1 the Asia
  low; for a buy, 0 is the Asia low and 1 the Asia high. 0 and 1 are the manipulation/sweep levels. The
  OTE zone 0.5-0.62 is support when price is above it and resistance when price is below it, measured from
  the trade's own 0 edge (buy zone: low + 0.5..0.62 ranges; sell zone: high - 0.62..0.5 ranges).
- Kill zones (New York clock): London 02:00-05:00, New York 07:00-10:00.
- Candle shapes (range >= half an ATR(14) so noise is ignored): hammer shape (lower wick >= 2x body, upper
  wick <= 10% of range), inverted shape (mirror), bullish/bearish engulfing (body covers the previous
  opposite body and is larger), doji (body <= 10% of range). Hanging man = hammer shape at highs (short),
  shooting star = inverted shape at highs (short).

## Part 1: zone statistics (no trading, no gate)
`python scripts/ostdv_stats.py` writes `docs/evidence/round_c_ostdv_stats.json`: for each market, overall
and per year, how often the day's high and the day's low fall in each OSTDV zone, in equally wide
neighbouring bands, and (micro SMR) among days that reached k=2. Tests the claim "70-80% of day highs/lows
form at the micro SMR". Training lake only; never the holdout.

## Part 1b: level event study (owner request, 2026-10-11 22:47 Riyadh; no trading, no gate)
`python scripts/ostdv_events.py` writes `docs/evidence/round_c_ostdv_events.json` and a readable `.md`.
On 5-minute bars, for each trading day, side (above Asia high / below Asia low) and level k (named:
0.1111, 0.5, 1, 1.5, 2, 2.272, 3, 3.5), take the FIRST bar that reaches the level. At the close of the next
bar record: touch bar closed back inside or beyond the level, next bar closed back inside or beyond, candle
shape (hammer, inverted hammer, bull/bear engulfing, doji), session (asia, London KZ, pre-NY, NY KZ, NY day),
trend (EMA50 vs EMA200 with or against the move), whether the prior-day high/low was swept on the same bar,
touch bar size, and time since the range formed. Outcome from the level, unit one ATR, horizon 36 bars:
`retrace` (back one ATR first), `continue` (on one ATR first), `neither`. Report counts, retrace rate, the
top single patterns and pairs by lift versus the level's own base rate (at least 30 events, with the number of
years agreeing in direction), by market. The same study runs at placebo levels (0.3, 0.75, 1.25, 1.75,
2.636, 3.25) so patterns that also appear on arbitrary lines are not credited to the OSTDV levels. This shows
what repeatedly co-occurred, not why the market moved.

## Part 2: 172 strategies per market and timeframe (`--idea-sources round_c`)
- ostdv_sweep (24): poke 0 or 0.1111 ranges beyond the Asia edge, close back inside; any time / London /
  New York window.
- ostdv_fade (24): poke through level k (0.5, 1, 1.5, 2, 2.272, 3) and close back on the near side.
- ostdv_ote (12): price dips into the buy zone from above (low <= zone top, close above it) or rises into the
  sell zone from below (high >= zone bottom, close below it); any time / London / New York.
- ostdv_break (8): bar opens at/inside and closes beyond Asia edge +/- 0.5 or 1 ranges, trade with it.
- ict_sweep_fvg (24): liquidity (Asia edge or prior-day high/low) swept within 12 five-minute-equivalent
  bars, a fair value gap forms (this bar's low above the high two bars ago, mirrored for shorts), close back
  beyond the level; any / London / New York.
- ict_structure_shift (8): Asia edge swept, then close beyond the recent 6-bar swing; London / New York.
- candle_at_level (72): hammer, inverted hammer, bullish engulfing (long) and hanging man, shooting star,
  bearish engulfing (short) at a swept Asia or prior-day level; any / London / New York.
Exits: ATR stop/target (1.5/3.0 and 2.0/4.0) and a 36-five-minute-bar time stop. Long and short are separate
seeds. No parameter tuning (no tunable parameters), so no extra variants.

## Acceptance for the Pod run (T-133)
1. Run `scripts/ostdv_stats.py` and `scripts/ostdv_events.py` on both markets; commit the JSON/markdown
   outputs to `docs/evidence/` (training lake only).
2. Run `scripts/lab_run.py --idea-sources round_c` on 60m, 15m, 5m (one at a time, same memory rules as
   Round B) for both markets. Do not touch holdout handling or config.
3. Commit `docs/evidence/t133_round_c_summary.json` with, per market and per family: attempts, too-few-trades
   count, Gate 1 passes, best 3 by `diag_mean_pnl_p_weekly` with the full fields (trades, OOS trades, OOS
   profit factor, walk-forward share, net, gross per trade, gate p, weekly p), and the counts of attempts with
   weekly p <= 0.05 and <= 0.001. Fields that are unavailable must be explained, not left empty.
