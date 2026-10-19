# T-180: Build and dry-check the historical Tai port harness (no history run)

## Why
T-179 (V-445): the port's signals are stable under bar noise (86.5% reappear at the same bar at
sd 1 point; the owner/CFD basis of 221 points is far larger than every registered sd, so noise
alone is not a test of the basis). Fourteen-plus bar-matching diagnostics are negative and the
25/31 gate is not relaxed (D-060). The historical rerun waits on Q-071. So that the rerun can
start the moment the owner answers, this task prepares everything for it without running it.

## Task (build + tiny dry check only)
1. `scripts/lab_t180_hist_tai.py`: runs the unchanged T-162 port (do not edit it) over a given
   instrument, timeframe (1m, 3m, 5m, 60m) and date window, reading bars only from the lake
   training range, with full warm-up. Output: trade list (entry, exit, side, R) using the port's
   own management (target-first, management from the entry bar), with the real spread/cost model
   used by the Strategy Lab, per-year summary, and a label field
   `"port of the stored indicator, loosely matched to the owner (3/31 within 3 bars, 15/31
   within 30 bars)"` in every output.
2. The script must refuse any window that touches the sealed holdout (hard error before reading
   data) and must have no broker, order, secret or lake-write code path.
3. Dry check only: run it on NSXUSD 1m, 3m, 5m, 60m and XAUUSD 1m, 3m, 5m, 60m for the owner
   window 2026-10-06 .. 2026-10-16 (or the available part; XAUUSD may have no rows, report that
   plainly). Report row counts, signal counts, trade counts per cell. Do NOT run longer history.
4. Tests: `tests/unit/test_t180_hist_tai.py` (holdout refusal, label present, determinism on tiny
   synthetic bars).

## Verdict first
"Harness ready yes/no; cells run: n/8; holdout guard tested yes/no; longer history NOT run."

## Rules
No broker calls, orders, sealed holdout reads, lake or production writes, tuning, secrets. Lint
with ruff check and ruff format (line length 100; no per-line noqa). Deliverables:
`docs/evidence/t180_report.md`, `docs/evidence/t180_dry_check.json`, the script and test. One log
entry, hand the turn to claude, stop.
