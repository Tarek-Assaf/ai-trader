# Round A plan: separate markets, stop rule, demo incubation

Owner approval: Q-046 option 1 (Riyadh 2026-10-10 21:05). No gate is loosened; all numbers come from
`config/strategy_lab.yaml` unchanged. Demo only; no live trading.

## Why

T-130 finished 3 runs x 222 attempts (60m and 15m on full history, 5m with key-level seeds) with
0 Gate 1 passes. The best NAS100 ideas are long trend ideas (OOS profit factor 1.1 to 1.24, p-values
near 0.8), which mostly ride the 2010-2026 bull market. Gold shows no edge after costs with ideas
copied from NAS100. NAS100 and gold are different markets and get their own ideas.

## Steps

1. **Round A (this phase).** `instrument_seed_specs` adds 16 NAS100 ideas (US-session trend pullback,
   US-session momentum break) and 20 gold ideas (Asian-session range fade, London-open break of the
   overnight range, US-session momentum break). T-131 runs them on 5m, 15m and 60m (15m and 60m on full
   history), both instruments, separate result files, reported per instrument.
2. **Round B (only if Round A has no pass).** Add the next instrument-specific families chosen from
   Round A evidence (for example gold reaction to scheduled US data via a time-of-day condition, NAS100
   gap and overnight-range ideas) and the 3m timeframe. One run set, same reporting.
3. **Cost calibration.** Use the recorder (B-007) to measure real Capital.com spreads, then rerun the
   best candidates with measured costs. Never use measured costs to loosen anything; only to replace the
   assumed 4.0 (NAS100) and 1.6 (gold) per-trade costs if the measurement supports it.
4. **Incubation (Gate 2 forward test).** The best near-misses (OOS profit factor at least 1.15, at
   least 300 trades, profitable walk-forward share at least 0.60 on at least one instrument) may run on
   the demo account as forward tests, clearly labelled "failed Gate 1, incubation only", with fixed
   small size and the existing risk limits. They cannot be promoted to Gate 3 or live. Owner approval
   is required before any incubation starts.

## Stop rule

If Round A and Round B both end with 0 Gate 1 passes, the search for Gate 1 ideas stops. Claude then
reports to the owner and the owner decides between: running the incubation of the best near-misses only,
widening the data (other instruments), or pausing the strategy search. No further search rounds are
started without that decision. A single pass is not accepted without the multiple-testing correction
counting all attempts of all rounds together.

## Reporting rules (to avoid misreading)

- Per instrument and per timeframe, never pooled.
- `null`, not 0, for missing data. Detail is written once, in the single log entry.
- `holdout_profit_factor` for an attempt that failed the preliminary screen equals the out-of-sample
  value by design: the sealed holdout is opened only by attempts that pass the preliminary gates
  (`gate1_metrics` falls back to out-of-sample). It is not independent evidence unless the attempt
  reached the holdout stage.
