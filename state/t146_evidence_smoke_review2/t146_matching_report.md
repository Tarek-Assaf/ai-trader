# T-146 movement matching report

Diagnostic only. No broker, orders, paper trading, holdout, Gate 1, ledger or promotion activity.

Generated: `2026-10-16T20:42:29.831231+00:00`
In-sample: `2024-10-01T00:00:00+00:00` to `2025-01-01T00:00:00+00:00`
Out-of-sample: `2025-01-01T00:00:00+00:00` to `2025-03-01T00:00:00+00:00`

Each result uses past-only ATR-normalised windows, DTW and a correlation baseline, with a random control matched for count and time spread.

## Groups

- **NSXUSD 1m**: 19720 in-sample windows, 12824 OOS queries, 6 evaluated under the query cap, 12 result rows in 21.149s; charts: `/workspace/state/t146_evidence_smoke_review2/t146_charts/nsxusd_1m`
  Multiple-testing verdicts: NOT_FOUND; query stride=3841; Bonferroni alpha is recorded per row.

A movement is a candidate only when its out-of-sample net-of-cost result beats the matched random control after the pre-registered trial correction. This diagnostic does not promote a strategy.
