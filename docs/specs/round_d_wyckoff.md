# Round D: Wyckoff ideas (range, spring / upthrust, test, sign of strength)

Owner request: 2026-10-12 19:22 Riyadh (D-034). Wyckoff has not been tested before. Definitions are fixed here
before any result is seen. NAS100 (NSXUSD) and gold (XAUUSD) are separate markets and reported separately.
Costs, gates and holdout handling stay unchanged.

## Definitions
- Trend before the range: the net move over the 2N bars before the range is at least 3 x ATR(14). Range window
  N in {48, 96, 192} five-minute-equivalent bars.
- Trading range: highest high and lowest low of the last N bars, width between 1.5 and 6 ATR(14), and a flat
  middle: the absolute slope of the closes over the N bars is at most 0.25 x ATR(14) per 10 bars.
- Accumulation range = the range formed after a down-trend; distribution range = after an up-trend.
- Spring (accumulation): a bar whose low breaks the range low by at most 0.5 x ATR (strict variant: at most
  0.25) and closes back inside the range. Upthrust (distribution): mirror at the range high.
- Test of the spring: within 12 bars after a spring, a bar whose low stays above the spring low and whose
  effort (bar range, or tick volume if the data has real volume) is smaller than the spring bar's.
- Sign of strength (SOS): within 10 bars after the spring, a bar closing above the range midpoint with range at
  least 1 x ATR. Sign of weakness (SOW) is the mirror after an upthrust.
- Effort versus result: effort = bar range x (volume if reliable, else 1); a shakeout bar has high effort and a
  small net move. First step: check whether the lake's volume column is populated (not 0 or 1); report it.

## Part 1: event study (no trading, no gate), same method as Part 1b of Round C
`python scripts/wyckoff_events.py` on 5-minute bars (training months only). For each market, N and variant:
how often a break of the range edge reverses (closes back inside) versus continues, and what follows after one
ATR and 36 bars, split by spring alone, spring + test, spring + SOS. Compare with PLACEBO ranges: the same
width distribution without the trend precondition, and ranges with edges shifted by +/-0.75 range, so that a
pattern seen at arbitrary lines is not credited to Wyckoff. Report counts, rates, lift versus placebo and years
agreeing. Output `docs/evidence/round_d_wyckoff_events.json` and a readable `.md`.

## Part 2: strategies (`--idea-sources round_d`), about 96 per market and timeframe
- spring_long / upthrust_short: enter at the close of the spring or upthrust bar (24 each: N x strictness x
  session any / London / New York).
- spring_test_long / upthrust_test_short: enter at the test bar (24).
- spring_sos_long / upthrust_sow_short: enter at the SOS or SOW bar (24).
- Exits: stop 0.25 ATR beyond the spring or upthrust extreme; targets: range midpoint, range far edge, or 2 x
  risk; plus a 36-bar time stop. No parameter tuning. Declarative specs only; add the needed indicators to the
  existing indicator set if missing and cover them with unit tests.
- Timeframes first 60m, 15m and 5m; 3m and 1m only after the owner asks.

## Part 3
Round D attempts become eligible parents for the staged search (see `docs/specs/staged_search.md`), so any
Wyckoff variant that comes close can be refined with the global trial count carried forward.

## Acceptance
Evidence summary in the T-134 format plus the per-trade comparison against random entries (`lab_random_baseline.py`
for the top 3). A run with no pass is a valid result.
