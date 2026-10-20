# T-206: Watch the first Tai demo session (owner enabled demo orders 2026-10-20 ~15:35 Riyadh)

## Why
The owner set TAI_DEMO_DRY_RUN=false on the Pod (image bfb1193). Orders now go to the Capital.com DEMO account.
We need early proof that the machinery behaves and a trade-by-trade comparison with what the owner's TradingView
indicator does on the same bars.

## Do (read-only on the Pod, no config or code edits, no orders of your own)
1. Confirm the startup line (`Tai demo startup experiment=on dry_run=off ...`) and that /workspace/state/tai_demo_positions.json exists.
2. List every Tai journal event since enabling: entries (time, direction, size, entry, stop, targets), refusals (reason counts, incl.
   spread_too_wide), exits (reason), alerts sent, errors. Flag anything unexpected: two open positions, an order without stop/targets,
   a refusal storm, an exit error, state-file mismatch with the broker.
3. For each entry compare with the port replay on the same bars (scripts/lab_t195_runtime_miss.py style): does the port also enter at that bar?
   Report matched/extra/missing and the first differing quantity for any difference.
4. Report realised demo P&L in R and money, and the spread paid per entry vs the 0.25R guard. State plainly if there are too few trades to judge.

## Rules
Dry-run flag, risk.yaml, gate files, strategy code: untouched. No secrets in output. If a safety fault is found (orders without protection,
duplicate positions), say so first in the log entry and set a blocker; Claude relays to the owner.

## Acceptance
`docs/evidence/t206/report.md` + `summary.json` (append a dated section per session). One log entry, turn to claude, stop.
