# Positive control: can the lab and Gate 1 detect an edge that really exists?

Owner approval: 2026-10-12 19:20 Riyadh ("yes for both", D-034). Why: about 2,500 strategy attempts over Rounds A-C
and the 3m run produced no Gate 1 pass. Before reading that as "no edge exists", prove that the pipeline would have
recognised a real one. This task changes no code path that judges strategies, no gate and no cost value.

## What is built
`scripts/lab_positive_control.py` (new) and its evidence file `docs/evidence/positive_control.json`.
1. Build a SYNTHETIC lake and holdout in a temporary directory, in the same parquet layout the lab reads
   (`<root>/<SYMBOL>/1m/<year>/<month>.parquet`, same columns). Use the symbol NSXUSD so the real cost model
   (spread 2.0 + slippage 1.0, unchanged) applies, but a separate lake root: no real data is read or written.
   Size for the Pod: 4 years of 1m bars, realistic NAS100 volatility (one-minute ATR about 1.5 points, daily
   volatility about 1.2%), the 3 most recent months sealed as holdout exactly as `prepare_holdout` does.
2. Base series: driftless random walk with volatility clustering (fixed seed, recorded in the evidence file).
3. Planted edge, defined by a rule the strategy can see at entry. Rule R: a bar whose range is at least 1.5 x
   ATR(14) and whose close is in the lowest 10% of its range. After every R bar, the next 12 bars receive an extra
   upward drift so that the average gross result of "enter next bar open long, ATR stop 1.5 / target 3.0, 36-bar time
   stop" equals a chosen multiple m of the round-trip cost (3.0 points). Generate five series: m = 2.0, 1.0,
   0.5, 0.0 (pure null: R occurs but no extra drift) and m = -0.5 (R is a trap).
4. Strategy under test: the declarative spec that expresses rule R with those exits (use existing indicators
   only; if rule R cannot be expressed, adjust R to something the spec language can express and say so).
5. Null battery: on the m = 0.0 series run 200 different declarative strategies (the existing seeds and tuned
   variants are fine) through the standard `ValidationPipeline` with `trials` equal to 200 plus any prior ledger
   count, to measure the false-pass rate.
6. Run everything through the unchanged `LabRunner` and `ValidationPipeline` with the real `config/strategy_lab.yaml`.

## Acceptance
- Report, per series, the Gate 1 verdict, every failing reason, trades, out-of-sample trades, profit factors,
  walk-forward share, regimes, neighbour result and the corrected p value.
- Expected: m = 2.0 passes Gate 1; m = 0.0 and m = -0.5 fail; the null battery produces zero passes (one is
  tolerated only if the corrected p value explains it, and must be reported).
- If m = 2.0 does NOT pass: this is the important result. List which gate rejected it and why, and DO NOT change
  any gate, threshold, cost or code; hand back to Claude with the evidence. A gate that rejects a planted edge
  worth twice the costs needs an explicit owner decision.
- m = 1.0 and 0.5 are reported, not required to pass (they show the detection limit).
- Wall time target: under 30 minutes on the Pod, peak memory under 6 GB. No real data, no broker, no secrets.
