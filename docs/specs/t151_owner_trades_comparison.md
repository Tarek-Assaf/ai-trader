# T-151: Reproduce the owner's own Tai entries (forming-candle replay)

**SUPERSEDED by T-160:** Owner 2026-10-17: supplied his TradingView Replay Trading export (NQ1 CME mini, 3m, times in UTC (owner confirmed 2026-10-17 13:46 Riyadh; an earlier message said Riyadh, that was a mistake),
6 to 16 Oct 2026), file docs/evidence/owner/owner_trades_tv_replay_nq1_3m_20261006_20261016.csv.
Unparks T-151 (D-040 condition met: the owner finished validating his trades).

## What the file contains
54 exit lines = 31 positions (grouped by entry time and direction; a position is split into 1/2/3
contract tranches that exit at TP or stop), 21 short and 10 long, net +23,700 USD at 20 USD per
point. Tranche win rate 72% (39 of 54), position win rate 61% (19 of 31). 21 exits are market orders
(partly manual). All entry minutes are multiples of 3.

## Finding that triggered this task (Claude, 2026-10-17)
Treating the file as UTC, against the T-149 signals on recorded Capital.com NAS100 data (1m, 3m and
5m, same days): 0 of 31 entries within 3 minutes, 1 of 31 within 6 minutes, 5 of 31 within 30 minutes
(3m signals alone: 3 of 31). Same direction in every near hit. The mismatch stands with the right time zone.

## Hypothesis to test first
The Pine script computes Heikin Ashi through request.security(haTicker, myTime, open/close,
lookahead_off) where myTime = chart minutes x 18 = 54 minutes. In Replay and live, that 54 minute HA
candle is still forming, so crossover(haClose, haOpen) can fire on any 3m bar inside it and later
vanish. Our replay used finished 54m candles only.

## Method (diagnostic only; no broker calls, holdout stays sealed, no gate/cost/ledger edits)
1. Data: recorded Capital.com NAS100 1m bid/ask from the lake for 2026-10-05 to 2026-10-16 (read
   only). Build 3m bars by UTC clock buckets as in T-149.
2. Signal variants, each run on identical data (pre-registered; report all):
   A. closed 54m HA candles only (the T-149 behaviour, baseline).
   B. forming 54m HA candle: at every closed 3m bar recompute the HA open/close of the partial
      54m candle; signal when crossover(haClose, haOpen) happens versus the previous 3m evaluation.
   C. like B but the HA state is carried across 54m candle boundaries as TradingView does.
   For the 54m candle anchor try 3 session anchors (CME open 17:00 Chicago, UTC midnight, Riyadh
   midnight) and report each; do not pick one by best fit without saying so.
3. Filters exactly as the Pine script defaults (EMA200 trend, ADX 14/14 >= 20 with DI side, VWAP
   side, max 1.5 ATR from VWAP, no sideways filter). Warm-up: state the bars available (the lake has
   only 12 days, so EMA200 warm-up is short; report how many signals fall before warm-up is
   complete and exclude them).
4. Compare every variant with the 31 owner entries: signal within +/-1, +/-2 and +/-5 3m bars and the
   same direction; report hits, misses, and extra signals the owner did not take, per day.
5. Side comparison of price: owner entry price vs recorded NAS100 mid at that minute (the futures vs
   CFD basis), median and range in points.
6. Report: plain-language table per variant (hits of 31, extra signals), a verdict on whether the
   forming-candle hypothesis explains the mismatch, and what remains unexplained. Do not tune
   parameters to raise the hit count.
7. Count of variants tried is reported as the trial count. Nothing here feeds Gate 1, the ledger or
   promotion files.

## Acceptance
- scripts/lab_t151_owner_trades.py with CI-importable tests (forming-candle HA against a hand
  calculated example, crossover on a partial candle that later reverses, tolerance matching).
- docs/evidence/t151_owner_trades_summary.json and a plain-language report.
- ruff check and ruff format --line-length 100 (CI uses ruff 0.9.6), mypy clean.
- Respect the Pod execution safety rules (13 GB cap, watchdog). The data is only 12 days, so the run
  is short; no 1m grid is needed.
- PR, ONE log entry, validation entry, set to review, hand the turn to claude.

## Addendum 2026-10-18 (Claude): why 3-12 signals cannot explain 31 entries
The owner confirmed the uploaded Pine file is exactly his chart configuration. Re-reading it shows two
signal paths and the T-151 variants A/B/C only model the first:
1. HA crossover on the 54m Heikin-Ashi series (variants A/B/C).
2. Refresh entries (default on, 12 bars): whenever the indicator's own trade is flat for >= 12 bars
   and the smart filter and structure-stop risk pass, it signals WITHOUT a crossover. These fire on
   most bars where the trend filters agree, so they are far more frequent than crosses.
Cycle 2 must therefore add, all diagnostic only, no parameter tuning:
- Variant D: refresh-path generator (filters + risk + flat >= 12 bars, no cross), on closed 3m bars.
  Report hits within +/-1, +/-2, +/-5 bars and extra signals per day, with and without the virtual
  in-trade gating (the Pine trade state machine: no new signal while its own virtual trade is open).
- Per owner entry (31): value of each condition at the entry bar (HA state long/short, EMA200 side,
  ADX, DI side, VWAP side, |close-vwap|/ATR, risk/ATR with the 5-bar swing +/-0.2 ATR stop). Count how
  many entries pass ALL filters, and which single condition fails most often.
- Stop fit: the owner's bracket stop distance per entry versus the structure-stop rule (points and in
  ATR); report the median error. Close fit would show the owner follows the indicator's levels.
- Basis note: filters use NAS100 CFD data while the owner trades NQ1! futures (median +209 pts);
  side tests are unaffected, but VWAP/EMA distances should be computed on price minus the per-day
  median basis as a second column.
