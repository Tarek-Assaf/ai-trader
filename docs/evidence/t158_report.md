# T-158 Pipeline audit

## Owner verdict

The audit **fails Stage 1 time alignment**: the recorded-vs-owner series has poor agreement at zero shift (27 comparable points, agreement 0.3704, correlation -0.2352), while the best tested shift is **+240 minutes** (29 comparable points, agreement 0.8621, correlation 0.7448). Stage 2 bar building and Stage 3 indicator maths match independent references exactly; Stage 4 replay controls and Stage 5 engine arithmetic pass their known answers. The first concrete fault to fix is timestamp/session alignment. A separate reviewed task should correct that plumbing; this diagnostic task applies no fix or tuning.

## Stage results

- **Stage 1 — FAIL.** Tested shifts from -12h to +12h in 30-minute steps. Zero offset: agreement 0.3704, correlation -0.2352. Best: +240 minutes, agreement 0.8621, correlation 0.7448.
- **Stage 2 — PASS.** NSXUSD and XAUUSD production resampling matches independent aggregation at 1m, 3m, 5m, 54m and 60m. No mismatches or missing bars; 54m grouping is verified as 18 x 3m.
- **Stage 3 — PASS.** EMA200, ATR14, ADX14 and DI references match production for both instruments with maximum absolute error 0.0 and no missing values. Heikin Ashi warmup is included.
- **Stage 4 — PASS.** 31 owner entry groups classify as 2 new signals and 29 refreshes. Heikin Ashi categories: 8 current-bar flips, 12 opposite-colour, 11 same-colour/no-flip. Positive control confirms the production `evaluate_entries` path and both break rules.
- **Stage 5 — PASS.** The ten-trade hand ledger matches production (gross 20.0, costs 10.0). The lookahead control reaches 100% wins, the coin-flip control loses net 5.0 after costs, and buy-and-hold differs by 0.0 from its arithmetic expectation.

## Safety and scope

Diagnostic only. The run made 0 broker calls, placed 0 orders, read no sealed holdout, evaluated no gate, wrote no lake data, and made no promotion or ledger writes. The script and focused known-answer tests are included for reproducibility.

## Proposed fix

Audit and correct the owner/replay timestamp and session alignment in a separate reviewed task. No correction is included in T-158.
