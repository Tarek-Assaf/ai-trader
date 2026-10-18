# T-154 diagnostic report

## Scope

This cycle addressed the review requests on the movement-matching rerun. The matcher now precomputes one normalized NumPy library-window matrix per fixed window length and compares each query against that matrix in vectorized batches. DTW keeps the same formulas and applies the existing correlation prefilter before scalar DTW scoring. A scalar fallback remains for environments without NumPy. The unit test compares indexed and scalar correlation and DTW scores.

The Pod rerun used the full configured query set (`max_queries=20000`) and a 1,200-second limit for each group. It did not shrink the query set. Evidence is in `t154_review_cycle2_rerun.json`.

## Rerun results

* **XAUUSD 60m:** 1,458 library windows, 918 OOS queries, and all 918 queries evaluated in 226.409 seconds. The 18 result rows were `NOT_FOUND`, with 360 trials per result (6,480 total).
* **NSXUSD 60m:** 0 windows after 1,200.3 seconds, so matching stopped before a verdict. The source still has same-day gaps, including 11:00-13:00 and 20:00-23:00 UTC.
* **NSXUSD 5m:** 19,408 OOS queries; 5,171 evaluated before the 1,200.1-second bound. Partial, no final verdict or trial count.
* **XAUUSD 5m:** 20,877 OOS queries; 4,141 evaluated before the 1,200.1-second bound. Partial, no final verdict or trial count.
* **NSXUSD 3m:** 24,617 OOS queries; 2,871 evaluated before the 1,200.1-second bound. Partial, no final verdict or trial count.
* **XAUUSD 3m:** 26,207 OOS queries; 2,445 evaluated before the 1,200.1-second bound. Partial, no final verdict or trial count.
* **NSXUSD 1m:** 48,906 OOS queries; 4,795 evaluated before the 1,200.1-second bound. Partial, no final verdict or trial count.
* **XAUUSD 1m:** 51,264 OOS queries; 4,289 evaluated before the 1,200.1-second bound. Partial, no final verdict or trial count.

The previous `918 OOS, queries_evaluated 2` value was caused by the prior rerun's `max_queries=2` bound. This cycle evaluated all 918 XAUUSD 60m OOS queries, so the mismatch is resolved.

## NSXUSD 60m source gap

The canonical NSXUSD lake exists and the legacy aliases are supported only when the canonical directory is empty. The source inventory shows same-day missing hours. Resampling therefore omits incomplete bins and the strict continuity rule rejects windows that cross unknown gaps. No data was filled or bridged, and the 60m group correctly stopped without a matching verdict.

## Safety and validation

The Pod process was read-only: broker calls, orders, holdout reads, gate evaluation, cost edits, ledger writes, and promotion writes were all false or zero. No broker or trading side effects occurred. The reviewer must validate this cycle; no own pass validation is claimed. Desktop checks available on this workspace are compile, diff and synthetic benchmark checks; pytest, Ruff and mypy are unavailable locally.

## Deterministic sample fallback

The full-query run could not finish the six smaller groups within the per-group bound, so the fallback was declared before execution. A deterministic sample request of 3,000 OOS queries per group was run for NSXUSD/XAUUSD at 5m, 3m and 1m with the same 360 trials per result. All six groups again reached the 1,200-second bound before emitting a final verdict or trial count: runner `queries_done` was 14,178 (NSXUSD 5m), 11,367 (XAUUSD 5m), 9,576 (NSXUSD 3m), 8,397 (XAUUSD 3m), 13,644 (NSXUSD 1m) and 11,994 (XAUUSD 1m). These are checkpoint counters; no final OOS count or trial result was emitted. The sample therefore does not satisfy the acceptance requirement. The redacted checkpoint record is in `t154_review_cycle2_sample.json`.
